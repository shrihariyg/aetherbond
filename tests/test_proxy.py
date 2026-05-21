import unittest
import asyncio
import socket
import sys
import os

# Adjust path to import local aetherbond package
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from aetherbond.client.interfaces import get_active_interfaces
from aetherbond.client.metrics import PathMonitor
from aetherbond.client.scheduler import Scheduler
from aetherbond.client.proxy import Socks5Proxy
from aetherbond.common.simulator import simulator_registry

class TestProxy(unittest.IsolatedAsyncioTestCase):
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

    async def test_socks5_proxy_handshake_and_relay(self):
        """
        Runs SOCKS5 proxy and a mock echo server, verifying handshake and bidirectional pipe.
        """
        echo_host = "127.0.0.1"
        echo_port = 19999
        proxy_port = 11080
        
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
        
        # 2. Start SOCKS5 Proxy
        proxy = Socks5Proxy("127.0.0.1", proxy_port, self.scheduler)
        proxy_task = asyncio.create_task(proxy.start())
        
        await asyncio.sleep(0.1)  # Allow servers to start
        
        try:
            # 3. Simulate Client SOCKS5 Handshake and Connection Request
            # Connect directly to our SOCKS5 proxy
            reader, writer = await asyncio.open_connection("127.0.0.1", proxy_port)
            
            # SOCKS5 Handshake: Version 5, 1 Method, Method 0x00 (NO AUTH)
            writer.write(b"\x05\x01\x00")
            await writer.drain()
            
            # Read handshake response
            handshake_resp = await reader.readexactly(2)
            self.assertEqual(handshake_resp, b"\x05\x00")
            
            # SOCKS5 Request: Connect to Mock Echo Server (127.0.0.1:19999)
            # Command=1 (CONNECT), RSV=0, ATYP=1 (IPv4), DST.ADDR=127.0.0.1, DST.PORT=19999
            ip_bytes = socket.inet_aton(echo_host)
            port_bytes = struct_pack = bytearray(b"\x05\x01\x00\x01") + ip_bytes + bytearray(b"\x4e\x1f") # 19999 is 0x4e1f
            writer.write(port_bytes)
            await writer.drain()
            
            # Read request reply (10 bytes for SOCKS5 IPv4 reply)
            reply = await reader.readexactly(10)
            self.assertEqual(reply[0], 5) # Version 5
            self.assertEqual(reply[1], 0) # Success Status
            
            # 4. Perform Data Transfer over SOCKS5 Tunnel
            test_message = b"AetherBond aggregated proxy payload check!"
            writer.write(test_message)
            await writer.drain()
            
            # Read echoed data back
            echoed_data = await reader.readexactly(len(test_message))
            self.assertEqual(echoed_data, test_message)
            
            writer.close()
            await writer.wait_closed()
            
            # Assert target echo server was actually triggered
            self.assertTrue(await asyncio.wait_for(echo_received.wait(), timeout=1.0))
            
        finally:
            # Shutdown servers
            echo_server.close()
            await echo_server.wait_closed()
            await proxy.stop()
            proxy_task.cancel()

if __name__ == "__main__":
    unittest.main()
