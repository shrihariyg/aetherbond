use std::time::{Duration, Instant};

/// A lightweight, highly performant Kalman Filter implementation 
/// for real-time tracking and smoothing of network jitter.
#[derive(Debug, Clone)]
pub struct JitterKalmanFilter {
    q: f64, // Process noise covariance
    r: f64, // Measurement noise covariance
    x: f64, // Estimated jitter state (ms)
    p: f64, // Estimation error covariance
    k: f64, // Kalman gain
}

impl Default for JitterKalmanFilter {
    fn default() -> Self {
        Self {
            q: 0.05, // Smooth adjustments
            r: 2.0,  // Filter out measurement noise spikes
            x: 0.0,
            p: 1.0,
            k: 0.0,
        }
    }
}

impl JitterKalmanFilter {
    pub fn new(process_noise: f64, measurement_noise: f64) -> Self {
        Self {
            q: process_noise,
            r: measurement_noise,
            x: 0.0,
            p: 1.0,
            k: 0.0,
        }
    }

    /// Updates the Kalman filter with a new raw jitter measurement and returns the smoothed estimate.
    pub fn update(&mut self, measurement: f64) -> f64 {
        // Prediction update
        self.p += self.q;

        // Measurement update (Correction)
        self.k = self.p / (self.p + self.r);
        self.x += self.k * (measurement - self.x);
        self.p *= 1.0 - self.k;

        self.x
    }

    pub fn get_estimate(&self) -> f64 {
        self.x
    }
}

/// Dynamic path stats tracker modeled on BBR congestion control.
/// Monitors min RTT, max bandwidth, and schedules adaptive pacing window.
#[derive(Debug, Clone)]
pub struct BbrPathMetrics {
    pub interface_ip: String,
    pub smoothed_rtt: Duration,
    pub min_rtt: Duration,
    pub min_rtt_timestamp: Instant,
    
    pub observed_bandwidth_bps: f64,
    pub max_bandwidth_bps: f64,
    pub max_bw_timestamp: Instant,
    
    pub packet_loss_rate: f64, // EWMA of lost packets (0.0 to 1.0)
    pub jitter_filter: JitterKalmanFilter,
    
    pub cwnd_bytes: usize,
    pub pacing_rate_bps: f64,
    pub is_online: bool,
}

impl BbrPathMetrics {
    pub fn new(interface_ip: String) -> Self {
        let now = Instant::now();
        Self {
            interface_ip,
            smoothed_rtt: Duration::from_millis(50),
            min_rtt: Duration::from_millis(50),
            min_rtt_timestamp: now,
            observed_bandwidth_bps: 0.0,
            max_bandwidth_bps: 1_000_000.0, // Default 1 Mbps baseline
            max_bw_timestamp: now,
            packet_loss_rate: 0.0,
            jitter_filter: JitterKalmanFilter::default(),
            cwnd_bytes: 1472 * 10, // Default 10 packet window size
            pacing_rate_bps: 1_000_000.0,
            is_online: true,
        }
    }

    /// Records a new round-trip time sample, updating min_rtt and smoothed_rtt.
    pub fn register_rtt_sample(&mut self, rtt: Duration) {
        let now = Instant::now();
        
        // EWMA update of smoothed RTT (alpha = 0.125)
        self.smoothed_rtt = Duration::from_secs_f64(
            self.smoothed_rtt.as_secs_f64() * 0.875 + rtt.as_secs_f64() * 0.125
        );

        // Update absolute minimum RTT tracking (sliding 10 seconds window)
        if rtt < self.min_rtt || now.duration_since(self.min_rtt_timestamp) > Duration::from_secs(10) {
            self.min_rtt = rtt;
            self.min_rtt_timestamp = now;
        }

        // Feed jitter variation (deviation from smoothed RTT) to the Kalman Filter
        let jitter_deviation_ms = (rtt.as_secs_f64() - self.smoothed_rtt.as_secs_f64()).abs() * 1000.0;
        self.jitter_filter.update(jitter_deviation_ms);

        self.recalculate_congestion_window();
    }

    /// Registers a throughput sample (bytes sent/received in a specific duration).
    pub fn register_bandwidth_sample(&mut self, bytes: usize, elapsed: Duration) {
        let now = Instant::now();
        if elapsed.is_zero() {
            return;
        }

        let bps = (bytes as f64 * 8.0) / elapsed.as_secs_f64();
        self.observed_bandwidth_bps = bps;

        // Tracks highest observed throughput (sliding 10 seconds window)
        if bps > self.max_bandwidth_bps || now.duration_since(self.max_bw_timestamp) > Duration::from_secs(10) {
            self.max_bandwidth_bps = bps;
            self.max_bw_timestamp = now;
        }

        self.recalculate_congestion_window();
    }

    /// Registers a packet loss or transmission success outcome.
    pub fn register_transmission(&mut self, is_lost: bool) {
        let alpha = 0.05; // EWMA weight
        let sample = if is_lost { 1.0 } else { 0.0 };
        self.packet_loss_rate = self.packet_loss_rate * (1.0 - alpha) + sample * alpha;
    }

    /// Detects buffer bloat or link congestion by comparing current latency to minimal baseline.
    pub fn detect_queue_buildup(&self) -> bool {
        // If smoothed latency exceeds min latency by 30% plus a 15ms tolerance window, flag buildup
        let threshold = self.min_rtt.as_secs_f64() * 1.30 + 0.015;
        self.smoothed_rtt.as_secs_f64() > threshold
    }

    /// Adjusts the dynamic congestion window (cwnd) and adaptive pacing rate using BBR rules.
    fn recalculate_congestion_window(&mut self) {
        // 1. Calculate Bandwidth-Delay Product (BDP)
        // BDP = Max Bandwidth * Min RTT
        let bdp_bytes = (self.max_bandwidth_bps * self.min_rtt.as_secs_f64() / 8.0) as usize;

        // 2. Adjust target CWND based on queue buildup
        let multiplier = if self.detect_queue_buildup() {
            1.25 // Reduce buffer bloat by keeping window tight
        } else {
            2.0  // Double BDP for robust pipe-filling on stable links
        };

        // Enforce minimum window of at least 4 standard MTU frames
        let target_cwnd = ((bdp_bytes as f64) * multiplier) as usize;
        self.cwnd_bytes = target_cwnd.max(1472 * 4);

        // 3. Compute pacing rate (Bytes/sec pacing threshold)
        // BBR pacing rate = pacing_gain * Max Bandwidth
        let pacing_gain = if self.detect_queue_buildup() { 0.75 } else { 1.25 };
        self.pacing_rate_bps = self.max_bandwidth_bps * pacing_gain;
    }

    /// Computes a unified link reliability score (0.0 to 100.0) used by WRR.
    pub fn calculate_scheduling_score(&self) -> f64 {
        if !self.is_online {
            return 0.0;
        }

        let base_bw_mbps = self.max_bandwidth_bps / 1_000_000.0;
        
        // Dynamic penalties:
        // Penalty for high packet loss
        let loss_penalty = if self.packet_loss_rate > 0.02 {
            // Drop score exponentially if loss > 2%
            (self.packet_loss_rate * 50.0).exp().min(80.0)
        } else {
            0.0
        };

        // Penalty for high jitter
        let jitter_ms = self.jitter_filter.get_estimate();
        let jitter_penalty = (jitter_ms / 10.0).min(20.0);

        // Penalty for queue buildup
        let queue_penalty = if self.detect_queue_buildup() { 30.0 } else { 0.0 };

        let raw_score = base_bw_mbps * 10.0 - loss_penalty - jitter_penalty - queue_penalty;
        raw_score.max(1.0).min(100.0)
    }
}
