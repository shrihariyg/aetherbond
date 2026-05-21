import unittest
import asyncio
import socket
import sys
import os
import struct

# Adjust path to import local aetherbond package
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from aetherbond.client.interfaces import get_active_interfaces
from aetherbond.client.metrics import PathMonitor
from aetherbond.client.scheduler import Scheduler
from aetherbond.client.vpn import AetherVPNClient
from aetherbond.server.main import AetherServerProtocol
from aetherbond.common.simulator import simulator_registry

class TestVPN(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Enable simulator
        simulator_registry.enable()
        
        # Disable packet loss for deterministic unit testing
        for link in simulator_registry.interfaces.values():
            link.loss_rate = 0.0
            
        # Setup scheduler
        self.interfaces = get_active_interfaces(use_simulation=True)
        self.monitor = PathMonitor()
        self.scheduler = Scheduler(self.monitor)
        self.monitor.start(self.interfaces)
        
        # Wait a moment for metrics calculation
        await asyncio.sleep(0.5)

    async def asyncTearDown(self):
        self.monitor.start_time = 0
        await self.monitor.stop()

    async def test_vpn_tunnel_handshake_and_relayed_transmission(self):
        """
        Tests the Phase 3 VPN Client and Server Router pipeline end-to-end.
        Relays SOCKS requests via the encrypted multipath UDP tunnels to a mock echo server.
        """
        echo_host = "127.0.0.1"
        echo_port = 29999
        server_port = 51820
        client_proxy_port = 11085
        secret = "AetherBondSecretKeyDefault32Bytes!"

        # 1. Start Mock Echo Server
        echo_received = asyncio.Event()
        
        async def handle_echo(reader, writer):
            data = await reader.read(100)
            writer.write(data)
            await writer.drain()
            writer.close()
            await writer.wait_closed()
            echo_received.set()

        echo_server = await asyncio.start_server(handle_echo, echo_host, echo_port)

        # 2. Start AetherBond Aggregator Server (UDP Protocol)
        loop = asyncio.get_running_loop()
        server_protocol = AetherServerProtocol(secret)
        server_transport, _ = await loop.create_datagram_endpoint(
            lambda: server_protocol,
            local_addr=("127.0.0.1", server_port)
        )

        # 3. Start AetherVPNClient
        vpn_client = AetherVPNClient("127.0.0.1", server_port, secret, self.scheduler)
        await vpn_client.start(local_proxy_port=client_proxy_port)

        await asyncio.sleep(0.2)  # Give ports a moment to bind

        try:
            # 4. Initiate local client SOCKS5 connection to the VPN Client
            reader, writer = await asyncio.open_connection("127.0.0.1", client_proxy_port)

            # SOCKS5 Handshake
            writer.write(b"\x05\x01\x00")
            await writer.drain()

            handshake_resp = await reader.readexactly(2)
            self.assertEqual(handshake_resp, b"\x05\x00")

            # SOCKS5 Connection request targeting the mock Echo Server
            ip_bytes = socket.inet_aton(echo_host)
            port_bytes = bytearray(b"\x05\x01\x00\x01") + ip_bytes + bytearray(struct.pack("!H", echo_port))
            writer.write(port_bytes)
            await writer.drain()

            # Read SOCKS5 connection reply
            reply = await reader.readexactly(10)
            self.assertEqual(reply[0], 5) # SOCKS5 Version
            self.assertEqual(reply[1], 0) # Success

            # 5. Send payload over the VPN Tunnel
            test_message = b"AetherBond Phase 3 VPN encapsulated pipeline payload!"
            writer.write(test_message)
            await writer.drain()

            # Read echoed payload back
            echoed_data = await reader.readexactly(len(test_message))
            self.assertEqual(echoed_data, test_message)

            writer.close()
            await writer.wait_closed()

            # Verify target echo server got the message
            self.assertTrue(await asyncio.wait_for(echo_received.wait(), timeout=2.0))

        finally:
            # Cleanup all components
            await vpn_client.stop()
            await server_protocol.shutdown()
            server_transport.close()
            echo_server.close()
            await echo_server.wait_closed()


if __name__ == "__main__":
    unittest.main()
