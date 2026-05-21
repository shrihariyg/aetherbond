# AetherBond System Architecture Diagram

This document contains the core system architecture diagram for **AetherBond**, showing the client machine, multipath transit lanes, and the VPS aggregator server.

## Mermaid Source Diagram

```mermaid
flowchart TD
    subgraph Client [Client Machine]
        App[User Applications / Downloader] --> |Traffic| Sched[AetherBond Scheduler]
        
        subgraph Scheduler [Scheduler & Monitoring]
            Sched -->|Path 1: Wi-Fi| Sock1[Interface Socket 1: 192.168.1.15]
            Sched -->|Path 2: Ethernet| Sock2[Interface Socket 2: 10.0.0.4]
            Sched -->|Path 3: LTE Dongle| Sock3[Interface Socket 3: 192.168.8.100]
            
            Monitor[Path Monitor] -.-> |RTT, Loss, Jitter| Sched
        end
    end

    subgraph Internet_Lanes [Multipath Transit]
        Sock1 -->|ISP A| NetA[Wi-Fi Link]
        Sock2 -->|ISP B| NetB[Ethernet Link]
        Sock3 -->|ISP C| NetC[LTE Link]
    end

    subgraph Aggregator [VPS Aggregator Server]
        NetA -->|Encapsulated Traffic| Recv[Multipath Receiver]
        NetB -->|Encapsulated Traffic| Recv
        NetC -->|Encapsulated Traffic| Recv
        
        Recv --> Reorder[Resequencing & Reassembly Buffer]
        Reorder --> NAT[NAT & IP Forwarder]
        NAT -->|Public IP| Dest[Public Internet / Target Server]
    end
```

## Visual Representation

Below is the rendered high-fidelity system diagram:

![AetherBond System Diagram](system_diagram.png)
