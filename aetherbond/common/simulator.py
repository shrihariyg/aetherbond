import asyncio
import random
import time
import logging
from typing import Dict, Optional

logger = logging.getLogger("aetherbond.simulator")

class SimulatedInterface:
    def __init__(self, name: str, ip: str, bandwidth_mbps: float, latency_ms: float, loss_rate: float = 0.0):
        """
        Represents a simulated physical interface.
        - bandwidth_mbps: Max bandwidth in Megabits per second.
        - latency_ms: Simulated latency (one-way delay, 1/2 of RTT) in milliseconds.
        - loss_rate: Packet loss probability (0.0 to 1.0).
        """
        self.name = name
        self.ip = ip
        self.bandwidth_mbps = bandwidth_mbps
        self.latency_ms = latency_ms
        self.loss_rate = loss_rate
        self.is_alive = True
        self.total_bytes_transferred = 0
        self.current_speed_bps = 0.0
        self._last_speed_update = time.time()
        self._interval_bytes = 0

    def get_bandwidth_bps(self) -> float:
        return (self.bandwidth_mbps * 1_000_000) / 8

    def update_bytes(self, num_bytes: int):
        self.total_bytes_transferred += num_bytes
        self._interval_bytes += num_bytes
        now = time.time()
        elapsed = now - self._last_speed_update
        if elapsed >= 1.0:
            self.current_speed_bps = self._interval_bytes / elapsed
            self._interval_bytes = 0
            self._last_speed_update = now

    def toggle(self, state: Optional[bool] = None):
        """Turn the interface UP or DOWN to simulate failover."""
        if state is None:
            self.is_alive = not self.is_alive
        else:
            self.is_alive = state
        logger.warning(f"Interface {self.name} is now {'UP' if self.is_alive else 'DOWN'}")


class LinkSimulator:
    def __init__(self):
        self.interfaces: Dict[str, SimulatedInterface] = {}
        self.is_enabled = False

    def add_interface(self, name: str, ip: str, bandwidth_mbps: float, latency_ms: float, loss_rate: float = 0.0):
        self.interfaces[ip] = SimulatedInterface(name, ip, bandwidth_mbps, latency_ms, loss_rate)
        logger.info(f"Added simulated interface {name} ({ip}) - {bandwidth_mbps}Mbps, {latency_ms}ms, loss: {loss_rate * 100}%")

    def get_interface(self, ip: str) -> Optional[SimulatedInterface]:
        return self.interfaces.get(ip)

    def enable(self):
        self.is_enabled = True
        logger.info("Link simulator enabled.")

    def disable(self):
        self.is_enabled = False
        logger.info("Link simulator disabled.")

    async def simulate_delay(self, ip: str):
        """Simulates path latency (RTT) and checks for packet loss or link failure."""
        if not self.is_enabled:
            return

        if ip not in self.interfaces:
            return

        link = self.interfaces[ip]
        if not link.is_alive:
            raise ConnectionResetError(f"Simulated link {link.name} ({ip}) is down.")

        # Simulate packet loss
        if link.loss_rate > 0.0 and random.random() < link.loss_rate:
            raise OSError(f"Simulated packet loss on link {link.name} ({ip}).")

        # Simulate latency delay (one-way latency * 2 = RTT)
        delay_sec = (link.latency_ms * 2) / 1000.0
        # Add slight jitter (+/- 10%)
        jitter = delay_sec * random.uniform(-0.1, 0.1)
        await asyncio.sleep(max(0.001, delay_sec + jitter))

    async def throttle_stream(self, ip: str, chunk_size: int):
        """Throttles transmission to match simulated bandwidth limitations."""
        if not self.is_enabled or ip not in self.interfaces:
            return

        link = self.interfaces[ip]
        if not link.is_alive:
            raise ConnectionResetError(f"Simulated link {link.name} ({ip}) is down.")

        # Calculate time needed to transfer this chunk at the interface's speed limit
        speed_bps = link.get_bandwidth_bps()
        if speed_bps <= 0:
            raise ZeroDivisionError("Simulated interface bandwidth is set to zero.")
            
        transfer_time = chunk_size / speed_bps
        await asyncio.sleep(transfer_time)
        link.update_bytes(chunk_size)

# Global simulator instance
simulator_registry = LinkSimulator()

# Populate default simulation profiles
simulator_registry.add_interface("Sim_WiFi", "192.168.99.10", bandwidth_mbps=15.0, latency_ms=15.0, loss_rate=0.01)
simulator_registry.add_interface("Sim_Ethernet", "192.168.99.20", bandwidth_mbps=45.0, latency_ms=8.0, loss_rate=0.00)
simulator_registry.add_interface("Sim_Cellular", "192.168.99.30", bandwidth_mbps=8.0, latency_ms=60.0, loss_rate=0.03)
