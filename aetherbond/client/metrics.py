import asyncio
import time
import socket
import logging
import random
from typing import Dict, List, Optional, Any
from aetherbond.common.simulator import simulator_registry

logger = logging.getLogger("aetherbond.metrics")

class LinkMetrics:
    def __init__(self, name: str, ip: str, is_simulated: bool = False):
        self.name = name
        self.ip = ip
        self.is_simulated = is_simulated
        
        # Live stats
        self.rtt_ms: float = 0.0
        self.jitter_ms: float = 0.0
        self.loss_rate: float = 0.0
        self.bandwidth_mbps: float = 0.0
        self.is_online: bool = True
        self.score: float = 0.0
        
        # History for rolling metrics
        self._rtt_history: List[float] = []
        self._sent_packets: int = 0
        self._received_packets: int = 0

    def update_score(self):
        """
        Calculates a dynamic path quality score (higher is better).
        Formula balances high bandwidth with low latency and low packet loss.
        """
        if not self.is_online:
            self.score = 0.0
            return

        # Base factors
        rtt_factor = max(1.0, self.rtt_ms)
        loss_penalty = self.loss_rate * 500.0  # Heavy penalty for packet loss
        
        # If simulated, use simulation bandwidth directly, else default or passive measured
        bw = self.bandwidth_mbps if self.bandwidth_mbps > 0 else 10.0
        
        # Score calculation: Bandwidth is positive, latency and loss are negative
        self.score = (bw * 100) / (rtt_factor + loss_penalty)
        # Apply a scale factor
        self.score = round(max(0.1, self.score), 2)


class PathMonitor:
    def __init__(self, targets: List[str] = None):
        # Default public reliable IP addresses (e.g. Cloudflare, Google DNS)
        self.targets = targets or ["1.1.1.1", "8.8.8.8"]
        self.metrics: Dict[str, LinkMetrics] = {}
        self._monitor_tasks: Dict[str, asyncio.Task] = {}
        self.is_running = False

    def get_metrics(self, ip: str) -> Optional[LinkMetrics]:
        return self.metrics.get(ip)

    def register_interface(self, name: str, ip: str, is_simulated: bool = False) -> LinkMetrics:
        if ip not in self.metrics:
            self.metrics[ip] = LinkMetrics(name, ip, is_simulated)
            # Fetch default bandwidth estimate
            if is_simulated:
                sim_link = simulator_registry.get_interface(ip)
                if sim_link:
                    self.metrics[ip].bandwidth_mbps = sim_link.bandwidth_mbps
            else:
                # Default guess for physical interface (updated passively later)
                self.metrics[ip].bandwidth_mbps = 50.0 
        return self.metrics[ip]

    def start(self, interfaces: List[Any]):
        self.is_running = True
        for iface in interfaces:
            metric = self.register_interface(iface.name, iface.ip, iface.is_simulated)
            task = asyncio.create_task(self._monitor_loop(metric))
            self._monitor_tasks[iface.ip] = task
        logger.info(f"Path monitor started for {len(interfaces)} interfaces.")

    async def stop(self):
        self.is_running = False
        for task in self._monitor_tasks.values():
            task.cancel()
        await asyncio.gather(*self._monitor_tasks.values(), return_exceptions=True)
        self._monitor_tasks.clear()
        logger.info("Path monitor stopped.")

    async def _monitor_loop(self, metric: LinkMetrics):
        """
        Periodic ping loop to gather stats on a specific interface.
        Creates socket bound to the specific local interface IP to force path routing.
        """
        ping_interval = 2.0
        while self.is_running:
            try:
                if metric.is_simulated:
                    # In simulation mode, read directly from the simulator with added jitter
                    sim_link = simulator_registry.get_interface(metric.ip)
                    if sim_link and sim_link.is_alive:
                        metric.is_online = True
                        metric.bandwidth_mbps = sim_link.bandwidth_mbps
                        
                        # Simulate ping RTT
                        base_rtt = sim_link.latency_ms * 2
                        jitter = base_rtt * random.uniform(-0.1, 0.1)
                        current_rtt = max(1.0, base_rtt + jitter)
                        
                        # Apply packet loss simulation
                        if sim_link.loss_rate > 0 and random.random() < sim_link.loss_rate:
                            metric._sent_packets += 1
                            # Packet lost
                        else:
                            metric._sent_packets += 1
                            metric._received_packets += 1
                            self._record_rtt(metric, current_rtt)
                        
                        # Calculate loss rate
                        if metric._sent_packets > 0:
                            metric.loss_rate = (metric._sent_packets - metric._received_packets) / metric._sent_packets
                        
                    else:
                        metric.is_online = False
                        metric.rtt_ms = 9999.0
                        metric.loss_rate = 1.0
                else:
                    # Physical interface active pinging using bound socket
                    await self._ping_physical(metric)

                metric.update_score()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in monitor loop for {metric.name} ({metric.ip}): {e}")
                metric.is_online = False
                metric.update_score()

            await asyncio.sleep(ping_interval)

    def _record_rtt(self, metric: LinkMetrics, rtt: float):
        metric._rtt_history.append(rtt)
        if len(metric._rtt_history) > 10:
            metric._rtt_history.pop(0)
        
        # Calculate RTT and Jitter (avg deviation)
        metric.rtt_ms = sum(metric._rtt_history) / len(metric._rtt_history)
        if len(metric._rtt_history) > 1:
            metric.jitter_ms = sum(abs(r - metric.rtt_ms) for r in metric._rtt_history) / len(metric._rtt_history)
        else:
            metric.jitter_ms = 0.0

    async def _ping_physical(self, metric: LinkMetrics):
        """
        Sends a UDP ping to one of the targets, binding to the interface local IP.
        """
        target = random.choice(self.targets)
        port = 53 # Standard DNS port (safe for UDP packets)
        
        loop = asyncio.get_running_loop()
        start_time = time.time()
        
        # Create a socket and bind to local interface IP
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setblocking(False)
        
        try:
            # Bind to local IP and let OS pick ephemeral port
            sock.bind((metric.ip, 0))
            
            # DNS query payload (simple transaction ID and flags)
            # A lightweight DNS ping ensures firewalls don't drop it (unlike ICMP)
            query = b'\xaa\xbb\x01\x00\x00\x01\x00\x00\x00\x00\x00\x00\x03www\x06google\x03com\x00\x00\x01\x00\x01'
            
            metric._sent_packets += 1
            await loop.sock_sendto(sock, query, (target, port))
            
            # Wait for response with a 1.5 second timeout
            try:
                await asyncio.wait_for(loop.sock_recv(sock, 512), timeout=1.5)
                rtt = (time.time() - start_time) * 1000.0
                metric._received_packets += 1
                metric.is_online = True
                self._record_rtt(metric, rtt)
            except asyncio.TimeoutError:
                # Timed out -> loss
                pass
                
            if metric._sent_packets > 0:
                metric.loss_rate = (metric._sent_packets - metric._received_packets) / metric._sent_packets
                
        except Exception as e:
            # Interface might be down, or permission issues
            metric.is_online = False
            metric.loss_rate = 1.0
            metric.rtt_ms = 9999.0
        finally:
            sock.close()
