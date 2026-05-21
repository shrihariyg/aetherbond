import argparse
import asyncio
import os
import sys
import logging
from colorama import init, Fore, Style
from aetherbond.client.interfaces import get_active_interfaces
from aetherbond.client.metrics import PathMonitor
from aetherbond.client.scheduler import Scheduler
from aetherbond.client.downloader import DownloadSession
from aetherbond.common.simulator import simulator_registry

# Initialize colorama
init()

# Setup logging
logging.basicConfig(
    level=logging.ERROR,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("aetherbond.main")

def print_banner():
    banner = f"""
{Fore.CYAN}{Style.BRIGHT}=============================================================
           _   _   _   _   ___   _   _   _   _   
          / \ / \ / \ / \ /   \ / \ / \ / \ / \  
         ( A | e | t | h | e r | B | o | n | d ) 
          \_/ \_/ \_/ \_/ \___/ \_/ \_/ \_/ \_/  
  
    Multipath Bandwidth Aggregator & Speedify Core (v1.0)
=============================================================
{Style.RESET_ALL}"""
    print(banner)

def render_progress_bar(percent: float, speed_mbps: float, elapsed: float, completed: int, total: int):
    bar_length = 30
    filled = int(bar_length * percent / 100)
    bar = "█" * filled + "-" * (bar_length - filled)
    
    sys.stdout.write(
        f"\r{Fore.GREEN}Progress: |{bar}| {percent:.1f}% "
        f"({completed}/{total} chunks) | Speed: {Fore.YELLOW}{speed_mbps:.2f} Mbps{Fore.GREEN} | Time: {elapsed:.1f}s{Style.RESET_ALL}"
    )
    sys.stdout.flush()

def print_interface_table(scheduler: Scheduler):
    links = scheduler.monitor.metrics.values()
    print(f"\n{Fore.MAGENTA}Active Interface Telemetry:{Style.RESET_ALL}")
    print(f"{'Interface':<15} | {'IP Address':<15} | {'RTT (ms)':<10} | {'Loss (%)':<10} | {'Bandwidth':<12} | {'Score':<10}")
    print("-" * 78)
    
    for l in links:
        status_color = Fore.GREEN if l.is_online else Fore.RED
        loss_pct = l.loss_rate * 100
        
        # Color coding RTT and loss
        rtt_color = Fore.GREEN if l.rtt_ms < 50 else (Fore.YELLOW if l.rtt_ms < 150 else Fore.RED)
        loss_color = Fore.GREEN if l.loss_rate == 0 else (Fore.YELLOW if l.loss_rate < 0.05 else Fore.RED)
        
        print(
            f"{status_color}{l.name:<15}{Style.RESET_ALL} | "
            f"{l.ip:<15} | "
            f"{rtt_color}{l.rtt_ms:>8.1f} ms{Style.RESET_ALL} | "
            f"{loss_color}{loss_pct:>7.1f}%{Style.RESET_ALL} | "
            f"{Fore.CYAN}{l.bandwidth_mbps:>7.1f} Mbps{Style.RESET_ALL} | "
            f"{Fore.WHITE}{l.score:>8.1f}{Style.RESET_ALL}"
        )
    print()

async def download_workflow(url: str, output: str, chunk_size: float, use_sim: bool, sim_failover_trigger: bool):
    print_banner()
    
    if use_sim:
        print(f"{Fore.YELLOW}[SIMULATION MODE ACTIVE]{Style.RESET_ALL}")
        simulator_registry.enable()
    else:
        print(f"{Fore.GREEN}[PHYSICAL MODE ACTIVE]{Style.RESET_ALL}")
        simulator_registry.disable()

    # 1. Interface Discovery
    interfaces = get_active_interfaces(use_simulation=use_sim)
    if not interfaces:
        print(f"{Fore.RED}Error: No active interfaces detected. Aborting.{Style.RESET_ALL}")
        return

    print(f"{Fore.CYAN}Discovered {len(interfaces)} Interfaces:{Style.RESET_ALL}")
    for iface in interfaces:
        print(f" - {iface.name} ({iface.ip}) simulated={iface.is_simulated}")
    
    # 2. Initialize Path Monitor & Scheduler
    monitor = PathMonitor()
    scheduler = Scheduler(monitor)
    monitor.start(interfaces)
    
    # Let monitor compile first metrics pass (1.0 sec)
    print(f"\n{Fore.WHITE}Probing interfaces and calculating path metrics...{Style.RESET_ALL}")
    await asyncio.sleep(1.5)
    print_interface_table(scheduler)

    # 3. Setup Downloader Session
    session = DownloadSession(url, output, scheduler, chunk_size_mb=chunk_size)
    
    def on_progress(p):
        render_progress_bar(
            p["percent"], p["speed_mbps"], p["elapsed_sec"], 
            p["chunks_completed"], p["total_chunks"]
        )

    session.on_progress_cb = on_progress
    
    success = await session.initialize()
    if not success:
        print(f"{Fore.RED}\nFailed to initialize download session. Check link and destination.{Style.RESET_ALL}")
        await monitor.stop()
        return

    # Trigger optional failover demo task in background
    failover_demo_task = None
    if use_sim and sim_failover_trigger:
        async def trigger_failover_simulation():
            # Wait 4 seconds, then drop the fastest connection (Sim_Ethernet) to demonstrate failover!
            await asyncio.sleep(4.0)
            eth = simulator_registry.get_interface("192.168.99.20")
            if eth:
                print(f"\n\n{Fore.RED}[SIMULATOR TRIGGER] Dropping Sim_Ethernet link to test failover!{Style.RESET_ALL}\n")
                eth.toggle(False)
                
            # Wait another 4 seconds, and revive it
            await asyncio.sleep(4.0)
            if eth:
                print(f"\n\n{Fore.GREEN}[SIMULATOR TRIGGER] Restoring Sim_Ethernet link back UP!{Style.RESET_ALL}\n")
                eth.toggle(True)
                
        failover_demo_task = asyncio.create_task(trigger_failover_simulation())

    # 4. Run Aggregated Download
    print(f"{Fore.CYAN}Starting multipath bandwidth aggregation download...{Style.RESET_ALL}\n")
    try:
        await session.start()
        print(f"\n\n{Fore.GREEN}✓ Download complete successfully! Saved to: {output}{Style.RESET_ALL}")
    except Exception as e:
        print(f"\n\n{Fore.RED}✗ Download failed: {e}{Style.RESET_ALL}")
    finally:
        if failover_demo_task:
            failover_demo_task.cancel()
        await monitor.stop()
        
    # Print final summary table
    print_interface_table(scheduler)

def main():
    parser = argparse.ArgumentParser(description="AetherBond: Speedify-like Multipath Bandwidth Aggregator")
    parser.add_argument("url", nargs="?", default="https://releases.ubuntu.com/22.04/ubuntu-22.04.4-desktop-amd64.iso", help="URL of the file to download")
    parser.add_argument("-o", "--output", default="downloaded_file.iso", help="Output file path destination")
    parser.add_argument("-c", "--chunk-size", type=float, default=5.0, help="Chunk range segment size in Megabytes (default: 5MB)")
    parser.add_argument("--simulated", action="store_true", default=True, help="Enable link simulation (default: True, runs tests on single network configs)")
    parser.add_argument("--no-sim", action="store_false", dest="simulated", help="Run in physical network mode binding to raw interfaces")
    parser.add_argument("--failover-demo", action="store_true", default=True, help="Simulate a link dropping and reviving mid-download")
    
    args = parser.parse_args()
    
    asyncio.run(download_workflow(
        args.url, args.output, args.chunk_size, args.simulated, args.failover_demo
    ))

if __name__ == "__main__":
    main()
