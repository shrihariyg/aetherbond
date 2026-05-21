import os
import asyncio
import logging
from typing import Dict, Any, Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from aetherbond.client.interfaces import get_active_interfaces
from aetherbond.client.metrics import PathMonitor
from aetherbond.client.scheduler import Scheduler
from aetherbond.client.downloader import DownloadSession
from aetherbond.common.simulator import simulator_registry

# Setup FastAPI App
app = FastAPI(title="AetherBond Dashboard API")
logger = logging.getLogger("aetherbond.gui")

# State holders
active_monitor: Optional[PathMonitor] = None
active_scheduler: Optional[Scheduler] = None
active_session: Optional[DownloadSession] = None
current_download_task: Optional[asyncio.Task] = None

class DownloadRequest(BaseModel):
    url: str
    output_path: str = "downloaded_file.iso"
    chunk_size_mb: float = 5.0
    use_simulation: bool = True

class ToggleInterfaceRequest(BaseModel):
    ip: str
    state: Optional[bool] = None

@app.on_event("startup")
async def startup_event():
    # Initialize basic monitoring on startup
    global active_monitor, active_scheduler
    
    # Enable simulator by default for testing
    simulator_registry.enable()
    
    interfaces = get_active_interfaces(use_simulation=True)
    active_monitor = PathMonitor()
    active_scheduler = Scheduler(active_monitor)
    active_monitor.start(interfaces)
    logger.info("AetherBond GUI background monitor initialized.")

@app.on_event("shutdown")
async def shutdown_event():
    global active_monitor
    if active_monitor:
        await active_monitor.stop()

@app.post("/api/download")
async def start_download(req: DownloadRequest):
    global active_monitor, active_scheduler, active_session, current_download_task
    
    if active_session and active_session.is_started and not active_session.is_completed:
        return JSONResponse(status_code=400, content={"error": "A download is already in progress."})

    # Adjust simulator setting
    if req.use_simulation:
        simulator_registry.enable()
    else:
        simulator_registry.disable()

    # Re-scan interfaces based on choice
    interfaces = get_active_interfaces(use_simulation=req.use_simulation)
    if not interfaces:
        return JSONResponse(status_code=400, content={"error": "No active network interfaces detected."})

    # Restart monitor if mode changed
    if active_monitor:
        await active_monitor.stop()
    
    active_monitor = PathMonitor()
    active_scheduler = Scheduler(active_monitor)
    active_monitor.start(interfaces)
    
    # Setup DownloadSession
    # Put output files in a subfolder or keep workspace clean
    out_dir = os.path.dirname(os.path.abspath(req.output_path))
    os.makedirs(out_dir, exist_ok=True)
    
    active_session = DownloadSession(
        req.url, req.output_path, active_scheduler, chunk_size_mb=req.chunk_size_mb
    )
    
    success = await active_session.initialize()
    if not success:
        return JSONResponse(status_code=400, content={"error": "Failed to initialize download range requests."})

    # Start download in background
    current_download_task = asyncio.create_task(active_session.start())
    return {"message": "Download started.", "total_size_mb": round(active_session.total_size / (1024*1024), 2)}


@app.post("/api/interface/toggle")
async def toggle_interface(req: ToggleInterfaceRequest):
    link = simulator_registry.get_interface(req.ip)
    if not link:
        return JSONResponse(status_code=404, content={"error": "Simulated interface not found."})
    
    link.toggle(req.state)
    return {"name": link.name, "ip": link.ip, "is_alive": link.is_alive}


@app.get("/api/status")
async def get_system_status():
    global active_scheduler, active_session
    
    telemetry = active_scheduler.get_telemetry() if active_scheduler else []
    
    download_info = {}
    if active_session:
        download_info = {
            "url": active_session.url,
            "filename": os.path.basename(active_session.output_path),
            "total_size": active_session.total_size,
            "downloaded": active_session.total_downloaded_bytes,
            "percent": round((active_session.total_downloaded_bytes / active_session.total_size) * 100, 1) if active_session.total_size > 0 else 0.0,
            "speed_mbps": round((active_session.current_speed_bps * 8) / 1_000_000, 2),
            "is_started": active_session.is_started,
            "is_completed": active_session.is_completed,
            "elapsed_sec": round(time.time() - active_session.start_time, 1) if active_session.is_started else 0.0,
            "chunks": [{"index": c.index, "status": c.status, "interface_ip": c.interface_ip} for c in active_session.chunks]
        }

    return {
        "simulator_enabled": simulator_registry.is_enabled,
        "interfaces": telemetry,
        "download": download_info
    }


@app.websocket("/ws")
async def websocket_telemetry(websocket: WebSocket):
    await websocket.accept()
    logger.info("Dashboard WebSocket client connected.")
    try:
        while True:
            # Stream status details every 300ms for smooth transitions
            status = await get_system_status()
            await websocket.send_json(status)
            await asyncio.sleep(0.3)
    except WebSocketDisconnect:
        logger.info("Dashboard WebSocket client disconnected.")
    except Exception as e:
        logger.error(f"WebSocket send error: {e}")

# Mount static folder (this will be created next)
static_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
os.makedirs(static_path, exist_ok=True)
app.mount("/", StaticFiles(directory=static_path, html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8080)
