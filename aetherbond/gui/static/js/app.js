// AetherBond Client Dashboard App Logic
let socket = null;
let speedChart = null;
const maxChartDataPoints = 40;
let chartLabels = [];
let chartDatasets = {
    aggregate: [],
    wifi: [],
    ethernet: [],
    cellular: []
};

// Initialize Chart.js
function initChart() {
    const ctx = document.getElementById('speed-chart').getContext('2d');
    
    // Generate dummy initial labels
    for (let i = 0; i < maxChartDataPoints; i++) {
        chartLabels.push('');
        chartDatasets.aggregate.push(0);
        chartDatasets.wifi.push(0);
        chartDatasets.ethernet.push(0);
        chartDatasets.cellular.push(0);
    }

    speedChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: chartLabels,
            datasets: [
                {
                    label: 'Bonded Throughput',
                    data: chartDatasets.aggregate,
                    borderColor: '#00F2FE',
                    backgroundColor: 'rgba(0, 242, 254, 0.05)',
                    borderWidth: 3,
                    fill: true,
                    tension: 0.3,
                    pointRadius: 0
                },
                {
                    label: 'Sim_WiFi / Wi-Fi',
                    data: chartDatasets.wifi,
                    borderColor: '#FFC400',
                    borderWidth: 1.5,
                    borderDash: [4, 4],
                    fill: false,
                    tension: 0.3,
                    pointRadius: 0
                },
                {
                    label: 'Sim_Ethernet / Ethernet',
                    data: chartDatasets.ethernet,
                    borderColor: '#00E676',
                    borderWidth: 1.5,
                    borderDash: [4, 4],
                    fill: false,
                    tension: 0.3,
                    pointRadius: 0
                },
                {
                    label: 'Sim_Cellular / Mobile',
                    data: chartDatasets.cellular,
                    borderColor: '#FF1744',
                    borderWidth: 1.5,
                    borderDash: [4, 4],
                    fill: false,
                    tension: 0.3,
                    pointRadius: 0
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: 'top',
                    labels: {
                        color: '#94A3B8',
                        font: { family: 'Outfit', size: 11 }
                    }
                }
            },
            scales: {
                x: {
                    grid: { display: false },
                    ticks: { display: false }
                },
                y: {
                    grid: { color: 'rgba(255, 255, 255, 0.04)' },
                    ticks: {
                        color: '#94A3B8',
                        font: { family: 'Outfit', size: 10 }
                    },
                    title: {
                        display: true,
                        text: 'Speed (Mbps)',
                        color: '#94A3B8',
                        font: { family: 'Outfit', size: 11 }
                    },
                    suggestedMax: 20
                }
            }
        }
    });
}

// Update speed chart datasets
function updateChart(aggSpeed, wifiSpeed, ethSpeed, cellSpeed) {
    chartDatasets.aggregate.shift();
    chartDatasets.aggregate.push(aggSpeed);
    
    chartDatasets.wifi.shift();
    chartDatasets.wifi.push(wifiSpeed);

    chartDatasets.ethernet.shift();
    chartDatasets.ethernet.push(ethSpeed);

    chartDatasets.cellular.shift();
    chartDatasets.cellular.push(cellSpeed);
    
    // Adapt Y axis scale dynamically based on combined speed
    let maxVal = Math.max(...chartDatasets.aggregate, ...chartDatasets.wifi, ...chartDatasets.ethernet, ...chartDatasets.cellular);
    speedChart.options.scales.y.suggestedMax = Math.ceil((maxVal + 5) / 10) * 10;
    
    speedChart.update('none'); // Update without full animation for performance
}

// Connect WebSocket Telemetry
function connectWebSocket() {
    const wsUrl = `ws://${window.location.host}/ws`;
    socket = new WebSocket(wsUrl);

    socket.onopen = () => {
        console.log("WebSocket connected.");
    };

    socket.onmessage = (event) => {
        const data = JSON.parse(event.data);
        handleTelemetryUpdate(data);
    };

    socket.onclose = () => {
        console.log("WebSocket closed. Attempting reconnect in 2s...");
        setTimeout(connectWebSocket, 2000);
    };

    socket.onerror = (err) => {
        console.error("WebSocket error:", err);
        socket.close();
    };
}

// Process telemetry data from server
function handleTelemetryUpdate(data) {
    // 1. Update Mode tags
    const modeTag = document.getElementById('sys-mode-tag');
    if (data.simulator_enabled) {
        modeTag.className = 'badge-sim';
        modeTag.textContent = 'Simulator ON';
    } else {
        modeTag.className = 'badge-physical';
        modeTag.textContent = 'Physical ON';
    }

    // 2. Render Interface Grid
    renderInterfaces(data.interfaces, data.simulator_enabled);

    // 3. Extract speed values for charts
    let aggregateSpeed = 0.0;
    let wifiSpeed = 0.0;
    let ethSpeed = 0.0;
    let cellSpeed = 0.0;

    if (data.download && data.download.is_started) {
        aggregateSpeed = data.download.speed_mbps;
        
        // Populate individual interface speeds from downloader statistics
        const speeds = data.download.active_speeds || {};
        wifiSpeed = speeds["192.168.99.10"] || 0.0;
        ethSpeed = speeds["192.168.99.20"] || 0.0;
        cellSpeed = speeds["192.168.99.30"] || 0.0;
    }

    // Update top header speed indicator
    document.getElementById('top-speed').innerHTML = `${aggregateSpeed.toFixed(2)} <span class="unit">Mbps</span>`;

    // Push into Chart
    updateChart(aggregateSpeed, wifiSpeed, ethSpeed, cellSpeed);

    // 4. Handle Download session details
    if (data.download && data.download.is_started) {
        updateDownloadProgress(data.download);
    }
}

// Dynamically generate Interface Cards
function renderInterfaces(interfaces, simEnabled) {
    const container = document.getElementById('interfaces-container');
    container.innerHTML = '';

    interfaces.forEach(iface => {
        const card = document.createElement('div');
        card.className = `interface-card ${iface.is_online ? '' : 'offline'}`;
        
        const isChecked = iface.is_online ? 'checked' : '';
        const displayToggle = simEnabled ? '' : 'style="display:none;"'; // Only show toggle in Simulation mode

        card.innerHTML = `
            <div class="if-header">
                <div class="if-title">
                    <span class="if-name">${iface.name}</span>
                    <span class="if-ip">${iface.ip}</span>
                </div>
                <label class="switch" ${displayToggle}>
                    <input type="checkbox" ${isChecked} onchange="toggleLink('${iface.ip}', this.checked)">
                    <span class="slider"></span>
                </label>
            </div>
            <div class="if-stats">
                <div class="stat-item">
                    <span class="val">${iface.is_online ? iface.rtt_ms.toFixed(1) + ' ms' : 'N/A'}</span>
                    <span class="lbl">Latency (RTT)</span>
                </div>
                <div class="stat-item">
                    <span class="val">${iface.is_online ? iface.loss_rate.toFixed(1) + '%' : 'N/A'}</span>
                    <span class="lbl">Packet Loss</span>
                </div>
                <div class="stat-item">
                    <span class="val">${iface.bandwidth_mbps.toFixed(1)} Mbps</span>
                    <span class="lbl">Path Limit</span>
                </div>
                <div class="score-badge">Score: ${iface.score.toFixed(0)}</div>
            </div>
        `;
        container.appendChild(card);
    });
}

// Toggle interface state via API
async function toggleLink(ip, state) {
    try {
        const response = await fetch('/api/interface/toggle', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ip: ip, state: state })
        });
        const res = await response.json();
        console.log(`Interface ${res.name} is now ${res.is_alive ? 'UP' : 'DOWN'}`);
    } catch (err) {
        console.error("Error toggling interface:", err);
    }
}

// Update downloader progress bar and block matrix
function updateDownloadProgress(download) {
    const startBtn = document.getElementById('start-btn');
    
    // Update button text and state
    if (download.is_completed) {
        startBtn.disabled = false;
        startBtn.innerHTML = `<i class="fa-solid fa-play"></i> Aggregate & Download`;
    } else {
        startBtn.disabled = true;
        startBtn.innerHTML = `<i class="fa-solid fa-sync fa-spin"></i> Aggregating Traffic...`;
    }

    // Progress bar and ratio updates
    document.getElementById('progress-fill-bar').style.width = `${download.percent}%`;
    document.getElementById('progress-percent').innerText = `${download.percent}% Completed (${(download.downloaded / (1024*1024)).toFixed(1)} MB / ${(download.total_size / (1024*1024)).toFixed(1)} MB)`;
    
    const chunks = download.chunks || [];
    const completed = chunks.filter(c => c.status === 'COMPLETED').length;
    document.getElementById('progress-ratio').innerText = `${completed}/${chunks.length} chunks`;

    // Render chunk blocks matrix
    const matrixContainer = document.getElementById('chunk-matrix-container');
    
    // Check if we need to rebuild the block elements (only if chunk count changed or is empty)
    if (matrixContainer.children.length !== chunks.length) {
        matrixContainer.innerHTML = '';
        chunks.forEach((chunk, index) => {
            const block = document.createElement('div');
            block.id = `chunk-block-${index}`;
            block.className = 'chunk-block';
            block.title = `Chunk ${index}: Pending`;
            matrixContainer.appendChild(block);
        });
    }

    // Apply colors and titles dynamically to avoid expensive DOM re-creation
    chunks.forEach((chunk, index) => {
        const block = document.getElementById(`chunk-block-${index}`);
        if (!block) return;
        
        let statusClass = 'chunk-block';
        if (chunk.status === 'COMPLETED') statusClass += ' completed';
        else if (chunk.status === 'DOWNLOADING') statusClass += ' downloading';
        else if (chunk.status === 'FAILED') statusClass += ' failed';
        
        block.className = statusClass;
        block.title = `Chunk ${chunk.index}: ${chunk.status} ${chunk.interface_ip ? '(' + chunk.interface_ip + ')' : ''}`;
    });
}

// Attach Event Listeners
document.getElementById('start-btn').addEventListener('click', async () => {
    const url = document.getElementById('download-url').value;
    const chunkSize = parseFloat(document.getElementById('chunk-size').value);
    const mode = document.getElementById('mode-select').value;
    
    const startBtn = document.getElementById('start-btn');
    startBtn.disabled = true;
    startBtn.innerHTML = `<i class="fa-solid fa-circle-notch fa-spin"></i> Initializing...`;

    try {
        const response = await fetch('/api/download', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                url: url,
                chunk_size_mb: chunkSize,
                use_simulation: mode === 'simulation',
                output_path: "downloads/ubuntu_bonded.iso"
            })
        });
        
        const data = await response.json();
        if (response.ok) {
            console.log("Download started successfully:", data);
        } else {
            alert(`Failed: ${data.error}`);
            startBtn.disabled = false;
            startBtn.innerHTML = `<i class="fa-solid fa-play"></i> Aggregate & Download`;
        }
    } catch (err) {
        console.error("Error triggering download:", err);
        alert("Failed to connect to the AetherBond aggregator backend.");
        startBtn.disabled = false;
        startBtn.innerHTML = `<i class="fa-solid fa-play"></i> Aggregate & Download`;
    }
});

// App Entrypoint
window.addEventListener('DOMContentLoaded', () => {
    initChart();
    connectWebSocket();
});
