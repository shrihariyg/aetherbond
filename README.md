# AetherBond: High-Performance Multipath VPN & Bandwidth Aggregator

AetherBond is a highly reliable, scalable, and secure commercial-grade multipath VPN and bandwidth bonding platform. Inspired by systems like Speedify, Multipath TCP, and QUIC transport architectures, AetherBond combines heterogeneous network connections—such as Wi-Fi, Ethernet, and 4G/5G Cellular LTE—into a single high-throughput, low-latency logical transport lane.

AetherBond operates strictly in user-space, avoiding brittle custom kernel patches, and implements a decoupled multi-language architecture:
*   **Data Plane (`aetherbond-core`)**: Pre-allocated, zero-copy, highly optimized Rust library for packet serialization, WireGuard-grade ChaCha20-Poly1305 encryption, path pacing, and heap-based packet reassembly.
*   **Control Plane (`aetherbond-control`)**: Event-driven Go orchestrator managing dynamic adapter scanning (via Netlink), STUN NAT traversal (RFC 5389), Prometheus metrics, and JSON APIs.
*   **Desktop Interface (`aetherbond-gui`)**: Tauri desktop client presenting a glassmorphic dark-themed dashboard with real-time SVG canvas graphing.
*   **Scale Relay (`config/kubernetes`)**: Distributed aggregators packaged in Helm charts with automatic geo-latency routing and horizontal autoscaling.

---

## 🚀 Key Architectural Features

1.  **BBR-Inspired Congestion Scheduling**: Individual interfaces track Max Bandwidth and Min RTT filters, scaling down path allocations during loss spikes or latency rises rather than relying on simple Round-Robin routing.
2.  **Jitter Filtering via Kalman Filters**: A dedicated linear Kalman filter isolates transient network jitter from persistent queue buildup to prevent premature scheduling downgrades.
3.  **Dynamic Min-Heap Resequencer**: Solves the *TCP Out-of-Order Penalty* on the aggregator node using an adaptive flush timeout window:
    $$\tau_{reorder} = 1.2 \cdot (RTT_{slowest} - RTT_{fastest}) + 3.0 \cdot \text{Jitter}_{max}$$
4.  **Adaptive Redundancy Engine**: Real-time packet duplication for VoIP, Gaming, or critical ACK frames across paths, with a sliding Bloom Filter on the receiving end for zero-overhead duplicate eviction.
5.  **eBPF / AF_XDP Bypass Loader**: Linux kernel-bypass driver support via the `aya` crate, with a highly optimized cross-platform raw socket fallback (allocating 2MB send/recv buffers) on non-Linux architectures.

---

## 📂 Repository Directory Structure

```
g:\Python\speedify_etan\
├── aetherbond-core/               # Rust High-Performance Data Plane
│   ├── Cargo.toml
│   └── src/
│       ├── congestion/            # BBR Congestion, Pacing Engine & Kalman Jitter filters
│       ├── crypto/                # ChaCha20-Poly1305 AEAD wire encryption
│       ├── redundancy/            # VoIP duplication scheduler & Sliding Bloom de-duplicator
│       ├── resequencing/          # Min-Heap sliding-window chronological packet buffer
│       ├── transport/             # Multi-stream QUIC multiplexers
│       ├── xdp/                   # Linux eBPF/XDP bypass loader & Raw Socket fallback
│       └── lib.rs                 # Core library definitions and integration unit tests
│
├── aetherbond-control/            # Go Control Plane & Orchestrator
│   ├── go.mod
│   ├── main.go                    # REST API, Prometheus exporter, and link daemon entry
│   └── pkg/
│       ├── interfaces/            # Netlink adapter monitor and cross-platform pollers
│       ├── nat/                   # STUN Client formulation and external mapping sweeps
│       └── telemetry/             # Multi-writer colorized logger & log persistence
│
├── aetherbond-chaos/              # Python Chaos Lab & Performance Benchmarks
│   ├── chaos_lab.py               # Linux network namespaces & tc netem injection script
│   └── benchmarks.py              # iPerf3 throughput and aggregation ratio comparator
│
├── aetherbond-gui/                # Tauri Desktop Application
│   ├── src-tauri/                 # Rust systems wrapper booting local UI
│   └── ui/                        # Glassmorphic HTML5/CSS3/JS asset files
│
├── config/                        # Cloud & Local Metrics Deployments
│   ├── grafana_dashboard.json     # Premium local metrics dashboard parameters
│   ├── prometheus.yml             # Scraping configurations mapping Go control pools
│   └── kubernetes/                # Autoscaling cluster aggregator Helm template charts
│
└── docs/                          # Academic & System Documentation
    ├── aetherbond_research_paper.md # IEEE-style systems research paper
    ├── user_manual.md             # Operations & installation manual
    └── handover.md                # Technical platforms handover guide
```

---

## 🛠️ Quick Start & Build Pipelines

### Prerequisites
Ensure the following toolchains are available on your system:
*   Rust Toolchain (`cargo` edition 2021)
*   Go Programming Language (`go` 1.18+)
*   Node Package Manager (`npm` and `node` for Tauri UI compilation)
*   Linux host with `iproute2` and `tc` packages (required only for Chaos Lab execution)

### 1. Build and Test the Rust Data Plane
```bash
cd aetherbond-core
cargo test
cargo build --release
```
The integration tests verify:
*   BBR metrics scoring and Kalman updates
*   Sliding Heap Resequencer packet ordering
*   ChaCha20-Poly1305 encryption epoch sequences
*   XDP raw loopback socket fallback packet transfers

### 2. Build the Go Control Plane
```bash
cd ../aetherbond-control
go build -o aetherbond-orchestrator main.go
./aetherbond-orchestrator
```
This boots the `/metrics` Prometheus exporter, the Netlink adapter monitors, and the STUN NAT scanner on `http://127.0.0.1:9100`.

### 3. Launch the Tauri Desktop UI
Ensure your orchestrator is running, then compile and launch the GUI frame:
```bash
cd ../aetherbond-gui
npm install
npm run tauri dev
```

### 4. Deploy Aggregator Relays (VPS Node)
For single-node VPS setups, launch the aggregator in host network mode:
```bash
docker-compose up -d
```
For high-capacity cloud clusters, deploy the Helm charts:
```bash
helm install aetherbond-relay ./config/kubernetes/aetherbond-relay --namespace aetherbond --create-namespace
```

---

## 🧪 Chaos Lab & Benchmarks

AetherBond features an automated testing framework to model extreme network scenarios. In your Linux emulation namespace environment, execute:

```bash
cd aetherbond-chaos
python chaos_lab.py
```
This runs a 5-stage simulation, injecting:
1.  **LTE Latency Spikes**: Spikes cellular RTT to 320ms and packet loss to 25%.
2.  **Wi-Fi Jitter Surges**: Elevates Wi-Fi jitter to 45ms (Kalman filter response verification).
3.  **Ethernet Bulk Congestion**: Increases Ethernet base latency from 10ms to 95ms (Queue bloat pacing verification).
4.  **Multi-Link Failover**: Disconnects primary Ethernet link (failover transition verification).
5.  **Out-of-Order Packet Injection**: Shuffles arrival sequences (resequencer verification).

---

## 📚 Complete Project Documentation

Refer to the generated guides in the `docs/` folder for in-depth explanations:
*   **Scholarly Research Paper**: [docs/aetherbond_research_paper.md](file:///g:/Python/speedify_etan/docs/aetherbond_research_paper.md) (covers academic design, BBR congestion pacing, Kalman mathematics, and benchmarking data).
*   **Operational Installation Manual**: [docs/user_manual.md](file:///g:/Python/speedify_etan/docs/user_manual.md) (detailed setup matrices, Wintun guides, eBPF permissions, and Prometheus dashboards).
*   **Systems Handover Guide**: [docs/handover.md](file:///g:/Python/speedify_etan/docs/handover.md) (comprehensive architectural notes, pipeline parameters, and future development roadmaps).
