from fastapi import APIRouter, Depends, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List
import datetime
from .auth import get_spotify_client
from ..core.scanner import scanner

router = APIRouter()


class ScanSettings(BaseModel):
    start_date: str
    end_date: str
    include_followed: bool = True
    include_liked_songs: bool = False
    min_liked_songs: int = 1
    album_types: List[str] = ['album', 'single']
    refresh_artists: bool = False
    
@router.get("/cache-info")
def get_cache_info():
    return scanner.get_artists_cache_info()

@router.post("/start")
async def start_scan(settings: ScanSettings, background_tasks: BackgroundTasks, sp=Depends(get_spotify_client)):
    engine_settings = settings.dict()
    
    if scanner.get_status()["is_running"]:
        return {"status": "error", "message": "Scan already running"}

    background_tasks.add_task(scanner.scan_process, sp, engine_settings)
    return {"status": "started", "settings": engine_settings}

@router.get("/status")
def get_scan_status():
    return scanner.get_status()

@router.get("/results")
def get_scan_results():
    return scanner.get_results()

@router.post("/stop")
def stop_scan():
    scanner.stop_scan()
    return {"status": "stopping"}
