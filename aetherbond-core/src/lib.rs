pub mod congestion;
pub mod resequencing;
pub mod crypto;
pub mod redundancy;
pub mod transport;
pub mod xdp;

// Simple integration check tests inside the Rust library!
#[cfg(test)]
mod tests {
    use super::congestion::{BbrPathMetrics, JitterKalmanFilter};
    use super::resequencing::Resequencer;
    use super::crypto::AetherCrypto;
    use super::redundancy::{AdaptiveDuplicator, SlidingDeDuplicator};
    use super::transport::QuicMultiplexer;
    use std::time::{Duration, Instant};
    use std::sync::{Arc, Mutex};

    #[test]
    fn test_crypto_encryption() {
        let key = [0u8; 32];
        let crypto = AetherCrypto::new(&key);
        let payload = b"Hello, AetherBond Aggregator Node!";
        
        let encrypted = crypto.encrypt(payload, 999, 2).expect("Encryption failed");
        assert!(encrypted.len() > payload.len());

        let (decrypted, seq, path_id, _latency) = crypto.decrypt(&encrypted).expect("Decryption failed");
        assert_eq!(decrypted, payload.to_vec());
        assert_eq!(seq, 999);
        assert_eq!(path_id, 2);
    }

    #[test]
    fn test_jitter_kalman_filter() {
        let mut filter = JitterKalmanFilter::default();
        
        // Feed sample jitters: 10ms, 12ms, 11ms, 9ms, 30ms (spike)
        let f1 = filter.update(10.0);
        let f2 = filter.update(12.0);
        let f3 = filter.update(11.0);
        let f4 = filter.update(9.0);
        let f5 = filter.update(30.0); // Spike should be heavily smoothed

        assert!(f1 > 0.0);
        assert!(f5 < 25.0); // Verifies smoothing of transient spikes
    }

    #[test]
    fn test_bbr_path_metrics() {
        let mut metrics = BbrPathMetrics::new("192.168.1.50".to_string());
        
        // Register latency samples
        metrics.register_rtt_sample(Duration::from_millis(40));
        metrics.register_rtt_sample(Duration::from_millis(42));
        
        // Register bandwidth samples: 100KB in 50ms = 2MB/s = 16 Mbps
        metrics.register_bandwidth_sample(100_000, Duration::from_millis(50));

        assert!(metrics.max_bandwidth_bps > 1_000_000.0);
        assert!(metrics.smoothed_rtt.as_millis() >= 40);
        
        let score = metrics.calculate_scheduling_score();
        assert!(score > 0.0);
    }

    #[test]
    fn test_resequencer_reordering() {
        let flushed = Arc::new(Mutex::new(Vec::new()));
        let flushed_clone = flushed.clone();

        let mut reseq = Resequencer::new(100, 50, move |payload| {
            flushed_clone.lock().unwrap().push(payload);
        });

        // Put packet out of order: 102, then 100, then 101
        reseq.put(102, vec![3]);
        reseq.put(100, vec![1]);
        
        // At this point, only 100 can be flushed if next expected is 100
        {
            let data = flushed.lock().unwrap();
            assert_eq!(data.len(), 1);
            assert_eq!(data[0], vec![1]);
        }

        reseq.put(101, vec![2]);

        // Now 101 and 102 should instantly cascade flush consecutively
        {
            let data = flushed.lock().unwrap();
            assert_eq!(data.len(), 3);
            assert_eq!(data[1], vec![2]);
            assert_eq!(data[2], vec![3]);
        }
    }

    #[test]
    fn test_adaptive_redundancy() {
        let duplicator = AdaptiveDuplicator::default();
        
        // QoS 1 (Control frames) should always be duplicated if there's any packet loss
        assert!(duplicator.should_duplicate(1, 0.01));
        
        // QoS 3 (Bulk streams) should not be duplicated
        assert!(!duplicator.should_duplicate(3, 0.05));
        
        // QoS 0 (VoIP/Gaming) duplicates if loss crosses 3% threshold
        assert!(!duplicator.should_duplicate(0, 0.015));
        assert!(duplicator.should_duplicate(0, 0.04));
    }

    #[test]
    fn test_sliding_deduplicator() {
        let mut dedup = SlidingDeDuplicator::new(10);
        
        // Register new sequences
        assert!(dedup.register(500));
        assert!(dedup.register(501));
        
        // Try registering duplicate
        assert!(!dedup.register(500));
        assert_eq!(dedup.size(), 2);
        
        // Cycle the ring buffer history past sequence 500 to evict it
        for i in 600..610 {
            dedup.register(i);
        }
        
        // Now sequence 500 has aged out of history set, register should succeed again
        assert!(dedup.register(500));
    }

    #[test]
    fn test_quic_multiplexer() {
        let mux = QuicMultiplexer::new(5);
        
        let (stream, _rx) = mux.create_stream(10, 0).expect("Failed to create stream");
        assert_eq!(stream.stream_id, 10);
        assert_eq!(stream.priority, 0);

        let retrieved = mux.get_stream(10).expect("Failed to get stream");
        assert_eq!(retrieved.stream_id, 10);

        mux.close_stream(10);
        assert!(mux.get_stream(10).is_none());
    }

    #[test]
    fn test_xdp_fallback() {
        use super::xdp::XdpBypassLoader;
        let mut loader = XdpBypassLoader::new("eth0");
        let init_result = loader.initialize();
        assert!(init_result.is_ok(), "XDP/Raw Socket fallback initialization failed!");
        
        // Test packet sending/receiving fallback in non-blocking mode
        let test_payload = b"XDP_TEST_FRAME";
        // Since we bind to localhost for fallback, we can send to ourselves!
        if let Some(ref sock) = loader.socket {
            let addr = sock.local_addr().unwrap();
            let target_str = format!("{}", addr);
            let sent = loader.send_packet(test_payload, &target_str);
            assert!(sent.is_ok());
            assert_eq!(sent.unwrap(), test_payload.len());
            
            // Allow a small window for the packet to loop back
            std::thread::sleep(Duration::from_millis(50));
            
            let mut buf = [0u8; 100];
            let received = loader.receive_packet(&mut buf);
            assert!(received.is_ok());
            let len = received.unwrap();
            assert_eq!(&buf[..len], test_payload);
        }
    }
}
