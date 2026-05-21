import struct
import time
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

# Custom binary header format:
# - Sequence Number: 32-bit unsigned integer (I)
# - Timestamp: 64-bit unsigned integer in microseconds (Q)
# - Path ID: 32-bit unsigned integer (I)
# Total header size: 4 + 8 + 4 = 16 bytes
HEADER_FORMAT = "!IQI"
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

class AetherProtocol:
    def __init__(self, secret_key: bytes):
        """
        Initializes the AetherProtocol with a 32-byte pre-shared symmetric key.
        """
        if len(secret_key) != 32:
            # Derive or pad to 32 bytes
            self.key = secret_key.ljust(32, b'\x00')[:32]
        else:
            self.key = secret_key
            
        self.cipher = ChaCha20Poly1305(self.key)

    def encrypt_and_pack(self, payload: bytes, seq: int, path_id: int) -> bytes:
        """
        Encapsulates and encrypts an IP packet (L3).
        """
        # Get current time in microseconds
        timestamp_us = int(time.time() * 1_000_000)
        
        # Build binary header
        header = struct.pack(HEADER_FORMAT, seq, timestamp_us, path_id)
        
        # Encrypt the payload
        # Use sequence number + timestamp as a unique 12-byte nonce for ChaCha20-Poly1305
        # 4 bytes (seq) + 8 bytes (timestamp) = 12 bytes nonce!
        nonce = struct.pack("!IQ", seq, timestamp_us)
        
        # Encrypt payload and authenticate header as associated data (AAD)
        encrypted_payload = self.cipher.encrypt(nonce, payload, header)
        
        # Return combined header + encrypted payload
        return header + encrypted_payload

    def unpack_and_decrypt(self, packet_bytes: bytes) -> tuple[bytes, int, int, float]:
        """
        Unpacks and decrypts an encapsulated packet.
        Returns: (decrypted_payload, seq, path_id, rtt_ms)
        """
        if len(packet_bytes) < HEADER_SIZE:
            raise ValueError("Packet is too small to contain AetherBond header.")
            
        # Extract header
        header = packet_bytes[:HEADER_SIZE]
        encrypted_payload = packet_bytes[HEADER_SIZE:]
        
        seq, timestamp_us, path_id = struct.unpack(HEADER_FORMAT, header)
        
        # Reconstruct the 12-byte nonce
        nonce = struct.pack("!IQ", seq, timestamp_us)
        
        # Decrypt payload and verify integrity
        decrypted_payload = self.cipher.decrypt(nonce, encrypted_payload, header)
        
        # Calculate transient transit latency
        now_us = int(time.time() * 1_000_000)
        latency_ms = (now_us - timestamp_us) / 1000.0
        
        return decrypted_payload, seq, path_id, latency_ms
