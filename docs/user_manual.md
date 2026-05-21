# AetherBond Operations & End-User Manual

Welcome to **AetherBond**, the high-performance multipath VPN and bandwidth bonding platform. This manual provides comprehensive operational steps for installing, deploying, and troubleshooting the AetherBond Client desktop application and Cloud VPS Aggregator Nodes.

---

## 1. System Requirements

### Supported Client Operating Systems
*   **Linux**: Ubuntu 20.04 LTS or newer, Fedora 36 or newer (requires kernel 5.8+ for full eBPF/XDP bypass support).
*   **Windows**: Windows 10 or 11 (64-bit) (requires installation of the Wintun loopback driver).
*   **macOS**: macOS Monterey (12.0) or newer (Apple Silicon and Intel architectures).

### Supported Hardware Network Interfaces
AetherBond dynamically aggregates up to **8 physical adapters** concurrently:
*   Physical Gigabit / Multi-Gigabit Ethernet.
*   Internal or USB Wi-Fi Adapters (supporting 2.4 GHz, 5 GHz, and Wi-Fi 6 channels).
*   Cellular LTE / 5G dongles, USB modems, or tethered smartphones.
*   Virtual VPN interfaces and generic loopback adapters.

---

## 2. Client Deployment & Setup

### A. Windows Installation Steps
1.  **Download Wintun**:
    *   Download the official high-performance Wintun loopback driver from [Wintun.net](https://www.wintun.net/).
    *   Copy the `wintun.dll` file corresponding to your architecture into your System32 directory (`C:\Windows\System32\`) or place it directly in the same folder as the AetherBond client executable.
2.  **Compile & Launch Tauri GUI**:
    *   Ensure Node.js and Rust are installed.
    *   Navigate to the Tauri workspace and compile:
        ```bash
        cd aetherbond-gui
        npm install
        npm run tauri build
        ```
    *   The compiled executable will be generated at `src-tauri/target/release/aetherbond-gui.exe`.
3.  **Run with Administrator Privileges**:
    *   Right-click `aetherbond-gui.exe` and select **Run as Administrator** (required to initialize virtual interfaces, inject route policies, and alter system routing matrices).

### B. Linux Installation Steps
1.  **Set Net Admin Permissions**:
    *   Linux requires system capabilities to initialize raw network sockets and load eBPF/XDP drivers:
        ```bash
        sudo setcap cap_net_admin,cap_net_raw=eip ./aetherbond-orchestrator
        sudo setcap cap_net_admin,cap_net_raw=eip ./aetherbond-core
        ```
2.  **Boot the Go Control plane**:
    ```bash
    cd aetherbond-control
    go run main.go
    ```
3.  **Boot the Rust Data plane**:
    ```bash
    cd aetherbond-core
    cargo run --release
    ```

---

## 3. Server Deployment & VPS Relays

AetherBond aggregates traffic by routing packets through a public **Cloud VPS Aggregator Node (Relay)**. You can deploy this node using either Docker Compose or Kubernetes.

### Option A: Lightweight Docker Compose Deployment
Ensure Docker and Docker Compose are installed on your public VPS server. Save the following configuration as `docker-compose.yml` on the server:

```yaml
version: '3.8'

services:
  aetherbond-relay:
    image: aetherbond/aggregator:latest
    container_name: aetherbond-relay
    network_mode: "host"
    cap_add:
      - NET_ADMIN
      - SYS_ADMIN
    volumes:
      - /dev/net/tun:/dev/net/tun
      - ./config:/etc/aetherbond
    environment:
      - LISTEN_PORT=51820
      - BND_INTERFACE=eth0
    restart: always
```

Run the container:
```bash
docker-compose up -d
```

### Option B: High-Availability Kubernetes Relay Cluster
Deploy the AetherBond aggregator chart inside your Kubernetes cluster:

```bash
cd config/kubernetes/
helm install aetherbond-relay ./aetherbond-relay --namespace aetherbond --create-namespace
```

Verify that the aggregator pods are active and binding directly to host networks:
```bash
kubectl get pods -n aetherbond -o wide
```

---

## 4. Running & Operating AetherBond Client

### Real-Time Desktop UI Dashboard
Upon launching the Tauri GUI application, the **AetherBond Interface Manager** will initialize:

```
+--------------------------------------------------------------------------+
|  AetherBond Multipath Aggregator                                 16:24:00 |
+--------------------------------------------------------------------------+
|  AGGREGATED BANDWIDTH:  88.0 Mbps             LATENCY INDEX:  10.2 ms    |
+--------------------------------------------------------------------------+
|  Active Path Grid:                                                       |
|  [fa-ethernet]  Ethernet (eth0)     -  192.168.1.50   [ ACTIVE / 10ms  ] |
|  [fa-wifi]      Wi-Fi (wlan0)       -  192.168.10.12  [ ACTIVE / 25ms  ] |
|  [fa-signal]    Cellular LTE (lte0) -  10.45.122.9    [ ACTIVE / 65ms  ] |
+--------------------------------------------------------------------------+
|  Real-Time Path Latencies Map (RTT ms):                                 |
|  150 |                                                                   |
|      |                                                                   |
|      |                                                                   |
|    0 +-----------------------------------------------------------------+ |
+--------------------------------------------------------------------------+
|  [RUN NAT DIAGNOSTICS]                           [VIEW LOG CONSOLE]      |
+--------------------------------------------------------------------------+
```

*   **Aggregated Bandwidth Card**: Shows your current cumulative bonding speed in Megabits per second.
*   **Latency Index**: Reflects the real-time average latency calculated across your physical adapters.
*   **Dynamic Lane Matrices**: Check the individual status (UP/DOWN/OFFLINE) and IP address mapping for each interface.
*   **Run NAT Diagnostics**: Sends dynamic STUN binding requests (RFC 5389) across all interfaces to verify firewall configurations.

---

## 5. Telemetry & Observability Integration

AetherBond automatically exposes a Prometheus metrics scraper on the Go control plane at `http://127.0.0.1:9100/metrics`.

### Scraping Setup
Add the following scraping target to your local `prometheus.yml` server configurations:

```yaml
scrape_configs:
  - job_name: 'aetherbond-client'
    static_configs:
      - targets: ['127.0.0.1:9100']
```

### Premium Grafana Dashboards
Import the JSON config located at **[grafana_dashboard.json](file:///g:/Python/speedify_etan/config/grafana_dashboard.json)** into Grafana to monitor:
*   Real-time link status per adapter.
*   Kalman-smoothed path latency profiles.
*   Outbound packet pacing rates.
*   Aggregator resequencing buffer heap size.

---

## 6. Troubleshooting

### Problem 1: Tauri GUI shows "Orchestrator Unreachable"
*   **Root Cause**: The Go control plane (`aetherbond-control`) is not running, or is blocked by local firewall software.
*   **Solution**: 
    1. Verify that `aetherbond-orchestrator` is running in your task manager.
    2. Check that port `9100` is open on your system and not bound by another service:
       ```bash
       # Windows
       netstat -ano | findstr 9100
       # Linux
       ss -lntp | grep 9100
       ```

### Problem 2: Ethernet or LTE lane is marked "Offline"
*   **Root Cause**: The network adapter lacks a valid IPv4 gateway address or dynamic Netlink routing mappings.
*   **Solution**:
    1. Verify that the physical connection is active and has been assigned a dynamic DHCP IP address.
    2. Check the diagnostics file at `config/aetherbond-diagnostics.log` for any hardware device mapping warnings.

### Problem 3: Bandwidth drops when adding Wi-Fi or LTE to Ethernet
*   **Root Cause**: Path latency asymmetry is causing packet reordering that exceeds the capacity of the resequencing buffer.
*   **Solution**:
    1. Adjust your BBR buffer limits in `config/kubernetes/aetherbond-relay/values.yaml` or set the base timeout `baseTimeoutMs` to a higher value (e.g., `60ms`).
    2. Ensure that raw socket fallback pacing configurations are turned on inside your local configuration files.
