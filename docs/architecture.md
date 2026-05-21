# AetherBond Architecture Specification

This document details the system design, network routing strategies, and packet schedules for AetherBond, a fully open-source multipath bandwidth aggregation system.

---

## 1. System Topology

```mermaid
flowchart TD
    subgraph Client [Client Machine (Desktop)]
        App[User Application / Browser] --> |HTTP/SOCKS Request| LocalProxy[Local Proxy / TUN Adapter]
        LocalProxy --> |Intercepts Packets| Sched[Intelligent Scheduler]
        
        subgraph Paths [Physical Network Interfaces]
            Sched -->|Link 1: Bind 192.168.1.15| Wi-Fi[Wi-Fi Interface]
            Sched -->|Link 2: Bind 10.0.0.4| Eth[Ethernet Interface]
            Sched -->|Link 3: Bind 192.168.8.100| LTE[USB LTE Dongle]
        end
        
        Monitor[Active Path Monitor] -.->|RTT, Jitter, Packet Loss| Sched
    end

    subgraph Internet_Transit [Internet Gateways]
        Wi-Fi -->|ISP A Gateway| NetA[Wi-Fi Transit Path]
        Eth -->|ISP B Gateway| NetB[Ethernet Transit Path]
        LTE -->|ISP C Gateway| NetC[LTE Transit Path]
    end

    subgraph Server [VPS Aggregator Server]
        NetA -->|Encapsulated UDP| Recv[Multipath UDP Receiver]
        NetB -->|Encapsulated UDP| Recv
        NetC -->|Encapsulated UDP| Recv
        
        Recv --> Reseq[Resequencing & Reassembly Buffer]
        Reseq --> NAT[NAT & IP Forwarder]
        NAT -->|Forward to Web| Target[Public Internet Destination]
    end

    Target -->|Response Data| NAT
    NAT -->|Encapsulated Response| Strip[Downlink Packet Striper]
    Strip -->|Send back over Link 1| Wi-Fi
    Strip -->|Send back over Link 2| Eth
    Strip -->|Send back over Link 3| LTE
```

---

## 2. Dynamic Interface Selection & IP Binding

Standard sockets normally rely on the operating system's routing table default gateway. To route connections through a *specific* interface (e.g., forcing a packet onto Ethernet even when Wi-Fi is the primary interface), AetherBond leverages **Interface-Aware Socket Binding**.

### Mechanism: Local IP Binding
Before calling `connect()` on a socket, the client calls `bind((local_ip, 0))`.
* **How it works**: By binding to the specific local IP address of an interface (e.g., `192.168.1.15`), the OS IP routing layer is forced to choose the physical interface corresponding to that subnet and route the packet through its designated gateway.
* **Compatibility**: This is fully supported on Windows, Linux, and macOS without requiring root privileges.
* **Routing Table Caveat**: If an interface has a gateway that is completely ignored by the main routing table, native policy routing (e.g., `ip rule add from <IP> table <T>` on Linux) must be set up so that packets originating from that local IP are correctly sent to that interface's default gateway.

---

## 3. Multipath Downloader Design (Phase 1)

For large file downloads, AetherBond divides a target HTTP file into discrete byte ranges and streams them in parallel.

```
       [Target File: 100MB]
                 |
  +--------------+--------------+
  |              |              |
[Chunk 1]     [Chunk 2]     [Chunk 3]
 (0-33MB)      (33-66MB)     (66-100MB)
  |              |              |
[Bind: Wi-Fi] [Bind: Eth]   [Bind: LTE]
  |              |              |
  v              v              v
=========================================
      Combined Network Assembly
```

* **Dynamic Chunk Allocation**: Chunks are dynamically allocated using **Weighted Round Robin (WRR)**. If `Eth` is 3x faster than `Wi-Fi`, it gets 3x more chunks.
* **In-Flight Re-steering**: If a link drops or exhibits massive latency spikes, its currently active chunk download is aborted and re-queued to an active, reliable link.
* **Merging Pipeline**: Chunks are downloaded to disk blocks directly using file seek offsets (`file.seek(start_byte)`), eliminating the memory overhead of holding multi-gigabyte files in RAM.

---

## 4. The Out-of-Order Packet Problem & VPN Resequencing (Phase 3)

Simple packet striping across paths with different latencies degrades standard TCP. If **Link A** has 20ms RTT and **Link B** has 120ms RTT, sending packets 1, 3, 5 over Link A and 2, 4, 6 over Link B results in packet 3 arriving *before* packet 2. 

Receiving stacks interpret out-of-order packets as network congestion, triggering:
1. **Duplicate ACKs**.
2. **TCP Fast Retransmit** (re-sending packets that are already in transit).
3. **TCP Congestion Window Halving**, which collapses overall throughput.

### Solution: Resequencing Heap Buffer
The Aggregator Server implements a sliding-window heap reorder buffer:
* Incoming packets are pushed to a min-heap keyed by sequence numbers.
* Packets are popped and released to the OS TUN interface *only* in strict contiguous order (`expected_seq`).
* If a packet is lost, the buffer waits up to a tiny threshold (e.g., 20ms) for an adaptive retransmission before releasing subsequent packets to prevent complete stalling.
