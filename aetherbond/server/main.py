import asyncio
import argparse
import logging
import sys
from typing import Tuple, Dict
from aetherbond.common.protocol import AetherProtocol
from aetherbond.server.router import AetherRouter

# Set up logging to stdout with a neat clean format
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("aetherbond.server.main")

class AetherServerProtocol(asyncio.DatagramProtocol):
    def __init__(self, secret: str):
        self.secret = secret.encode()
        self.protocol = AetherProtocol(self.secret)
        self.router = AetherRouter(self.protocol, self.send_response)
        self.transport = None
        # Track last known address for each path ID to support dynamic dynamic path migrations
        self.path_addresses: Dict[int, Tuple[str, int]] = {}

    def connection_made(self, transport):
        self.transport = transport
        logger.info("AetherBond Server UDP socket successfully bound and listening.")

    def datagram_received(self, data: bytes, addr: Tuple[str, int]):
        # Extract path_id out of the header before full decryption to map client address
        try:
            # We can unpack the header (first 16 bytes: seq, timestamp, path_id)
            import struct
            if len(data) >= 16:
                seq, timestamp, path_id = struct.unpack("!IQI", data[:16])
                self.path_addresses[path_id] = addr
        except Exception as e:
            logger.debug(f"Failed to pre-parse path ID: {e}")

        # Let the router handle packet decryption and resequencing asynchronously
        asyncio.create_task(self.router.handle_client_packet(data, addr))

    async def send_response(self, packet_bytes: bytes, client_addr: Tuple[str, int], path_id: int):
        # Always prefer the last active address that successfully sent data for this path_id
        target_addr = self.path_addresses.get(path_id, client_addr)
        if self.transport:
            self.transport.sendto(packet_bytes, target_addr)
            
    async def shutdown(self):
        logger.info("Cleaning up active client sessions...")
        await self.router.cleanup()

async def main_async(host: str, port: int, secret: str):
    loop = asyncio.get_running_loop()
    logger.info(f"Starting AetherBond Aggregator Server on {host}:{port}...")
    
    # Create UDP endpoint
    server_protocol = AetherServerProtocol(secret)
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: server_protocol,
        local_addr=(host, port)
    )
    
    try:
        # Keep running until cancelled
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        logger.info("Shutdown signal received.")
    finally:
        await server_protocol.shutdown()
        transport.close()
        logger.info("Server socket closed. Exiting.")

def main():
    parser = argparse.ArgumentParser(description="AetherBond Aggregator Server (VPS side)")
    parser.add_argument("--host", default="0.0.0.0", help="Binding host address (default: 0.0.0.0)")
    parser.add_argument("--port", type=int, default=51820, help="Binding UDP port (default: 51820)")
    parser.add_argument("--secret", default="AetherBondSecretKeyDefault32Bytes!", help="Pre-shared symmetric key")
    
    args = parser.parse_args()
    
    try:
        asyncio.run(main_async(args.host, args.port, args.secret))
    except KeyboardInterrupt:
        logger.info("Server stopped by user keyboard interrupt.")

if __name__ == "__main__":
    main()
