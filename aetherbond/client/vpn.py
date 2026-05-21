import asyncio
import struct
import logging
import socket
from typing import Dict, Tuple, Optional
from aetherbond.common.protocol import AetherProtocol
from aetherbond.client.scheduler import Scheduler
from aetherbond.server.receiver import ResequencingBuffer

logger = logging.getLogger("aetherbond.client.vpn")

class VPNClientSession:
    def __init__(self, session_id: int, writer: asyncio.StreamWriter):
        self.session_id = session_id
        self.writer = writer
        self.active = True

    async def write(self, data: bytes):
        if not self.active:
            return
        try:
            self.writer.write(data)
            await self.writer.drain()
        except Exception as e:
            logger.error(f"Error writing to client local socket for session {self.session_id}: {e}")
            self.active = False

    def close(self):
        self.active = False
        try:
            self.writer.close()
        except Exception:
            pass


class AetherVPNClient:
    """
    Client-side VPN Bonding Tunnel coordinator.
    Intercepts local traffic (via user-space SOCKS5 or direct TCP listeners),
    encapsulates & encrypts it using AetherProtocol, stripes it across multiple
    interfaces using Weighted Round Robin, and interacts with the VPS Aggregator Server.
    """
    def __init__(self, server_host: str, server_port: int, secret_key: str, scheduler: Scheduler):
        self.server_host = server_host
        self.server_port = server_port
        self.scheduler = scheduler
        self.protocol = AetherProtocol(secret_key.encode())
        
        self.sessions: Dict[int, VPNClientSession] = {}
        self.active_sockets: Dict[str, socket.socket] = {}
        
        self.next_seq_out = 0
        self.next_session_id = 1
        self._lock = asyncio.Lock()
        self.is_running = False
        
        # Resequencer for incoming packets from the VPS Server
        self.resequencer = ResequencingBuffer(callback=self._handle_reordered_server_packet)

    async def start(self, local_proxy_port: int = 1085):
        """
        Starts the client SOCKS5 VPN ingress proxy and boots the background UDP tunnel.
        """
        self.is_running = True
        
        # 1. Open local UDP sockets for every interface in the scheduler registry
        await self._init_multipath_sockets()
        
        # 2. Boot background task to listen for returning traffic from VPS on all sockets
        for interface_ip, sock in self.active_sockets.items():
            asyncio.create_task(self._receive_loop(interface_ip, sock))
            
        # 3. Start local user-space TUN ingress proxy (SOCKS5 format)
        self.local_server = await asyncio.start_server(
            self._handle_local_client, "127.0.0.1", local_proxy_port
        )
        logger.info(f"AetherBond User-Space TUN/VPN Client running on 127.0.0.1:{local_proxy_port}")
        logger.info(f"Multipath lanes active: {list(self.active_sockets.keys())}")

    async def stop(self):
        self.is_running = False
        if hasattr(self, 'local_server'):
            self.local_server.close()
            await self.local_server.wait_closed()
            
        async with self._lock:
            for session in list(self.sessions.values()):
                session.close()
            self.sessions.clear()
            
            for sock in self.active_sockets.values():
                sock.close()
            self.active_sockets.clear()
            
        logger.info("AetherVPNClient stopped successfully.")

    async def _init_multipath_sockets(self):
        """Creates bound UDP sockets for every configured client interface."""
        interfaces = list(self.scheduler.monitor.metrics.keys())
        for ip in interfaces:
            try:
                # Create UDP socket
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.setblocking(False)
                
                # Bind socket to interface IP (forces outbound traffic through this interface)
                # For simulated interfaces, we just bind to local or any, but label it
                metric = self.scheduler.monitor.metrics.get(ip)
                bind_ip = "0.0.0.0" if metric and metric.is_simulated else ip
                
                sock.bind((bind_ip, 0))
                self.active_sockets[ip] = sock
                logger.info(f"Bound multi-path tunnel socket on interface {ip} to port {sock.getsockname()[1]}")
            except Exception as e:
                logger.error(f"Failed to bind socket on interface {ip}: {e}")

    async def _send_to_vps(self, payload: bytes, path_id: int, interface_ip: str):
        """Sends encrypted packet to VPS aggregator over the chosen physical/simulated link."""
        sock = self.active_sockets.get(interface_ip)
        if not sock:
            logger.error(f"No socket available for interface {interface_ip}")
            return
            
        async with self._lock:
            seq = self.next_seq_out
            self.next_seq_out += 1
            
        # Encrypt packet via AetherProtocol
        packet_bytes = self.protocol.encrypt_and_pack(payload, seq, path_id)
        
        # Send packet
        try:
            loop = asyncio.get_running_loop()
            await loop.sock_sendto(sock, packet_bytes, (self.server_host, self.server_port))
        except Exception as e:
            logger.error(f"Error striping packet on path {path_id} ({interface_ip}): {e}")

    async def _receive_loop(self, interface_ip: str, sock: socket.socket):
        """Listens for returned packets from the VPS on specific link socket."""
        loop = asyncio.get_running_loop()
        while self.is_running:
            try:
                data, addr = await loop.sock_recvfrom(sock, 65535)
                # Process datagram through protocol decrypt
                try:
                    payload, seq, path_id, latency_ms = self.protocol.unpack_and_decrypt(data)
                    # Push server packet to client-side resequencer
                    await self.resequencer.put(seq, payload)
                except Exception as ex:
                    logger.debug(f"Failed to decrypt server packet: {ex}")
            except OSError:
                break
            except Exception as e:
                logger.error(f"Receiver error on {interface_ip}: {e}")
                await asyncio.sleep(0.1)

    def _handle_reordered_server_packet(self, payload: bytes):
        """Callback from client ResequencingBuffer once server response packet is ordered."""
        if len(payload) < 5:
            return
            
        status = payload[0]
        session_id, = struct.unpack("!I", payload[1:5])
        body = payload[5:]
        
        session = self.sessions.get(session_id)
        if not session:
            return
            
        if status == 2: # DATA
            asyncio.create_task(session.write(body))
        elif status == 3: # CLOSE
            logger.info(f"VPS requested session {session_id} termination.")
            session.close()
            self.sessions.pop(session_id, None)

    async def _handle_local_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """Handles local SOCKS5 requests, mapping them to server multiplexed tunnel sessions."""
        client_addr = writer.get_extra_info('peername')
        session_id = 0
        try:
            # 1. Handshake
            header = await reader.readexactly(2)
            version, nmethods = struct.unpack("!BB", header)
            if version != 5:
                writer.close()
                return
            await reader.readexactly(nmethods)
            writer.write(struct.pack("!BB", 5, 0))
            await writer.drain()

            # 2. Connect request
            request_header = await reader.readexactly(4)
            version, cmd, rsv, atyp = struct.unpack("!BBBB", request_header)
            
            if cmd != 1:  # Connect
                writer.write(struct.pack("!BBBB4sH", 5, 7, 0, 1, b"\x00\x00\x00\x00", 0))
                writer.close()
                return

            if atyp == 1:
                addr_bytes = await reader.readexactly(4)
                dst_addr = socket.inet_ntoa(addr_bytes)
            elif atyp == 3:
                length = (await reader.readexactly(1))[0]
                dst_addr = (await reader.readexactly(length)).decode('utf-8')
            else:
                writer.write(struct.pack("!BBBB4sH", 5, 8, 0, 1, b"\x00\x00\x00\x00", 0))
                writer.close()
                return

            port_bytes = await reader.readexactly(2)
            dst_port = struct.unpack("!H", port_bytes)[0]
            
            async with self._lock:
                session_id = self.next_session_id
                self.next_session_id += 1
                session = VPNClientSession(session_id, writer)
                self.sessions[session_id] = session

            logger.info(f"TUN Tunneling new session {session_id} for target {dst_addr}:{dst_port}")

            # Send SOCKS5 success reply immediately to intercept the traffic stream
            writer.write(struct.pack("!BBBB4sH", 5, 0, 0, 1, b"\x00\x00\x00\x00", 0))
            await writer.drain()

            # 3. Issue CONNECT Command to VPS over the chosen physical/simulated link
            # Select first link for the connect command
            interface_ip = self.scheduler.select_interface_wrr()
            path_id = list(self.active_sockets.keys()).index(interface_ip)
            
            connect_payload = (
                bytes([1]) + 
                struct.pack("!I", session_id) + 
                bytes([1]) + # Protocol: TCP
                struct.pack("!H", dst_port) + 
                dst_addr.encode()
            )
            await self._send_to_vps(connect_payload, path_id, interface_ip)

            # 4. Stream data relay loop
            while session.active:
                data = await reader.read(4096)
                if not data:
                    break
                
                # Stripe chunks across paths dynamically using the Scheduler weights!
                interface_ip = self.scheduler.select_interface_wrr()
                path_id = list(self.active_sockets.keys()).index(interface_ip)
                
                # Apply simulation latency/bandwidth cap check
                metric = self.scheduler.monitor.get_metrics(interface_ip)
                if metric and metric.is_simulated:
                    from aetherbond.common.simulator import simulator_registry
                    await simulator_registry.simulate_delay(interface_ip)
                    await simulator_registry.throttle_stream(interface_ip, len(data))
                
                data_payload = bytes([2]) + struct.pack("!I", session_id) + data
                await self._send_to_vps(data_payload, path_id, interface_ip)

        except Exception as e:
            logger.error(f"Error handling tunnel client stream on session {session_id}: {e}")
        finally:
            # Send CLOSE command to VPS
            if session_id > 0:
                interface_ip = self.scheduler.select_interface_wrr()
                path_id = list(self.active_sockets.keys()).index(interface_ip)
                close_payload = bytes([3]) + struct.pack("!I", session_id)
                await self._send_to_vps(close_payload, path_id, interface_ip)
                
                async with self._lock:
                    self.sessions.pop(session_id, None)
            writer.close()
