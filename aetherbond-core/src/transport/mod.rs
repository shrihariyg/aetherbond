use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use tokio::sync::mpsc;

/// Represents an independent stream channel inside the multipath QUIC tunnel.
/// Eliminates Head-of-Line (HOL) blocking: if Stream 1 suffers packet loss, 
/// Stream 2 continues flowing over other lanes unimpeded.
pub struct QuicVirtualStream {
    pub stream_id: u32,
    pub priority: u8, // QoS Priority (0 = Highest, 255 = Lowest)
    tx: mpsc::Sender<Vec<u8>>,
}

impl QuicVirtualStream {
    pub fn new(stream_id: u32, priority: u8, buffer_size: usize) -> (Self, mpsc::Receiver<Vec<u8>>) {
        let (tx, rx) = mpsc::channel(buffer_size);
        (Self { stream_id, priority, tx }, rx)
    }

    /// Feeds payload data to the stream's local buffer.
    pub async fn push_payload(&self, data: Vec<u8>) -> Result<(), mpsc::error::SendError<Vec<u8>>> {
        self.tx.send(data).await
    }
}

/// Dynamic Stream Multiplexer for user-space multipath QUIC flow management.
#[derive(Clone)]
pub struct QuicMultiplexer {
    streams: Arc<Mutex<HashMap<u32, Arc<QuicVirtualStream>>>>,
    max_streams: usize,
}

impl QuicMultiplexer {
    pub fn new(max_streams: usize) -> Self {
        Self {
            streams: Arc::new(Mutex::new(HashMap::new())),
            max_streams,
        }
    }

    /// Creates and registers a new virtual stream inside the multiplexer tunnel.
    pub fn create_stream(&self, stream_id: u32, priority: u8) -> Option<(Arc<QuicVirtualStream>, mpsc::Receiver<Vec<u8>>)> {
        let mut guard = self.streams.lock().unwrap();
        
        if guard.len() >= self.max_streams {
            log::warn!("Maximum QUIC streams limit reached ({})", self.max_streams);
            return None;
        }

        let (stream, rx) = QuicVirtualStream::new(stream_id, priority, 128);
        let shared_stream = Arc::new(stream);
        
        guard.insert(stream_id, shared_stream.clone());
        Some((shared_stream, rx))
    }

    /// Retrieves an active virtual stream by ID.
    pub fn get_stream(&self, stream_id: u32) -> Option<Arc<QuicVirtualStream>> {
        let guard = self.streams.lock().unwrap();
        guard.get(&stream_id).cloned()
    }

    /// Closes and evicts a virtual stream, releasing its resources.
    pub fn close_stream(&self, stream_id: u32) {
        let mut guard = self.streams.lock().unwrap();
        if guard.remove(&stream_id).is_some() {
            log::info!("QUIC Virtual Stream {} closed.", stream_id);
        }
    }

    /// Emits a list of all active stream priorities. Used by QoS scheduler for prioritization.
    pub fn get_stream_priorities(&self) -> Vec<(u32, u8)> {
        let guard = self.streams.lock().unwrap();
        guard.iter()
            .map(|(&id, stream)| (id, stream.priority))
            .collect()
    }
}
