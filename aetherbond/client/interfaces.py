import psutil
import socket
import logging
from typing import Dict, List, Any
from aetherbond.common.simulator import simulator_registry

logger = logging.getLogger("aetherbond.interfaces")

class InterfaceInfo:
    def __init__(self, name: str, ip: str, is_simulated: bool = False):
        self.name = name
        self.ip = ip
        self.is_simulated = is_simulated

    def __repr__(self) -> str:
        return f"InterfaceInfo(name={self.name}, ip={self.ip}, simulated={self.is_simulated})"


def get_active_interfaces(use_simulation: bool = False) -> List[InterfaceInfo]:
    """
    Returns list of active network interfaces (physical or simulated).
    """
    if use_simulation:
        interfaces = []
        for ip, link in simulator_registry.interfaces.items():
            if link.is_alive:
                interfaces.append(InterfaceInfo(link.name, link.ip, is_simulated=True))
        return interfaces

    interfaces = []
    try:
        addrs = psutil.net_if_addrs()
        stats = psutil.net_if_stats()
        
        for iface_name, iface_addrs in addrs.items():
            # Check if the interface is up
            if iface_name in stats and not stats[iface_name].isup:
                continue

            # Ignore loopback adapters
            if "loopback" in iface_name.lower() or "localhost" in iface_name.lower():
                continue

            for addr in iface_addrs:
                # We only want IPv4 addresses currently for simple routing binding
                if addr.family == socket.AF_INET:
                    ip = addr.address
                    # Skip local loopback address explicitly
                    if ip.startswith("127."):
                        continue
                    
                    interfaces.append(InterfaceInfo(iface_name, ip, is_simulated=False))
                    
    except Exception as e:
        logger.error(f"Error scanning network interfaces: {e}")
        # Fall back to resolving hostname IP
        try:
            hostname = socket.gethostname()
            ip = socket.gethostbyname(hostname)
            if not ip.startswith("127."):
                interfaces.append(InterfaceInfo("PrimaryAdapter", ip, is_simulated=False))
        except Exception:
            pass

    return interfaces
