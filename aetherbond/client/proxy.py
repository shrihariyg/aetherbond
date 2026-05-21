import asyncio
import socket
import struct
import logging
from typing import Tuple, Optional
from aetherbond.client.scheduler import Scheduler
from aetherbond.common.simulator import simulator_registry

logger = logging.getLogger("aetherbond.proxy")

class Socks5Proxy:
    def __init__(self, host: str, port: int, scheduler: Scheduler):
        self.host = host
        self.port = port
        self.scheduler = scheduler
        self.server: Optional[asyncio.Server] = None
        self.is_running = False

    async def start(self):
        self.is_running = True
        self.server = await asyncio.start_server(
            self._handle_client, self.host, self.port
        )
        logger.info(f"SOCKS5 Multipath Proxy running on {self.host}:{self.port}")
        
        async with self.server:
            await self.server.serve_forever()

    async def stop(self):
        self.is_running = False
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            logger.info("SOCKS5 Proxy stopped.")

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        """
        Handles incoming SOCKS5 connections and relays them through dynamic interfaces.
        """
        client_addr = writer.get_extra_info('peername')
        try:
            # 1. SOCKS5 Handshake
            # Read version and number of methods
            header = await reader.readexactly(2)
            version, nmethods = struct.unpack("!BB", header)
            if version != 5:
                writer.close()
                return

            # Read supported authentication methods
            await reader.readexactly(nmethods)
            
            # Respond with NO AUTHENTICATION REQUIRED (0x00)
            writer.write(struct.pack("!BB", 5, 0))
            await writer.drain()

            # 2. SOCKS5 Request
            request_header = await reader.readexactly(4)
            version, cmd, rsv, atyp = struct.unpack("!BBBB", request_header)
            
            if cmd != 1:  # 1 = CONNECT command
                # Reply command not supported (0x07)
                self._send_reply(writer, 7)
                writer.close()
                return

            # Read Destination Address
            if atyp == 1:  # IPv4
                addr_bytes = await reader.readexactly(4)
                dst_addr = socket.inet_ntoa(addr_bytes)
            elif atyp == 3:  # Domain name
                domain_length_bytes = await reader.readexactly(1)
                domain_length = domain_length_bytes[0]
                domain_bytes = await reader.readexactly(domain_length)
                dst_addr = domain_bytes.decode('utf-8')
            elif atyp == 4:  # IPv6
                # Reply Address type not supported (0x08)
                self._send_reply(writer, 8)
                writer.close()
                return
            else:
                self._send_reply(writer, 8)
                writer.close()
                return

            # Read Destination Port
            port_bytes = await reader.readexactly(2)
            dst_port = struct.unpack("!H", port_bytes)[0]

            logger.info(f"SOCKS5 intercept: {client_addr} requests connection to {dst_addr}:{dst_port}")

            # 3. Dynamic Path Steering and Socket Binding
            # Steer connections dynamically using the Scheduler
            try:
                # Resolve destination IP if domain
                # Perform resolution on lowest-latency link (latency-aware DNS)
                dns_link_ip = self.scheduler.select_interface_lowest_latency()
                
                # Resolve IP (blocking operation wrapped in executor)
                loop = asyncio.get_running_loop()
                resolved_ips = await loop.run_in_executor(None, socket.getaddrinfo, dst_addr, dst_port)
                resolved_ip = resolved_ips[0][4][0]
                
            except Exception as e:
                logger.error(f"DNS Resolution failed for {dst_addr}: {e}")
                self._send_reply(writer, 4)  # Host unreachable
                writer.close()
                return

            # Choose interface for the main TCP session
            # Use Weighted Round Robin to balance concurrent connections across links!
            outgoing_interface_ip = self.scheduler.select_interface_wrr()
            
            # Check if using the simulation layer
            metric = self.scheduler.monitor.get_metrics(outgoing_interface_ip)
            is_sim = metric.is_simulated if metric else False

            remote_reader = None
            remote_writer = None

            if is_sim:
                # SIMULATED MODE proxy routing
                logger.info(f"Proxy connecting to {dst_addr}:{dst_port} simulated via interface {outgoing_interface_ip}")
                
                # Create TCP session to target host
                remote_reader, remote_writer = await asyncio.open_connection(resolved_ip, dst_port)
                
                # Wrap communication with simulator latency and speed constraints
                # For Phase 2 prototype, we connect natively but apply simulation parameters to the relay
                
            else:
                # PHYSICAL MODE proxy routing: Bind raw socket to selected interface IP
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.setblocking(False)
                
                try:
                    # Bind outgoing socket to the interface local IP
                    sock.bind((outgoing_interface_ip, 0))
                    
                    # Connect socket asynchronously
                    await loop.sock_connect(sock, (resolved_ip, dst_port))
                    
                    # Convert raw socket to asyncio Stream Reader/Writer
                    remote_reader, remote_writer = await asyncio.open_connection(sock=sock)
                    logger.info(f"Proxy established physical connection to {dst_addr}:{dst_port} bound to {outgoing_interface_ip}")
                    
                except Exception as e:
                    logger.error(f"Failed to connect through physical interface {outgoing_interface_ip}: {e}")
                    self._send_reply(writer, 3)  # Network unreachable
                    sock.close()
                    writer.close()
                    return

            # Send SOCKS5 Success response
            self._send_reply(writer, 0)
            
            # 4. Bidirectional Relay Pipe
            # Start concurrent forward and backward relays
            await asyncio.gather(
                self._relay_stream(reader, remote_writer, outgoing_interface_ip, is_sim, "uplink"),
                self._relay_stream(remote_reader, writer, outgoing_interface_ip, is_sim, "downlink")
            )

        except Exception as e:
            logger.error(f"Error handling SOCKS5 client {client_addr}: {e}")
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    def _send_reply(self, writer: asyncio.StreamWriter, status: int):
        # Header: Version (5), Status, Reserved (0), ATYP (1 - IPv4), Bind Addr (0.0.0.0), Bind Port (0)
        reply = struct.pack("!BBBB4sH", 5, status, 0, 1, b"\x00\x00\x00\x00", 0)
        writer.write(reply)
        # Note: Do not wait for drain inside this synchronous helper

    async def _relay_stream(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, 
                            interface_ip: str, is_sim: bool, direction: str):
        """
        Pipes data from one stream to another.
        If in simulator mode, applies bandwidth and latency constraints to data transfer.
        """
        buffer_size = 32 * 1024  # 32KB buffer
        try:
            while self.is_running:
                data = await reader.read(buffer_size)
                if not data:
                    break

                if is_sim:
                    # SIMULATOR PATH:
                    # 1. Apply simulation latency
                    await simulator_registry.simulate_delay(interface_ip)
                    
                    # 2. Throttling throughput based on the interface bandwidth cap
                    await simulator_registry.throttle_stream(interface_ip, len(data))

                writer.write(data)
                await writer.drain()
                
        except Exception as e:
            logger.error(f"Relay error in {direction}: {e}")
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
