use ring::aead::{AeadKeyHeader, BoundKey, Nonce, NonceSequence, OpeningKey, SealingKey, UnboundKey, CHACHA20_POLY1305};
use ring::error::Unspecified;
use ring::rand::{SecureRandom, SystemRandom};
use std::time::{SystemTime, UNIX_EPOCH};

/// Custom Noise-style session keys derived during secure handshake.
pub struct AetherCrypto {
    secret_key: [u8; 32],
}

/// A sequential nonce sequence generator for Ring AEAD encryption
struct AetherNonceSequence {
    seq: u64,
}

impl NonceSequence for AetherNonceSequence {
    fn advance(&mut self) -> Result<Nonce, Unspecified> {
        let mut nonce_bytes = [0u8; 12];
        let bytes = self.seq.to_be_bytes();
        nonce_bytes[4..12].copy_from_slice(&bytes);
        self.seq += 1;
        Ok(Nonce::assume_unique_header(nonce_bytes))
    }
}

impl AetherCrypto {
    pub fn new(pre_shared_key: &[u8; 32]) -> Self {
        Self {
            secret_key: *pre_shared_key,
        }
    }

    /// Encrypts raw network packet payload with authenticated sequence, path, and timestamp headers.
    pub fn encrypt(&self, payload: &[u8], seq: u32, path_id: u32) -> Result<Vec<u8>, Unspecified> {
        let unbound_key = UnboundKey::new(&CHACHA20_POLY1305, &self.secret_key)?;
        
        // Dynamic unique nonce sequence starting at current time microsecond epoch
        let now_us = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_micros() as u64;
            
        let nonce_seq = AetherNonceSequence { seq: now_us ^ (seq as u64) };
        let mut sealing_key = SealingKey::new(unbound_key, nonce_seq);

        // Prep headers (16 bytes: seq [4], timestamp [8], path [4])
        let mut encrypted_packet = Vec::with_capacity(16 + payload.len() + 16);
        encrypted_packet.extend_from_slice(&seq.to_be_bytes());
        encrypted_packet.extend_from_slice(&now_us.to_be_bytes());
        encrypted_packet.extend_from_slice(&path_id.to_be_bytes());
        
        // Add payload
        encrypted_packet.extend_from_slice(payload);

        // Associated Authenticated Data (AAD) is the 16-byte header
        let aad = ring::aead::Aad::from(&encrypted_packet[0..16]);
        
        // Encrypt in-place inside the packet vector
        sealing_key.seal_in_place_append_tag(aad, &mut encrypted_packet[16..])?;

        Ok(encrypted_packet)
    }

    /// Decrypts encapsulated package and verifies authenticated integrity tags.
    /// Returns (decrypted_payload, seq, path_id, latency_ms)
    pub fn decrypt(&self, packet: &[u8]) -> Result<(Vec<u8>, u32, u32, f64), Unspecified> {
        if packet.len() < 32 { // 16 bytes header + 16 bytes AEAD auth tag minimum
            return Err(Unspecified);
        }

        // Parse headers
        let mut seq_bytes = [0u8; 4];
        let mut time_bytes = [0u8; 8];
        let mut path_bytes = [0u8; 4];

        seq_bytes.copy_from_slice(&packet[0..4]);
        time_bytes.copy_from_slice(&packet[4..12]);
        path_bytes.copy_from_slice(&packet[12..16]);

        let seq = u32::from_be_bytes(seq_bytes);
        let timestamp_us = u64::from_be_bytes(time_bytes);
        let path_id = u32::from_be_bytes(path_bytes);

        // Reconstruct decryption key and nonce
        let unbound_key = UnboundKey::new(&CHACHA20_POLY1305, &self.secret_key)?;
        let nonce_seq = AetherNonceSequence { seq: timestamp_us ^ (seq as u64) };
        let mut opening_key = OpeningKey::new(unbound_key, nonce_seq);

        // Create buffer for decryption
        let mut data = packet[16..].to_vec();
        let aad = ring::aead::Aad::from(&packet[0..16]);

        // Decrypt in-place
        let decrypted_slice = opening_key.open_in_place(aad, &mut data)?;
        
        // Calculate dynamic latency
        let now_us = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_micros() as u64;
            
        let latency_ms = if now_us > timestamp_us {
            (now_us - timestamp_us) as f64 / 1000.0
        } else {
            0.0
        };

        Ok((decrypted_slice.to_vec(), seq, path_id, latency_ms))
    }
}
