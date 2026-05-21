import os
import time
import asyncio
import aiohttp
import logging
from typing import List, Dict, Any, Callable
from aetherbond.client.scheduler import Scheduler
from aetherbond.common.simulator import simulator_registry

logger = logging.getLogger("aetherbond.downloader")

class Chunk:
    def __init__(self, index: int, start: int, end: int):
        self.index = index
        self.start = start
        self.end = end
        self.bytes_downloaded = 0
        self.status = "PENDING"  # PENDING, DOWNLOADING, COMPLETED, FAILED
        self.interface_ip = ""
        self.elapsed_time = 0.0

    @property
    def size(self) -> int:
        return self.end - self.start + 1


class DownloadSession:
    def __init__(self, url: str, output_path: str, scheduler: Scheduler, chunk_size_mb: float = 5.0):
        self.url = url
        self.output_path = output_path
        self.scheduler = scheduler
        self.chunk_size = int(chunk_size_mb * 1024 * 1024)
        
        self.total_size = 0
        self.chunks: List[Chunk] = []
        self.is_started = False
        self.is_completed = False
        self.start_time = 0.0
        self.end_time = 0.0
        self.total_downloaded_bytes = 0
        self.current_speed_bps = 0.0
        
        # Callbacks for progress tracking
        self.on_progress_cb: Optional[Callable[[Dict[str, Any]], None]] = None

    async def initialize(self) -> bool:
        """
        Connects to the server, queries file details, and prepares ranges.
        """
        headers = {"User-Agent": "AetherBond Multipath Aggregator/1.0"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.head(self.url, headers=headers, allow_redirects=True) as resp:
                    if resp.status != 200:
                        # Try a GET request with Range: bytes=0-0 in case HEAD is not allowed
                        async with session.get(self.url, headers={**headers, "Range": "bytes=0-0"}) as get_resp:
                            if get_resp.status in [200, 206]:
                                self.total_size = int(get_resp.headers.get("Content-Range", "0-0/0").split("/")[-1])
                                accept_ranges = get_resp.headers.get("Accept-Ranges", "") == "bytes" or "Content-Range" in get_resp.headers
                            else:
                                raise Exception(f"Failed to fetch metadata (HTTP {resp.status})")
                    else:
                        self.total_size = int(resp.headers.get("Content-Length", 0))
                        accept_ranges = resp.headers.get("Accept-Ranges", "") == "bytes"

                    if self.total_size <= 0:
                        raise ValueError("File content size is zero or unknown.")

                    logger.info(f"File size: {self.total_size / (1024*1024):.2f} MB, Accept-Ranges: {accept_ranges}")

                    # Setup chunks
                    if accept_ranges:
                        num_chunks = (self.total_size + self.chunk_size - 1) // self.chunk_size
                        for i in range(num_chunks):
                            start = i * self.chunk_size
                            end = min(start + self.chunk_size - 1, self.total_size - 1)
                            self.chunks.append(Chunk(i, start, end))
                    else:
                        # Single large chunk if Accept-Ranges is not supported
                        logger.warning("Accept-Ranges not supported. Falling back to single-link download.")
                        self.chunks.append(Chunk(0, 0, self.total_size - 1))

                    # Pre-allocate sparse file on disk to write chunks concurrently
                    with open(self.output_path, "wb") as f:
                        f.truncate(self.total_size)
                    
                    return True
        except Exception as e:
            logger.error(f"Download session initialization failed: {e}")
            return False

    async def start(self):
        self.is_started = True
        self.start_time = time.time()
        
        # Spawn progress updates in background
        progress_task = asyncio.create_task(self._track_speed_loop())
        
        # Max concurrent connections = number of physical interfaces * 4 (multipath efficiency)
        sem = asyncio.Semaphore(12)
        
        # Create a queue of chunks to download
        queue = asyncio.Queue()
        for chunk in self.chunks:
            await queue.put(chunk)

        async def worker():
            while not queue.empty():
                chunk = await queue.get()
                async with sem:
                    await self._download_chunk(chunk, queue)
                queue.task_done()

        # Spawn workers
        num_workers = min(8, len(self.chunks))
        workers = [asyncio.create_task(worker()) for _ in range(num_workers)]
        
        await queue.join()
        
        # Stop workers and metrics tracking
        for w in workers:
            w.cancel()
        progress_task.cancel()
        
        self.is_completed = True
        self.end_time = time.time()
        logger.info(f"Download complete! Time elapsed: {self.end_time - self.start_time:.2f} seconds")

    async def _download_chunk(self, chunk: Chunk, queue: asyncio.Queue):
        """
        Downloads a single range chunk, binding it to the interface assigned by the scheduler.
        """
        max_retries = 3
        retries = 0
        
        while retries < max_retries:
            try:
                # Select outgoing interface IP via Weighted Round Robin scheduler
                interface_ip = self.scheduler.select_interface_wrr()
                chunk.interface_ip = interface_ip
                chunk.status = "DOWNLOADING"
                
                logger.info(f"Chunk {chunk.index} ({chunk.size / 1024:.1f} KB) scheduled via interface {interface_ip}")
                
                start_time = time.time()
                
                # Check if we are using the simulation layer
                metric = self.scheduler.monitor.get_metrics(interface_ip)
                is_sim = metric.is_simulated if metric else False
                
                if is_sim:
                    # SIMULATED PATH
                    # 1. Simulate path latency (RTT)
                    await simulator_registry.simulate_delay(interface_ip)
                    
                    # 2. Simulate streaming download chunks
                    chunk_step_bytes = 64 * 1024 # 64KB read blocks
                    downloaded = 0
                    
                    while downloaded < chunk.size:
                        if not self.is_started:
                            raise asyncio.CancelledError()
                            
                        # Read incremental bytes
                        step = min(chunk_step_bytes, chunk.size - downloaded)
                        
                        # Apply path congestion/throttling and check for failures
                        await simulator_registry.throttle_stream(interface_ip, step)
                        
                        downloaded += step
                        chunk.bytes_downloaded = downloaded
                        self.total_downloaded_bytes += step
                    
                    # Mock writing to file
                    # We write zero bytes just to simulate disk I/O at exact offset
                    with open(self.output_path, "r+b") as f:
                        f.seek(chunk.start)
                        f.write(b'\x00' * chunk.size)
                        
                else:
                    # PHYSICAL PATH: Bind socket to local interface IP
                    # Use a TCPConnector bound to the local IP of the selected interface
                    connector = aiohttp.TCPConnector(local_addr=(interface_ip, 0))
                    headers = {
                        "User-Agent": "AetherBond Multipath Aggregator/1.0",
                        "Range": f"bytes={chunk.start + chunk.bytes_downloaded}-{chunk.end}"
                    }
                    
                    async with aiohttp.ClientSession(connector=connector) as session:
                        async with session.get(self.url, headers=headers, timeout=30) as resp:
                            if resp.status not in [200, 206]:
                                raise Exception(f"HTTP error {resp.status} on chunk {chunk.index}")
                            
                            # Write chunk stream directly to pre-allocated disk offset
                            # To be safe in async, we open in "r+b" and seek
                            with open(self.output_path, "r+b") as f:
                                f.seek(chunk.start + chunk.bytes_downloaded)
                                
                                async for data in resp.content.iter_chunked(65536):
                                    f.write(data)
                                    chunk.bytes_downloaded += len(data)
                                    self.total_downloaded_bytes += len(data)
                                    
                # Success
                chunk.status = "COMPLETED"
                chunk.elapsed_time = time.time() - start_time
                logger.info(f"Chunk {chunk.index} successfully completed via interface {interface_ip} in {chunk.elapsed_time:.2f}s")
                return
                
            except Exception as e:
                retries += 1
                logger.warning(f"Error downloading chunk {chunk.index} on interface {chunk.interface_ip}: {e}. Retry {retries}/{max_retries}")
                chunk.status = "FAILED"
                await asyncio.sleep(1.0)
                
        # If we reach here, the chunk failed on this interface. Re-queue it for another interface!
        logger.error(f"Chunk {chunk.index} failed entirely on interface {chunk.interface_ip}. Re-steering to queue...")
        chunk.status = "PENDING"
        chunk.bytes_downloaded = 0
        await queue.put(chunk)

    async def _track_speed_loop(self):
        """
        Periodically calculates overall download metrics and reports progress.
        """
        last_bytes = 0
        last_time = time.time()
        
        while self.is_started:
            await asyncio.sleep(0.5)
            now = time.time()
            elapsed = now - last_time
            if elapsed <= 0:
                continue
                
            curr_bytes = self.total_downloaded_bytes
            delta_bytes = curr_bytes - last_bytes
            self.current_speed_bps = delta_bytes / elapsed
            
            last_bytes = curr_bytes
            last_time = now
            
            # Fire progress callback if registered
            if self.on_progress_cb:
                progress = {
                    "downloaded_bytes": curr_bytes,
                    "total_bytes": self.total_size,
                    "percent": round((curr_bytes / self.total_size) * 100, 1) if self.total_size > 0 else 0.0,
                    "speed_mbps": round((self.current_speed_bps * 8) / 1_000_000, 2),
                    "elapsed_sec": round(now - self.start_time, 1),
                    "chunks_completed": sum(1 for c in self.chunks if c.status == "COMPLETED"),
                    "total_chunks": len(self.chunks),
                    "active_speeds": {ip: round(link.current_speed_bps * 8 / 1_000_000, 2) 
                                      for ip, link in simulator_registry.interfaces.items()}
                }
                self.on_progress_cb(progress)
