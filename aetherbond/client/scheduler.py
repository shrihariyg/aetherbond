import random
import logging
from typing import Dict, List, Optional
from aetherbond.client.metrics import PathMonitor, LinkMetrics

logger = logging.getLogger("aetherbond.scheduler")

class Scheduler:
    def __init__(self, monitor: PathMonitor):
        self.monitor = monitor
        self._wrr_index = 0

    def get_active_links(self) -> List[LinkMetrics]:
        """
        Returns all online links that have a valid score > 0.
        """
        active = []
        for metric in self.monitor.metrics.values():
            if metric.is_online and metric.score > 0:
                active.append(metric)
        return active

    def select_interface_wrr(self) -> str:
        """
        Performs a Weighted Round Robin selection based on link scores.
        Excellent for distributing multi-connection download chunks.
        """
        links = self.get_active_links()
        if not links:
            raise ConnectionError("No active network interfaces are online.")

        # Calculate sum of scores
        total_score = sum(link.score for link in links)
        if total_score <= 0:
            # Fall back to uniform choice if scores are zero
            return random.choice(links).ip

        # Probabilistic weighted selection
        r = random.uniform(0, total_score)
        cumulative = 0.0
        for link in links:
            cumulative += link.score
            if r <= cumulative:
                return link.ip

        return links[0].ip

    def select_interface_lowest_latency(self) -> str:
        """
        Selects the link with the absolute lowest RTT (latency-aware routing).
        Used for SOCKS handshake, DNS queries, and latency-critical traffic.
        """
        links = self.get_active_links()
        if not links:
            raise ConnectionError("No active network interfaces are online.")

        # Sort by RTT (ascending)
        links_sorted = sorted(links, key=lambda l: l.rtt_ms)
        return links_sorted[0].ip

    def get_telemetry(self) -> List[Dict]:
        """
        Returns a serializable list of metrics for GUI/telemetry dashboards.
        """
        telemetry = []
        for metric in self.monitor.metrics.values():
            telemetry.append({
                "name": metric.name,
                "ip": metric.ip,
                "is_simulated": metric.is_simulated,
                "is_online": metric.is_online,
                "rtt_ms": round(metric.rtt_ms, 2),
                "jitter_ms": round(metric.jitter_ms, 2),
                "loss_rate": round(metric.loss_rate * 100, 2), # percentage
                "bandwidth_mbps": round(metric.bandwidth_mbps, 2),
                "score": metric.score
            })
        return telemetry
