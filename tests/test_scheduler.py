import unittest
import sys
import os

# Adjust path to import local aetherbond package
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from aetherbond.client.interfaces import InterfaceInfo
from aetherbond.client.metrics import PathMonitor
from aetherbond.client.scheduler import Scheduler

class TestScheduler(unittest.TestCase):
    def setUp(self):
        # Create a monitor with mock targets
        self.monitor = PathMonitor(targets=["127.0.0.1"])
        self.scheduler = Scheduler(self.monitor)
        
        # Register simulated interfaces
        self.wifi = self.monitor.register_interface("WiFi", "192.168.1.50", is_simulated=True)
        self.eth = self.monitor.register_interface("Ethernet", "10.0.0.50", is_simulated=True)
        self.lte = self.monitor.register_interface("LTE", "192.168.8.50", is_simulated=True)

    def test_lowest_latency_routing(self):
        """Verify that the latency-aware scheduler picks the absolute lowest RTT path."""
        # Configure latencies
        self.wifi.is_online = True
        self.wifi.rtt_ms = 40.0
        self.wifi.score = 50.0
        
        self.eth.is_online = True
        self.eth.rtt_ms = 10.0  # Lowest RTT
        self.eth.score = 100.0
        
        self.lte.is_online = True
        self.lte.rtt_ms = 120.0
        self.lte.score = 20.0
        
        best_ip = self.scheduler.select_interface_lowest_latency()
        self.assertEqual(best_ip, "10.0.0.50")  # Ethernet IP

    def test_failover_exclusion(self):
        """Verify that offline links are immediately excluded from scheduling."""
        self.wifi.is_online = True
        self.wifi.score = 50.0
        
        self.eth.is_online = False  # Down!
        self.eth.score = 100.0
        
        self.lte.is_online = True
        self.lte.score = 20.0

        # Perform WRR selections and verify Ethernet is never chosen
        selections = [self.scheduler.select_interface_wrr() for _ in range(50)]
        self.assertNotIn("10.0.0.50", selections)
        
        # Verify only WiFi and LTE are selected
        self.assertTrue(all(ip in ["192.168.1.50", "192.168.8.50"] for ip in selections))

    def test_no_active_links(self):
        """Verify that a ConnectionError is raised if all interfaces are down."""
        self.wifi.is_online = False
        self.eth.is_online = False
        self.lte.is_online = False
        
        with self.assertRaises(ConnectionError):
            self.scheduler.select_interface_wrr()

if __name__ == "__main__":
    unittest.main()
