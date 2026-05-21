import asyncio
import logging
import socket
from typing import Dict, Tuple, Optional
from aetherbond.common.protocol import AetherProtocol
from aetherbond.server.receiver import ResequencingBuffer

logger = logging.getLogger("aetherbond.server.router")

class UserSpaceSession:
    """
    Manages an active user-space transport session relayed by the server.
    Avoids requiring root/TUN drivers for cross-platform and simulation modes.
    """
    def __init__(self, session_id: int, dest_ip: str, dest_port: int, protocol: str, on_data_received):
        self.session_id = session_id
        self.dest_ip = dest_ip
        self.dest_port = dest_port
        self.protocol = protocol
        self.on_data_received = on_data_received
        
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self.udp_sock: Optional[socket.socket] = None
        self.active = False
        self._relay_task: Optional[asyncio.Task] = None

    async def connect(self) -> bool:
        try:
            if self.protocol == "TCP":
                self.reader, self.writer = await asyncio.open_connection(self.dest_ip, self.dest_port)
                self.active = True
                self._relay_task = asyncio.create_task(self._relay_loop())
                logger.info(f"Relay TCP connection established to {self.dest_ip}:{self.dest_port} for session {self.session_id}")
                return True
            elif self.protocol == "UDP":
                self.udp_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self.udp_sock.setblocking(False)
                self.active = True
                self._relay_task = asyncio.create_task(self._relay_udp_loop())
                logger.info(f"Relay UDP socket created to {self.dest_ip}:{self.dest_port} for session {self.session_id}")
                return True
        except Exception as e:
            logger.error(f"Failed to connect to target {self.dest_ip}:{self.dest_port}: {e}")
        return False

    async def send(self, data: bytes):
        if not self.active:
            return
        try:
            if self.protocol == "TCP" and self.writer:
                self.writer.write(data)
                await self.writer.drain()
            elif self.protocol == "UDP" and self.udp_sock:
                loop = asyncio.get_running_loop()
                await loop.sock_sendto(self.udp_sock, data, (self.dest_ip, self.dest_port))
        except Exception as e:
            logger.error(f"Error sending data to target on session {self.session_id}: {e}")
            await self.close()

    async def _relay_loop(self):
        try:
            while self.active and self.reader:
                data = await self.reader.read(4096)
                if not data:
                    break
                await self.on_data_received(self.session_id, data)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Relay loop error on session {self.session_id}: {e}")
        finally:
            await self.close()

    async def _relay_udp_loop(self):
        try:
            loop = asyncio.get_running_loop()
            while self.active and self.udp_sock:
                data, addr = await loop.sock_recvfrom(self.udp_sock, 4096)
                await self.on_data_received(self.session_id, data)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Relay UDP loop error on session {self.session_id}: {e}")
        finally:
            await self.close()

    async def close(self):
        if not self.active:
            return
        self.active = False
        if self._relay_task:
            self._relay_task.cancel()
        if self.writer:
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass
        if self.udp_sock:
            try:
                self.udp_sock.close()
            except Exception:
                pass
        logger.info(f"Session {self.session_id} closed")


class AetherRouter:
    """
    Bridges packets from AetherProtocol to the real network and returns response data.
    Supports user-space virtual session relays and raw tun forwarding.
    """
    def __init__(self, protocol: AetherProtocol, response_sender):
        """
        - protocol: Shared AetherProtocol instance.
        - response_sender: Async function to send encrypted packets back to specific client address paths.
                           Signature: response_sender(packet_bytes: bytes, client_addr: Tuple[str, int], path_id: int)
        """
        self.protocol = protocol
        self.response_sender = response_sender
        self.sessions: Dict[int, UserSpaceSession] = {}
        
        # Resequencer to ensure client packets are ordered correctly
        self.resequencer = ResequencingBuffer(callback=self._handle_reordered_packet)
        self.next_seq_out = 0
        self._lock = asyncio.Lock()

    async def handle_client_packet(self, packet_bytes: bytes, client_addr: Tuple[str, int]):
        """
        Receives raw encrypted UDP packets from multiple client paths, decrypts,
        and pushes to the resequencing buffer.
        """
        try:
            payload, seq, path_id, latency_ms = self.protocol.unpack_and_decrypt(packet_bytes)
            # Store client path context to know how to route replies back
            # Push to resequencing heap with client routing info attached
            await self.resequencer.put(seq, (payload, client_addr, path_id))
        except Exception as e:
            logger.error(f"Failed to process client packet: {e}")

    def _handle_reordered_packet(self, packet_info: Tuple[bytes, Tuple[str, int], int]):
        """
        Callback from ResequencingBuffer once packets are strictly ordered.
        Spawns async processor.
        """
        payload, client_addr, path_id = packet_info
        asyncio.create_task(self._process_payload(payload, client_addr, path_id))

    async def _process_payload(self, payload: bytes, client_addr: Tuple[str, int], path_id: int):
        """
        Processes decrypted, reordered user payload.
        User-space protocol payload format:
        - 1 byte: Command (1=Connect, 2=Data, 3=Close)
        - 4 bytes: Session ID (I)
        - Command-specific body
        """
        if len(payload) < 5:
            return
            
        cmd = payload[0]
        import struct
        session_id, = struct.unpack("!I", payload[1:5])
        body = payload[5:]
        
        if cmd == 1: # CONNECT
            # Body: Protocol (1 byte, 1=TCP, 2=UDP) + Port (2 bytes, H) + IP (string)
            if len(body) < 4:
                return
            proto_val = body[0]
            proto = "TCP" if proto_val == 1 else "UDP"
            port, = struct.unpack("!H", body[1:3])
            dest_ip = body[3:].decode('utf-8', errors='ignore')
            
            async with self._lock:
                if session_id in self.sessions:
                    await self.sessions[session_id].close()
                
                session = UserSpaceSession(
                    session_id=session_id,
                    dest_ip=dest_ip,
                    dest_port=port,
                    protocol=proto,
                    on_data_received=lambda sid, data: self._send_back_to_client(sid, data, client_addr, path_id)
                )
                self.sessions[session_id] = session
                connected = await session.connect()
                if not connected:
                    # Notify client of failure
                    await self._send_back_to_client(session_id, b"", client_addr, path_id, is_close=True)
                    del self.sessions[session_id]
                    
        elif cmd == 2: # DATA
            session = self.sessions.get(session_id)
            if session:
                await session.send(body)
                
        elif cmd == 3: # CLOSE
            async with self._lock:
                session = self.sessions.pop(session_id, None)
                if session:
                    await session.close()

    async def _send_back_to_client(self, session_id: int, data: bytes, client_addr: Tuple[str, int], path_id: int, is_close: bool = False):
        """
        Encrypts response payload and sends it back over the designated client path.
        Response Format:
        - 1 byte: Status (2=Data, 3=Close)
        - 4 bytes: Session ID (I)
        - Body: Response data
        """
        import struct
        cmd = 3 if is_close else 2
        payload = bytes([cmd]) + struct.pack("!I", session_id) + data
        
        async with self._lock:
            seq = self.next_seq_out
            self.next_seq_out += 1
            
        # Encrypt packet
        packet_bytes = self.protocol.encrypt_and_pack(payload, seq, path_id)
        
        # Send via response sender callback
        await self.response_sender(packet_bytes, client_addr, path_id)

    async def cleanup(self):
        """Closes all active sessions."""
        async with self._lock:
            for session in list(self.sessions.values()):
                await session.close()
            self.sessions.clear()
