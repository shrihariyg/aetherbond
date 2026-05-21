use std::collections::BTreeMap;
use std::time::{Duration, Instant};

/// Out-of-order packet container with its insertion time.
#[derive(Debug)]
struct ReseqPacket {
    payload: Vec<u8>,
    timestamp: Instant,
}

/// An ultra-low latency User-Space Resequencing Sliding-Window Buffer in Rust.
/// Uses a BTreeMap for high-performance sequence mapping and instant consecutive flushes.
pub struct Resequencer<F>
where
    F: FnMut(Vec<u8>),
{
    next_seq: u32,
    buffer: BTreeMap<u32, ReseqPacket>,
    callback: F,
    
    // Dynamic timeout parameters to adapt to unequal link RTT differences
    adaptive_timeout: Duration,
    last_flush_time: Instant,
    max_buffer_size: usize,
}

impl<F> Resequencer<F>
where
    F: FnMut(Vec<u8>),
{
    pub fn new(initial_seq: u32, max_buffer_size: usize, callback: F) -> Self {
        Self {
            next_seq: initial_seq,
            buffer: BTreeMap::new(),
            callback,
            adaptive_timeout: Duration::from_millis(40), // Balanced baseline timeout
            last_flush_time: Instant::now(),
            max_buffer_size,
        }
    }

    /// Dynamically scales the flush window timeout based on observed path jitter.
    pub fn update_timeout(&mut self, jitter_delta: Duration) {
        // Safe limits: between 15ms and 250ms
        let bounded_timeout = jitter_delta.mul_f64(1.5).max(Duration::from_millis(15)).min(Duration::from_millis(250));
        self.adaptive_timeout = bounded_timeout;
    }

    /// Places an incoming packet into the buffer and flushes all consecutive ordered packets.
    pub fn put(&mut self, seq: u32, payload: Vec<u8>) {
        let now = Instant::now();

        // 1. Detect and discard ancient packets that have already been skipped/flushed
        if seq < self.next_seq {
            log::debug!("Discarding late packet (sequence {}), expected next is {}", seq, self.next_seq);
            return;
        }

        // 2. Prevent buffer bloat under severe congestion (memory pressure policy)
        if self.buffer.len() >= self.max_buffer_size {
            log::warn!("Resequencer buffer memory limit reached. Forcing head flush to release resources.");
            self.force_flush_head();
        }

        // 3. Store the packet
        self.buffer.insert(seq, ReseqPacket { payload, timestamp: now });

        // 4. Flush consecutive ordered sequences starting from self.next_seq
        self.flush_consecutive();
    }

    /// Evaluates aging packets. Flushes gaps if the packet at the head of the buffer 
    /// has waited longer than the dynamic adaptive timeout.
    pub fn tick(&mut self) {
        if self.buffer.is_empty() {
            return;
        }

        let now = Instant::now();
        
        // Peek at the lowest sequence key in the buffer
        if let Some((&first_seq, packet)) = self.buffer.iter().next() {
            if now.duration_since(packet.timestamp) > self.adaptive_timeout {
                log::info!(
                    "Reseq timeout triggered. Skipping sequence gap from {} to {}.",
                    self.next_seq,
                    first_seq
                );
                
                // Advance stream sequence pointer beyond the missing packets to the oldest cached block
                self.next_seq = first_seq;
                self.flush_consecutive();
            }
        }
    }

    /// Emits consecutive ordered packets to the output callback.
    fn flush_consecutive(&mut self) {
        let mut count = 0;
        while self.buffer.contains_key(&self.next_seq) {
            if let Some(packet) = self.buffer.remove(&self.next_seq) {
                (self.callback)(packet.payload);
                self.next_seq += 1;
                count += 1;
            }
        }
        if count > 0 {
            self.last_flush_time = Instant::now();
        }
    }

    /// Forcibly releases the lowest sequence packet currently buffered, skipping any missing gaps.
    fn force_flush_head(&mut self) {
        if let Some((&lowest_seq, _)) = self.buffer.iter().next() {
            self.next_seq = lowest_seq;
            self.flush_consecutive();
        }
    }

    pub fn get_next_expected_seq(&self) -> u32 {
        self.next_seq
    }

    pub fn len(&self) -> usize {
        self.buffer.len()
    }
}
