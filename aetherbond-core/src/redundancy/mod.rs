use std::collections::HashSet;
use std::sync::Mutex;

/// Controls packet duplication steering based on path degradation and traffic classes.
#[derive(Debug)]
pub struct AdaptiveDuplicator {
    loss_threshold: f64,
}

impl Default for AdaptiveDuplicator {
    fn default() -> Self {
        Self { loss_threshold: 0.03 } // 3% loss threshold to trigger active duplication
    }
}

impl AdaptiveDuplicator {
    pub fn new(loss_threshold: f64) -> Self {
        Self { loss_threshold }
    }

    /// Determines if a packet should be duplicated across secondary links based on
    /// traffic priority (QoS class) and current path loss.
    ///
    /// QoS Classes:
    /// - 0: VoIP / Gaming (Latency & loss critical -> Always duplicate under loss)
    /// - 1: Control / ACK frames (Always duplicate to keep session state stable)
    /// - 2: Web Browsing (Standard)
    /// - 3: Bulk / Video (No duplication, relies on standard retransmission)
    pub fn should_duplicate(&self, qos_class: u8, primary_loss_rate: f64) -> bool {
        if qos_class == 1 {
            // Control/ACK frames are highly critical and always duplicated under any packet loss
            return primary_loss_rate > 0.005;
        }
        
        if qos_class == 0 {
            // VoIP/Gaming modes duplicate if loss crosses the configured threshold
            return primary_loss_rate >= self.loss_threshold;
        }

        false
    }
}

/// A highly performant Sliding Ring-Buffer De-duplicator.
/// Instantly discards redundant duplicated packets at the receiver TUN adapter.
pub struct SlidingDeDuplicator {
    max_history: usize,
    history: Vec<u32>,
    set: HashSet<u32>,
    write_idx: usize,
    lock: Mutex<()>,
}

impl SlidingDeDuplicator {
    pub fn new(max_history: usize) -> Self {
        Self {
            max_history,
            history: vec![0; max_history],
            set: HashSet::with_capacity(max_history),
            write_idx: 0,
            lock: Mutex::new(()),
        }
    }

    /// Tries to register a packet sequence number. 
    /// Returns `true` if this is a NEW packet, or `false` if it is a DUPLICATE and should be dropped.
    pub fn register(&mut self, seq: u32) -> bool {
        let _guard = self.lock.lock().unwrap();

        if self.set.contains(&seq) {
            // Duplicate detected!
            return false;
        }

        // Evict the oldest item in the sliding window ring buffer
        let old_seq = self.history[self.write_idx];
        if old_seq != 0 {
            self.set.remove(&old_seq);
        }

        // Write the new item
        self.history[self.write_idx] = seq;
        self.set.insert(seq);

        // Advance write pointer in cyclic ring
        self.write_idx = (self.write_idx + 1) % self.max_history;

        true
    }

    pub fn size(&self) -> usize {
        self.set.len()
    }
}
