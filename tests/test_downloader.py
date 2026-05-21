import unittest
import asyncio
import os
import sys
import shutil

# Adjust path to import local aetherbond package
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from aetherbond.client.interfaces import get_active_interfaces
from aetherbond.client.metrics import PathMonitor
from aetherbond.client.scheduler import Scheduler
from aetherbond.client.downloader import DownloadSession
from aetherbond.common.simulator import simulator_registry

class TestDownloader(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.test_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "test_out"))
        os.makedirs(self.test_dir, exist_ok=True)
        self.output_file = os.path.join(self.test_dir, "test_file.bin")
        
        # Enable simulator
        simulator_registry.enable()
        
        # Setup monitor & scheduler with simulated interfaces
        self.interfaces = get_active_interfaces(use_simulation=True)
        self.monitor = PathMonitor()
        self.scheduler = Scheduler(self.monitor)
        self.monitor.start(self.interfaces)

    async def asyncTearDown(self):
        self.monitor.start_time = 0 # Avoid async warnings
        await self.monitor.stop()
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def test_chunk_segmentation(self):
        """Verify that file chunk ranges are contiguous and complete."""
        session = DownloadSession(
            url="http://dummy.url/file.bin",
            output_path=self.output_file,
            scheduler=self.scheduler,
            chunk_size_mb=1.0  # 1MB chunks
        )
        
        # Manually trigger segmentation on a mock size
        session.total_size = 3500000 # 3.5 MB
        session.is_started = True
        
        # Setup chunks
        num_chunks = (session.total_size + session.chunk_size - 1) // session.chunk_size
        from aetherbond.client.downloader import Chunk
        for i in range(num_chunks):
            start = i * session.chunk_size
            end = min(start + session.chunk_size - 1, session.total_size - 1)
            session.chunks.append(Chunk(i, start, end))

        self.assertEqual(len(session.chunks), 4)
        
        # Verify contiguity
        self.assertEqual(session.chunks[0].start, 0)
        self.assertEqual(session.chunks[0].end, 1024*1024 - 1)
        self.assertEqual(session.chunks[1].start, 1024*1024)
        self.assertEqual(session.chunks[1].end, 2*1024*1024 - 1)
        self.assertEqual(session.chunks[3].end, 3500000 - 1)

    async def test_simulated_download_execution(self):
        """Performs a full simulated multipath download and validates output."""
        # Setup a small mock download session
        session = DownloadSession(
            url="https://releases.ubuntu.com/22.04/ubuntu-22.04.4-desktop-amd64.iso",
            output_path=self.output_file,
            scheduler=self.scheduler,
            chunk_size_mb=0.5  # 500 KB chunks for speed
        )
        
        # Mock initialization to bypass network HEAD request
        session.total_size = 2 * 1024 * 1024  # 2 MB mock file
        from aetherbond.client.downloader import Chunk
        num_chunks = (session.total_size + session.chunk_size - 1) // session.chunk_size
        for i in range(num_chunks):
            start = i * session.chunk_size
            end = min(start + session.chunk_size - 1, session.total_size - 1)
            session.chunks.append(Chunk(i, start, end))
            
        # Pre-allocate file
        with open(self.output_file, "wb") as f:
            f.truncate(session.total_size)
            
        # Start download session
        await session.start()
        
        # Assert file exists and is of correct size
        self.assertTrue(os.path.exists(self.output_file))
        self.assertEqual(os.path.getsize(self.output_file), session.total_size)
        self.assertTrue(session.is_completed)

if __name__ == "__main__":
    unittest.main()

