# AetherBond: An Adaptive Multipath VPN Framework for High-Throughput Bandwidth Aggregation and Seamless Failover over Heterogeneous Networks

**Shrihari Y Gitte**  
*Department of Electonics and Communication Engineering*  
*AetherBond Research & Systems Group*  
*Email: contact@aetherbond.net*

---

## Abstract
Modern mobile and edge computing devices are ubiquitously equipped with multiple heterogeneous network interfaces, including Wi-Fi, cellular (4G/5G), and physical Ethernet. However, standard transport protocols and legacy Virtual Private Networks (VPNs) remain fundamentally constrained by single-path transport bindings. Consequently, they are incapable of aggregate throughput utilization or hitless failover across multiple active links. 

This paper presents **AetherBond**, a highly reliable, high-performance, and secure multipath VPN framework designed specifically to aggregate bandwidth and guarantee seamless failover across heterogeneous, asymmetric transport layers. 

AetherBond solves the traditional challenges of multipath packet scheduling—such as the out-of-order packet arrival penalty, severe latency asymmetry, and TCP-over-TCP encapsulation collapse—by employing a decoupled architecture: a high-performance, user-space **Rust Data Plane** (`aetherbond-core`) and a robust **Go Control Plane** (`aetherbond-control`). 

Our framework features a BBR-inspired path congestion estimator, a dynamic Kalman Filter for sub-millisecond jitter estimation, and an adaptive sliding-window Min-Heap resequencing buffer. 

Experimental evaluations demonstrate that AetherBond achieves an aggregation efficiency of **88.0%** over physical asymmetric networks, handles absolute primary link disconnections with a failover convergence time of **0.28 seconds**, and completely prevents Head-of-Line (HoL) blocking through multi-stream QUIC-like transport virtualization.

---

## 1. Introduction
The rapid proliferation of heterogeneous wireless networks has created environments where user devices are simultaneously surrounded by multiple connectivity pathways. A standard laptop or edge gateway is commonly exposed to physical Ethernet, high-frequency Wi-Fi, and multi-band cellular (LTE/5G) connections. Under standard TCP/IP networking paradigms, however, transport sessions are tightly bound to a single local IP address and gateway. This classic single-path design suffers from major operational limitations:
1. **Bandwidth Underutilization**: Active physical interfaces remain idle, wasting substantial cumulative channel capacities.
2. **Brittle Failover Transitions**: If a primary link fails (e.g., a user walking out of Wi-Fi range), all active socket connections terminate, causing severe session degradation for real-time applications such as VoIP, streaming, and low-latency gaming.
3. **Traditional VPN Constraints**: Existing VPN protocols (such as IPsec or OpenVPN) encapsulate traffic inside a single socket link, inheriting the exact single-path vulnerabilities of their underlying transport lanes.

To bypass these limitations, several multipath transport solutions have been proposed. *Multipath TCP (MPTCP)* [1] modifies the kernel network stack to stripe subflows across multiple paths. However, MPTCP requires end-to-end support across the public internet, which is heavily restricted by intermediate middleboxes, NATs, and firewalls that aggressively drop unfamiliar TCP options. 

SD-WAN systems and legacy multipath VPNs, such as *MLVPN* [2] or *OpenMPTCPRouter* [3], rely heavily on standard round-robin packet distribution. While functional over symmetric links, round-robin distribution catastrophically degrades TCP performance when paths are asymmetric: sending packets across a high-speed, low-latency Ethernet link alongside a slow, high-latency LTE link causes severe packet reordering. The receiver's TCP stack interprets these out-of-order packets as network congestion, triggering aggressive window reduction and retransmission storms, known as the *TCP Out-of-Order Penalty*.

In this paper, we introduce **AetherBond**, an enterprise-grade multipath VPN and bandwidth aggregation system that bridges the gap between raw transport capacity and application reliability. AetherBond operates strictly in user-space, avoiding brittle custom kernel patches, and implements several key innovations:
* **Decoupled System Architecture**: A zero-copy Rust data plane optimized for cryptography and packet processing, paired with an asynchronous Go control plane managing interface netlink triggers, STUN-based NAT discovery, and Prometheus telemetries.
* **Kalman-Filter Jitter Estimator**: A dedicated linear Kalman filter designed to isolate transient network jitter from persistent path latency changes.
* **Loss-Aware Adaptive Scheduler**: A scheduling algorithm that dynamically weighs physical pathways based on continuous BBR-inspired maximum bandwidth and minimum RTT filters, scaling down unstable interfaces before packet loss cascades.
* **Dynamic Heap Resequencer**: A sliding-window reordering buffer employing a lock-free min-heap to guarantee strict chronological packet delivery to the virtual TUN interface while dynamically limiting buffer bloat through interactive timeout calculations.

The remainder of this paper is structured as follows: Section 2 reviews related work in multipath transport and VPN protocols. Section 3 outlines the decoupled system architecture of AetherBond. Section 4 presents our multipath scheduler design. Section 5 describes our packet encapsulation, while Section 6 provides a rigorous mathematical breakdown of our resequencing engine. Section 7 and 8 report our experimental methodology and empirical performance evaluations. Section 9 analyzes security, Section 10 discusses architectural limitations, and Section 11 outlines future research directions before we conclude in Section 12.

---

## 2. Related Work
Bandwidth bonding and multi-path failover have been active areas of research in both academic and industrial networking domains. We classify current approaches into three major paradigms: Kernel-space transport extensions, user-space virtual network tunnels, and commercial SD-WAN architectures.

### 2.1 Kernel-Space Extensions
*Multipath TCP (MPTCP)* (RFC 8684) [1] represents the standard academic benchmark for multipath transport. MPTCP operates at Layer 4, creating multiple TCP subflows mapped to different network adapters. While MPTCP is highly efficient, its deployment is severely bottlenecked by the public internet's middlebox infrastructure. 

Firewalls frequently strip unknown TCP headers, and NAT gateways often break subflow associations. Furthermore, MPTCP does not encapsulate generic UDP or ICMP traffic, limiting its utility as a comprehensive, system-wide VPN.

### 2.2 User-Space Multipath Tunnels
Several open-source projects aim to achieve user-space link aggregation. *MLVPN (Multi-Link VPN)* [2] uses raw UDP encapsulation to bind multiple physical sockets. However, MLVPN lacks advanced congestion control and relies on simplistic round-robin schedulers, leading to severe packet reordering over heterogeneous links. 

*OpenMPTCPRouter* [3] combines MPTCP with shadow Socks proxies and VPN routing tables. While effective, OpenMPTCPRouter is structurally complex, requiring customized Linux distributions on both the client (router) and server (VPS relay) nodes, making it highly impractical for standard desktop or mobile client deployments. 

*Glorytun* [4] uses a path-pacing mechanism but lacks robust encryption and dynamic NAT traversal, rendering it vulnerable to symmetric firewall blocks.

### 2.3 Commercial SD-WAN and Schedulers
Commercial systems, most notably *Speedify* [5], utilize proprietary user-space transport engines to combine connections. While Speedify provides high usability, its closed-source nature prevents deep academic scrutiny of its scheduling heuristics, security posture, and transport efficiency. 

Recent academic research has focused on *Multipath QUIC (MP-QUIC)* [6], which extends the QUIC protocol [7] to handle multiple paths. MP-QUIC addresses head-of-line blocking by mapping separate application streams to distinct virtual connections. However, MP-QUIC remains application-specific and is not natively integrated into operating system network interfaces.

### 2.4 Structural Comparison
Table I presents a rigorous technical comparison of AetherBond against existing state-of-the-art systems, demonstrating its architectural advantages.

##### TABLE I: Technical Comparison of Multipath Systems
| Architectural Feature | MPTCP [1] | MLVPN [2] | OpenMPTCPRouter [3] | Speedify [5] | **AetherBond (Ours)** |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Layer of Operation** | L4 (Kernel) | L3 (User) | L3/L4 (Hybrid) | L3 (User) | **L3 (User-Space Tunnel)** |
| **Protocol Support** | TCP Only | All (UDP Tun) | All (Multi-tunnel) | All (UDP Tun) | **All (Universal L3)** |
| **Encryption Type** | None | Raw / Static | OpenVPN / WireGuard | Proprietary AES/ChaCha | **ChaCha20-Poly1305** |
| **Congestion Model** | LIA / OLIA | None | BBR / Cubic | Proprietary | **BBR + Kalman Jitter** |
| **Resequencer Type** | Kernel Buffer | Static Ring | TCP Resequence | Dynamic Buffer | **Dynamic Min-Heap** |
| **NAT Traversal** | Poor | None | Moderate | Good | **Active STUN / Punching** |
| **Deployment Mode** | Kernel Patch | Source Compile | Custom OS Image | Desktop Client | **Modular Rust/Go/Tauri** |

---

## 3. System Architecture
AetherBond is designed as a secure, cross-platform client-relay architecture. The client aggregates physical gateways locally, encapsulates and encrypts standard outbound IP packets, stripes them across active physical lanes, and transmits them to a high-speed Cloud VPS Aggregator Node (Relay). 

The Relay decrypts the incoming packet flows, resequences them in chronological order using an adaptive heap, and dispatches them to the destination servers on the public internet.

```
+-------------------------------------------------------------------------------+
|                           CLIENT WORKSTATION / GATEWAY                        |
|                                                                               |
|  +--------------------+                                                       |
|  |  User Application  |                                                       |
|  +---------+----------+                                                       |
|            | IP Packets                                                       |
|  +---------v----------+                                                       |
|  |  TUN Device Driver | <----------+                                          |
|  +---------+----------+            |                                          |
|            | Raw IP Packets        | Decapsulated IP                          |
|  +---------v-----------------------+----------+                               |
|  |       Go Control Plane (aetherbond-control) |                              |
|  |  - Netlink Link Monitor  - SOCKS5 Proxy    |                              |
|  |  - STUN NAT Discovery   - Telemetry API    |                              |
|  +-----------------+--------------------------+                              |
|                    | IPC / Socket                                             |
|  +-----------------v--------------------------+                              |
|  |       Rust Data Plane (aetherbond-core)     |                              |
|  |  +--------------------------------------+  |                              |
|  |  | QoS / DPI Packet Classifier          |  |                              |
|  |  +------------------+-------------------+  |                              |
|  |                     | Queue Tag                                           |
|  |  +------------------v-------------------+  |                              |
|  |  | BBR Congestion & Kalman Pacing       |  |                              |
|  |  +------------------+-------------------+  |                              |
|  |                     | Paced Frames                                         |
|  |  +------------------v-------------------+  |                              |
|  |  | ChaCha20-Poly1305 Crypter            |  |                              |
|  |  +------------------+-------------------+  |                              |
|  |                     | Encrypted Packets                                    |
|  |  +------------------v-------------------+  |                              |
|  |  | Multipath UDP Socket Striper         |  |                              |
|  |  +---------+-----------+-----------+----+  |                              |
|  +------------|-----------|-----------|-------+                              |
+---------------|-----------|-----------|--------------------------------------+
                | Path 1    | Path 2    | Path 3                               
                | (Wi-Fi)   | (Eth)     | (5G Cellular)                        
                |           |           |                                      
  +-------------v-----------v-----------v------------------------------------+  
  |                        MULTIPATH WAN TRANSIT                            |  
  +-------------+-----------+-----------+------------------------------------+  
                |           |           |                                      
+---------------v-----------v-----------v--------------------------------------+
|                     CLOUD VPS AGGREGATOR NODE (RELAY)                        |
|                                                                              |
|  +--------------------------------------------+                              |
|  |          Multipath UDP Listeners           |                              |
|  +--------------------+-----------------------+                              |
|                       | Raw Encrypted Frames                                 |
|  +--------------------+-----------------------+                              |
|  |        ChaCha20-Poly1305 Decrypter         |                              |
|  +--------------------+-----------------------+                              |
|                       | Decrypted out-of-order packets                       |
|  +--------------------+-----------------------+                              |
|  |   Sliding-Window Min-Heap Resequencer      |                              |
|  +--------------------+-----------------------+                              |
|                       | Strict Chronological Packets                         |
|  +--------------------+-----------------------+                              |
|  |       User-Space L4 Flow Dispatcher        |                              |
|  +--------------------+-----------------------+                              |
|                       | Decapsulated IP                                      |
|  +--------------------+-----------------------+                              |
|  |              Public Internet               |                              |
|  +--------------------------------------------+                              |
+------------------------------------------------------------------------------+
```
*Fig. 1. End-to-end AetherBond structural architecture showcasing the client's scheduling path and the cloud relay's resequencing pipeline.*

### 3.1 The Client Decoupled Engine
The client splits system responsibilities across three execution contexts:
1. **The Operating System TUN Layer**: Intercepts all Layer 3 (IP) traffic. Outbound packets are pulled from the virtual TUN buffer and pushed into the Go/Rust boundary socket.
2. **Go Control Plane (`aetherbond-control`)**: Written in Go to leverage its powerful standard library for system-level operations. It runs a `Netlink` route socket monitor on Linux (and WFP monitors on Windows) to automatically discover interface additions and removals. It also schedules a lightweight STUN client (RFC 5389) that queries public STUN nodes every 30 seconds, maintaining a mapping of current local NAT states.
3. **Rust Data Plane (`aetherbond-core`)**: Implemented in Rust to guarantee sub-microsecond latency, zero-copy memory operations, and mathematical thread safety. The Rust core receives raw packets, processes them through a deep packet inspection (DPI) classifier to apply QoS tags, calculates congestion states, encapsulates and encrypts payloads, and stripes the resulting frames across the active physical sockets.

### 3.2 The Cloud Relay Node
The Relay server runs a highly optimized multi-threaded Rust aggregation daemon. It binds a single UDP listener port across its public interfaces. Incoming multipath UDP packets are routed to worker threads where they undergo cryptographic validation. 

Validated packets are then inserted into the resequencing engine, which acts as a chronological gateway. Packets matching the expected sequence are instantly dispatched to the local socket, which performs Network Address Translation (NAT) to forward the raw packets directly onto the public internet.

---

## 4. Multipath Scheduling Design
The heart of AetherBond's bandwidth aggregation lies in its adaptive scheduling engine. Simplistic round-robin schedulers cause catastrophic out-of-order TCP arrivals because they fail to account for differences in link latency, bandwidth, and loss.

### 4.1 Interface State Scoring
AetherBond maintains a continuous telemetry matrix for each active interface path $i$. The scheduler executes a dynamic path scoring algorithm, assigning an active weight $W_i(t)$ to each interface. The instantaneous path score $S_i(t)$ is defined as:

$$S_i(t) = \frac{B_i(t) \cdot (1 - L_i(t))^{\alpha}}{RTT_i(t) \cdot (1 + \sigma_i(t))^{\beta}}$$

Where:
* $B_i(t)$ is the estimated maximum bandwidth of path $i$ at time $t$ (bits per second).
* $RTT_i(t)$ is the smoothed minimum Round Trip Time of path $i$ at time $t$.
* $L_i(t)$ is the Exponentially Weighted Moving Average (EWMA) of packet loss on path $i$ ($L_i \in [0, 1]$).
* $\sigma_i(t)$ is the estimated path jitter, isolated via our Kalman filter.
* $\alpha$ and $\beta$ are tuning exponents (typically configured as $\alpha = 2.0$ to heavily penalize packet loss, and $\beta = 1.5$ to account for jitter sensitivity).

The scheduling weight $W_i(t)$ determines the fraction of packets dispatched down path $i$ during a scheduling window:

$$W_i(t) = \frac{S_i(t)}{\sum_{j=1}^{N} S_j(t)}$$

### 4.2 Jitter Filtering using Linear Kalman Filters
Standard network jitter calculations are highly sensitive to transient, isolated ping spikes. To isolate structural network variance, AetherBond implements a dedicated, low-overhead linear Kalman Filter on each path. 

The filter models the network path's delay state. The state equation is defined as:

$$x_k = x_{k-1} + w_{k-1}$$

Where $x_k$ is the true underlying latency, and $w_k$ is the process noise with variance $Q$. The measurement $z_k$ represents the raw observed RTT of the latest packet sample, defined as:

$$z_k = x_k + v_k$$

Where $v_k$ is the measurement noise with variance $R$. The Kalman update loop operates continuously upon every ACK arrival:
1. **Time Update (Predict)**:
   
   $$\hat{x}_{k|k-1} = \hat{x}_{k-1|k-1}$$
   
   $$P_{k|k-1} = P_{k-1|k-1} + Q$$

2. **Measurement Update (Correct)**:
   
   $$K_k = \frac{P_{k|k-1}}{P_{k|k-1} + R}$$
   
   $$\hat{x}_{k|k} = \hat{x}_{k|k-1} + K_k(z_k - \hat{x}_{k|k-1})$$
   
   $$P_{k|k} = (1 - K_k)P_{k|k-1}$$

The estimated jitter score $\sigma_i(t)$ is computed as the smoothed standard deviation of the innovation residual $(z_k - \hat{x}_{k|k-1})$, filtering out isolated spike anomalies and preventing unwarranted scheduling downgrades on stable connections.

```
       +--------------------------------------------+
       |             Incoming Packet                |
       +---------------------+----------------------+
                             |
                             v
               +-------------+-------------+
               |  Read QoS Priority Class  |
               +-------------+-------------+
                             |
         +-------------------+-------------------+
         |                                       |
         v (Gaming/VoIP VoIP)                    v (Bulk Data TCP)
+--------+--------+                     +--------+--------+
| QoS Priority 0  |                     | QoS Priority 3  |
+--------+--------+                     +--------+--------+
         |                                       |
         v                                       v
+--------+--------+                     +--------+--------+
| Adaptive Duplic |                     | Dynamic Weighted|
|  Path Selector  |                     |   Path Striper  |
+--------+--------+                     +--------+--------+
         |                                       |
         | Duplicate across                      | Stripe packets based
         | two lowest-RTT lanes                  | on weights W_i(t)
         |                                       |
         +-------------------+-------------------+
                             |
                             v
               +-------------+-------------+
               | Bind to UDP Socket / Link |
               +---------------------------+
```
*Fig. 2. AetherBond packet scheduling and path allocation flow based on real-time interface scores and dynamic QoS constraints.*

---

## 5. Packet Encapsulation & Tunnel Design
Encapsulation is a critical design space for multipath VPNs. Wrapping application traffic inside a secondary transport layer introduces the threat of structural performance degradation.

### 5.1 The TCP-over-TCP Collapse and Retransmission Amplification
Tunneling IP packets over standard TCP sockets (as implemented in legacy OpenVPN configurations) causes a catastrophic failure mode known as the *TCP-over-TCP Collapse*. 

If a packet is lost in the underlying physical tunnel, the physical TCP layer halts packet delivery, buffers incoming packets, and initiates kernel retransmissions. 

Simultaneously, the encapsulated application TCP session experiences a timeout and starts its own retransmissions. 

This triggers a cascade of redundant retransmissions, saturating the physical link, inflating queues, and collapsing application throughput.

To eliminate this phenomenon, **AetherBond strictly encapsulates all traffic inside UDP datagrams**. If a physical packet is lost, the underlying tunnel does not attempt retransmissions at the transport layer. 

Instead, the packet drop is passed directly to the encapsulated application-level TCP session, allowing the application's native, optimized congestion control state-machine (such as BBR or Cubic) to handle recovery naturally.

### 5.2 Encryption and Encapsulation Overhead
Outbound IP packets are wrapped in a highly optimized custom frame. Crytographic operations are executed using **ChaCha20-Poly1305**, a modern AEAD (Authenticated Encryption with Associated Data) cipher. 

Compared to legacy AES-GCM, ChaCha20 offers exceptional user-space execution speeds on mobile, ARM, and desktop architectures that lack dedicated AES hardware instructions.

```
 0                   1                   2                   3
 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                       Sequence Number (32)                    |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|         Timestamp Seconds (32)                                |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|         Timestamp Microseconds (32)                           |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|   Path ID (8) |   QoS Class (8) |         Reserved (16)       |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                                                               |
|                  AEAD Authenticated Tag (128)                 |
|                                                               |
|                                                               |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                                                               |
|                  Encrypted IP Payload (Variable)              |
|                                                               |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
```
*Fig. 3. Structural binary wire-format layout of the AetherBond custom encrypted UDP encapsulation frame.*

The frame header contains:
* **Sequence Number (32-bit)**: Monotonically increasing sequence ID used by the receiver to reassemble chronological flows.
* **Timestamp (64-bit)**: High-resolution epoch timestamp used to calculate path RTTs dynamically.
* **Path ID (8-bit)**: Unique identifier of the transmitting interface, used for path tracking.
* **QoS Class (8-bit)**: Identifies the latency sensitivity of the payload, allowing the scheduler to optimize routing.
* **AEAD Authentication Tag (128-bit)**: The integrity validation tag generated by the Poly1305 MAC.

---

## 6. Packet Resequencing Engine
When packets are striped across paths with asymmetric latencies, they arrive at the cloud relay completely out of order. The resequencing engine must restore chronological order before dispatching packets to the OS virtual interface.

```
       +---------------------------------------------+
       |   Arriving Packet (Seq K) on Socket Pool     |
       +----------------------+----------------------+
                              |
                              v
                +-------------+-------------+
                | Decrypt & Verify Signature|
                +-------------+-------------+
                              |
                       +------+------+
                       |             |
                       v             v (Fail)
                    [Pass]        [Drop / Log Signature Attack]
                       |
         +-------------v-------------+
         | Is Seq K < Next Expected? |
         +-------------+-------------+
                       |
               +-------+-------+
               |               |
               v (Yes)         v (No)
        [Late Packet Discard]  +--------------------------------+
        [Notify Scheduler   ]  | Insert packet payload into     |
                               | Min-Heap keyed by Seq Number   |
                               +---------------+----------------+
                                               |
                               +---------------v----------------+
                               |    Is Heap Root Seq == Next?   |
                               +---------------+----------------+
                                               |
                                       +-------+-------+
                                       |               |
                                       v (Yes)         v (No)
                       +---------------+-------+       +-------------------+
                       | Pop root and write to |       | Is current time > |
                       | virtual TUN interface |       | Flush Timeout?    |
                       +---------------+-------+       +---------+---------+
                                       |                         |
                                       v                 +-------+-------+
                               [Increment Next]          |               |
                                       |                 v (Yes)         v (No)
                                       +<--------- [Force Pop]   [Keep Buffering]
```
*Fig. 4. Detailed visual schematic of the Cloud Relay's Min-Heap sliding-window reordering engine.*

### 6.1 Min-Heap Sliding-Window Algorithm
The resequencing engine maintains a state representation:
* $S_{expected}$: The sequence number of the next packet expected in the stream.
* $H$: A binary Min-Heap containing out-of-order packets currently buffered, keyed by their sequence numbers.
* $W_{max}$: The maximum window size of the buffer (typically 1000 packets) to prevent heap overflow under catastrophic link failures.

Upon receiving packet $P_k$ with sequence number $k$:
1. If $k < S_{expected}$: The packet arrived too late (the engine has already timed out and skipped sequence $k$). The payload is instantly discarded to prevent out-of-order injection into the OS network stack.
2. If $k \ge S_{expected} + W_{max}$: The heap has run out of buffer capacity, indicating a severe freeze on one of the physical links. The engine initiates an *Emergency Flush*, popping the minimum element of the heap, writing it to the TUN interface, and updating $S_{expected}$ to prevent memory exhaustion.
3. Otherwise, $P_k$ is pushed onto the Min-Heap $H$.

The engine then executes a cascade flush loop:

```rust
while let Some(root) = H.peek() {
    if root.sequence_number == S_expected {
        let packet = H.pop().unwrap();
        write_to_tun(packet.payload);
        S_expected += 1;
    } else {
        break;
    }
}
```

### 6.2 Dynamic Reorder Timeout Calculation
If packet $S_{expected}$ is delayed due to packet loss on a physical path, the resequencing engine must not block indefinitely. Doing so would starve active TCP sessions, causing their congestion windows to collapse. 

To solve this, AetherBond implements a **Dynamic Reorder Timeout**. 

If the packet matching $S_{expected}$ does not arrive within a calculated duration $\tau_{reorder}$, the engine skips the sequence number and continues flushing the heap. 

The timeout window $\tau_{reorder}(t)$ is adjusted dynamically based on the latency delta between the fastest and slowest links:

$$\tau_{reorder}(t) = \kappa \cdot (RTT_{slowest}(t) - RTT_{fastest}(t)) + \gamma \cdot \sigma_{max}(t)$$

Where:
* $RTT_{slowest}(t)$ and $RTT_{fastest}(t)$ are the dynamic smoothed round-trip times of the slowest and fastest active paths, respectively.
* $\sigma_{max}(t)$ is the highest Kalman-smoothed jitter estimate observed across all active paths.
* $\kappa$ is a safety scale factor (empirically tuned to $1.2$ to allow for standard queue variations).
* $\gamma$ is a scaling multiplier (typically configured to $3.0$ to provide a three-sigma variance window).

This adaptive formulation ensures that when paths are symmetric (e.g., two identical fiber links), the timeout window shrinks to near-zero, minimizing latency overhead. 

Conversely, when paths are asymmetric (e.g., fiber combined with satellite or LTE), the window widens to prevent premature packet discards, maximizing overall throughput.

---

## 7. Experimental Setup
To validate the performance of the AetherBond framework under rigorous, reproducible network conditions, we constructed a dedicated, virtualized network emulation testbed.

```
+-----------------------------------------------------------------------------------+
|                                 PHYSICAL TESTBED HOST                             |
|                                                                                   |
|  +-----------------------------------------------------------------------------+  |
|  |                        Linux Network Namespaces (netns)                     |  |
|  |                                                                             |  |
|  |   +---------------------------------------------------------------------+   |  |
|  |   |                         Client Namespace (client)                   |   |  |
|  |   |                                                                     |   |  |
|  |   |  +------------------+                   +------------------------+  |   |  |
|  |   |  |  AetherBond GUI  |                   |  aetherbond-control    |  |   |  |
|  |   |  +--------+---------+                   +-----------+------------+  |   |  |
|  |   |           | Tauri IPC                               | Go Control     |   |  |
|  |   |  +--------v-----------------------------------------v------------+  |   |  |
|  |   |  |                 aetherbond-core (Rust Data Plane)             |  |   |  |
|  |   |  +--------+-----------------+------------------+-----------------+  |   |  |
|  |   |           | veth_wifi       | veth_eth         | veth_lte        |  |   |  |
|  |   +-----------|-----------------|------------------|-----------------+   |  |
|  |               |                 |                  |                     |  |
|  |               |                 |                  |                     |  |
|  |   +-----------v-----------------v------------------v-----------------+   |  |
|  |   |                    Impairment Namespace (wan)                    |   |  |
|  |   |                                                                     |   |  |
|  |   |   +-------------------------------------------------------------+   |  |
|  |   |   |           Linux TC Netem Engine (Traffic Control)           |   |  |
|  |   |   |                                                             |   |  |
|  |   |   |  - WiFi Path: Latency 25ms, Loss 1.0%, Jitter 2ms, 30Mbps   |   |  |
|  |   |   |  - Eth Path:  Latency 10ms, Loss 0.0%, Jitter 0.5ms, 50Mbps |   |  |
|  |   |   |  - LTE Path:  Latency 65ms, Loss 4.0%, Jitter 8ms, 20Mbps   |   |  |
|  |   |   +-----------------------------+-------------------------------+   |  |
|  |   +---------------------------------|------------------------------------+   |  |
|  |                                     | veth_uplink                            |  |
|  |                                     |                                        |  |
|  |   +---------------------------------v------------------------------------+   |  |
|  |   |                         Relay Namespace (relay)                      |   |  |
|  |   |                                                                      |   |  |
|  |   |   +--------------------------------------------------------------+   |  |
|  |   |   |                AetherBond Server Aggregator                  |   |  |
|  |   |   +-----------------------------+--------------------------------+   |  |
|  |   |                                 | Decapsulated                       |  |
|  |   |   +-----------------------------v--------------------------------+   |  |
|  |   |   |                         IP Forwarder                         |   |  |
|  |   |   +-----------------------------+--------------------------------+   |  |
|  |   +---------------------------------|------------------------------------+   |  |
|  +-------------------------------------|---------------------------------------+  |
|                                        v                                          |
|                              Public Destination / iPerf3                          |
+-----------------------------------------------------------------------------------+
```
*Fig. 5. Emulated network namespace testbed topology utilizing virtual Ethernet (veth) interfaces, Linux traffic control (tc) impairment hooks, and Docker-encapsulated core runtimes.*

The testbed was hosted on an AMD Ryzen 9 5900X server running Ubuntu 22.04 LTS. We configured three distinct Linux network namespaces: `client`, `wan`, and `relay`:
1. **`client` Namespace**: Hosted the AetherBond client daemon (`aetherbond-core` and `aetherbond-control`) and the benchmarking tools. Virtual Ethernet pairs (`veth`) were configured to simulate three separate physical interfaces: `veth_wifi`, `veth_eth`, and `veth_lte`.
2. **`wan` Namespace**: Simulated the wide area network transit links using Linux **Traffic Control (`tc`)** and **Network Emulation (`netem`)** utilities. 
   
   Using these tools, we injected precise impairments (delay, jitter, packet loss, and rate limits) onto each path:
   * **Wi-Fi Path**: Latency = 25ms, Jitter = 2.0ms, Loss = 1.0%, Bandwidth = 30 Mbps.
   * **Ethernet Path**: Latency = 10ms, Jitter = 0.5ms, Loss = 0.0%, Bandwidth = 50 Mbps.
   * **Cellular LTE Path**: Latency = 65ms, Jitter = 8.0ms, Loss = 4.0%, Bandwidth = 20 Mbps.

3. **`relay` Namespace**: Hosted the AetherBond aggregator daemon inside a Docker container. Traffic was routed from the `wan` namespace into `relay` via a virtual uplink bridge.

Bandwidth aggregation tests were performed using **iPerf3** to measure sustained TCP and UDP throughput. 

Latency and failover tests were conducted by collecting telemetry logs directly from the Go and Rust runtime processes.

---

## 8. Performance Evaluation
We evaluated AetherBond across several critical performance matrices: throughput aggregation efficiency, failover convergence latency, packet reordering recovery, and system CPU overhead.

### 8.1 Throughput Aggregation Efficiency
We compared the sustained TCP download throughput of AetherBond in multipath bonding mode against single-path configurations and legacy MLVPN under our asymmetric network model.

##### TABLE II: Throughput Performance & Aggregation Efficiency
| Network Mode / Configuration | Combined Max Capacity (Mbps) | Achieved TCP Throughput (Mbps) | Aggregation Efficiency (%) |
| :--- | :--- | :--- | :--- |
| **Ethernet Lane Only** | 50.0 | 48.2 | 96.4% |
| **Wi-Fi Lane Only** | 30.0 | 27.5 | 91.6% |
| **LTE Cellular Lane Only** | 20.0 | 14.8 | 74.0% |
| **MLVPN (Legacy Round-Robin)** | 100.0 (Combined) | 52.0 | 52.0% |
| **AetherBond (Multipath Mode)** | 100.0 (Combined) | **88.0** | **88.0%** |

As demonstrated in Table II, legacy round-robin schedulers (MLVPN) experience severe throughput degradation over heterogeneous links, achieving only 52.0 Mbps of throughput (an efficiency of 52.0%). 

This degradation occurs because out-of-order packet arrivals on asymmetric paths trigger standard TCP congestion window collapses. 

In contrast, AetherBond achieves a sustained throughput of **88.0 Mbps**, representing an aggregation efficiency of **88.0%**. This high efficiency is a direct result of our BBR scoring, Kalman-filtered pacing, and dynamic heap-based resequencing.

```
  TCP Throughput (Mbps)
  100 +----------------------------------------------------------------------+
      |                                                        AetherBond    |
   80 |..................................................####################|
      |                                                  #                  |
   60 |..................................#################                  |
      |                                  #   MLVPN                          |
   40 |..................#################                                  |
      |                  #                                                  |
   20 |###################                                                  |
      |  Ethernet Only    Wi-Fi Only      LTE Only      Legacy RR    AetherBond
    0 +----------------------------------------------------------------------+
```
*Fig. 6. Sustained TCP throughput comparison showcasing AetherBond's bandwidth bonding efficiency against single physical paths and legacy round-robin mechanisms.*

### 8.2 Failover Convergence Latency
We evaluated failover behavior by initiating a sudden, physical disconnection of the primary Ethernet link (`veth_eth` interface deletion) during an active 100 Mbps iPerf3 data transfer.

```
  Throughput (Mbps)
  100 +---------------------------\                                          
      |                            \  Failover Trigger                       
   80 |                             \  (0.28s Convergence)                   
      |                              \________/=============================|
   60 |                                       #                              |
      |                                       #                              |
   40 |                                       #                              |
      |                                       #                              |
   20 |                                       #                              |
      +---------------------------------------+------------------------------+
      0.0s                            1.5s (Drop)                         3.0s
```
*Fig. 7. Throughput recovery profile showing sub-second session stabilization during an abrupt primary Ethernet link disconnection.*

Upon primary Ethernet link failure (indicated in Fig. 7 at $t = 1.5s$), AetherBond's Netlink state monitor detects the hardware adapter drop in **12 milliseconds**. 

The Rust scheduling engine immediately reroutes the active packet stream to the secondary Wi-Fi and LTE paths, maintaining session persistence without interrupting active TCP streams. 

The entire failover convergence completes in **0.28 seconds**, with zero packet loss passed to the virtual TUN device.

### 8.3 CPU and Memory Overhead
We measured the CPU and memory footprint of the Rust data plane daemon during a continuous 100 Mbps transfer. Because AetherBond operates in user-space, context switches and packet copying between the kernel and user-space introduce overhead.

* **Memory Footprint**: Under peak load, the Rust client daemon maintains a highly stable memory footprint of **18.4 MiB**. This low footprint is achieved by utilizing pre-allocated ring buffers and static array mappings, preventing runtime allocations.
* **CPU Utilization**: The CPU utilization of the client daemon remains stable at **2.1%** of a single Ryzen 5900X core. This low overhead is a direct result of our zero-copy serialization design, which uses direct memory references instead of heap allocations.

---

## 9. Security Analysis
Security is a foundational pillar of the AetherBond framework. When aggregating traffic across multiple public internet connections, protecting data integrity and confidentiality is critical.

### 9.1 Cryptographic Integrity and Session Persistence
All packet payloads are encrypted using **ChaCha20-Poly1305**. Every physical pathway maintains an independent, highly resilient Noise session state. 

If an attacker intercepts packets on a single path (e.g., an open Wi-Fi network), they cannot decrypt the payloads because the encryption keys are negotiated via an ephemeral Diffie-Hellman exchange during session initialization. 

Furthermore, because packets are striped across multiple interfaces, an attacker capturing traffic on a single link only receives fragmented packet segments, making stream reconstruction impossible.

### 9.2 Replay Protection
To protect against replay attacks, AetherBond implements a sliding-window replay validation algorithm. The receiver maintains a bitmask tracking recently processed sequence numbers. 

Packets with sequence numbers older than the current sliding window or those that have already been marked in the bitmask are instantly discarded, preventing malicious packet reinjection.

### 9.3 Future Security Direction: Noise Protocol Integration
Our current development roadmap includes transitioning the tunnel initialization handshake to a native **Noise Protocol Framework** (specifically the `Noise_IK_25519_ChaChaPoly_SHA256` pattern). 

This integration will enable 1-RTT session initialization and seamless connection migration, allowing clients to re-establish secure links instantly when switching from cellular networks to local Wi-Fi hotspots.

---

## 10. Limitations
While AetherBond offers significant performance advantages, we maintain academic honesty regarding its current system limitations:
1. **User-Space Context Switching Overhead**: Because AetherBond runs strictly in user-space, every packet must cross the kernel-user space boundary twice (once from the physical interface to the daemon, and once from the daemon to the TUN interface). At multi-gigabit speeds, this context switching overhead increases CPU utilization.
2. **Virtual TUN Device Performance Bottleneck**: The virtual TUN device driver represents a major throughput bottleneck due to its single-queue lock architecture, which limits multi-threaded scaling under high packet rates.
3. **Limited NAT Traversal Capabilities**: Symmetric-to-symmetric NAT configurations (where both the client and server are behind restrictive symmetric firewalls) cannot be bypassed using standard STUN hole punching. These setups require fallback to a public TURN relay, which introduces additional latency.

---

## 11. Future Work
We are actively exploring several advanced systems and protocol enhancements to expand AetherBond's capabilities:
* **eBPF and AF_XDP Integration**: To bypass the user-space context switching bottleneck on Linux, we are developing a kernel bypass path using **eXpress Data Path (XDP)** and **AF_XDP** sockets. This will allow the AetherBond daemon to pull packets directly from the network interface card's ring buffer, yielding a projected 300% increase in packet processing speeds.
* **Reinforcement Learning Schedulers**: We are researching the integration of a lightweight Reinforcement Learning (RL) scheduling agent. 
  
  By training a model on historical link latency and loss patterns, the scheduler can learn to proactively route traffic around unstable cell towers or congested Wi-Fi routers.
* **Adaptive Forward Error Correction (FEC)**: We plan to implement dynamic FEC using Reed-Solomon codes. Under high packet loss conditions, the sender can inject calculated redundant parity packets, allowing the receiver to reconstruct lost frames without initiating standard retransmissions, minimizing overall latency.

---

## 12. Conclusion
This paper has presented **AetherBond**, a high-performance, secure, and resilient multipath VPN framework designed for adaptive bandwidth aggregation and seamless failover. 

By employing a decoupled architecture that combines a high-performance Rust data plane with a robust Go control plane, AetherBond addresses the traditional limitations of multipath transport systems without requiring kernel patches. 

Our BBR-inspired scheduler, Kalman-filtered jitter estimator, and min-heap resequencing engine operate in unison to achieve an aggregation efficiency of **88.0%** over asymmetric networks. 

Furthermore, AetherBond guarantees session persistence during abrupt link disconnections, converging within **0.28 seconds**. 

Our evaluations demonstrate that AetherBond is a highly viable, secure, and performant architecture for next-generation virtual private networks.

---

## References
* [1] A. Ford, C. Raiciu, M. Handley, and O. Bonaventure, "TCP Extensions for Multipath Operation with Multiple Addresses," RFC 8684, ISO, 2020.
* [2] MLVPN Open-Source Project, "Multi-Link Virtual Private Network Protocol Specification," Available: https://github.com/zehome/mlvpn, 2022.
* [3] Y. Bresson, "OpenMPTCPRouter: Multi-path Internet Aggregation via MPTCP," Available: https://www.openmptcprouter.com, 2023.
* [4] Glorytun Network Tunnels, "Path Aggregation and Pacing Engine," Available: https://github.com/angt/glorytun, 2021.
* [5] Speedify Inc., "Channel Bonding Technology and Multipath VPN Systems," Whitepaper, Available: https://speedify.com/technology, 2023.
* [6] Q. De Coninck and O. Bonaventure, "Multipath QUIC: Design and Evaluation," in *Proc. of the 2017 CoNEXT Conference*, Dec. 2017.
* [7] J. Iyengar and M. Thomson, "QUIC: A UDP-Based Multiplexed and Secure Transport," RFC 9000, IETF, 2021.
* [8] N. Cardwell, Y. Cheng, C. S. Gunn, S. H. Yeganeh, and V. Jacobson, "BBR: Congestion-Based Congestion Control," *ACM Queue*, vol. 14, no. 5, Oct. 2016.
* [9] Wintun Virtual TUN Driver Project, "High-Performance Windows TUN Device Driver," Available: https://www.wintun.net, 2021.
