import heapq
import asyncio
import logging
import time
from typing import Dict, List, Tuple, Callable, Optional

logger = logging.getLogger("aetherbond.server.resequencer")

class ResequencingBuffer:
    def __init__(self, callback: Callable[[bytes], None], flush_timeout_ms: float = 40.0):
        """
        Sliding-window packet resequencing buffer.
        - callback: Function triggered when a packet is ready to be written to the server's virtual interface.
        - flush_timeout_ms: Maximum time (in milliseconds) to wait for a missing packet before skipping it.
        """
        self.callback = callback
        self.flush_timeout = flush_timeout_ms / 1000.0
        
        self.expected_seq = 0
        self.heap: List[Tuple[int, bytes, float]] = []  # Heap of (seq, payload, arrival_timestamp)
        self.seen_seqs = set()
        
        self._flush_task: Optional[asyncio.Task] = None
        self._lock = asyncio.Lock()

    async def put(self, seq: int, payload: bytes):
        """
        Pushes a new packet into the buffer heap.
        If the packet is the next expected, pops it and any contiguous next packets.
        """
        async with self._lock:
            # Drop duplicates (e.g., retransmissions of packets we already have)
            if seq in self.seen_seqs or seq < self.expected_seq:
                return

            # Push to heap
            heapq.heappush(self.heap, (seq, payload, time.time()))
            self.seen_seqs.add(seq)

            # Process and release contiguous packets
            await self._release_contiguous_packets_unlocked()

            # Manage flushing task for missing packets
            if self.heap and not self._flush_task:
                self._flush_task = asyncio.create_task(self._wait_and_flush_missing())

    async def _release_contiguous_packets_unlocked(self):
        """
        Pops and executes callback on all contiguous packets.
        """
        while self.heap and self.heap[0][0] == self.expected_seq:
            seq, payload, _ = heapq.heappop(self.heap)
            self.seen_seqs.remove(seq)
            
            # Forward the packet to the routing layer
            try:
                self.callback(payload)
            except Exception as e:
                logger.error(f"Error in packet delivery callback: {e}")
                
            self.expected_seq += 1

    async def _wait_and_flush_missing(self):
        """
        Task that periodically checks if the head of the heap has timed out waiting
        for a missing packet. If so, skips it and flushes.
        """
        while True:
            await asyncio.sleep(0.01) # 10ms check interval
            
            async with self._lock:
                if not self.heap:
                    self._flush_task = None
                    break
                
                # Check how long the oldest out-of-order packet has been waiting
                oldest_seq, _, arrival_time = self.heap[0]
                
                # If we are waiting for a missing packet (oldest_seq > expected_seq)
                if oldest_seq > self.expected_seq:
                    elapsed = time.time() - arrival_time
                    if elapsed >= self.flush_timeout:
                        # Timeout! Skip missing sequences and jump forward
                        logger.warning(
                            f"Resequencer packet timeout. Skipping expected {self.expected_seq} "
                            f"and jumping to {oldest_seq} after {elapsed * 1000:.1f}ms wait."
                        )
                        self.expected_seq = oldest_seq
                        await self._release_contiguous_packets_unlocked()
                else:
                    # Oldest is already release-able, which means we just need to flush it
                    await self._release_contiguous_packets_unlocked()
