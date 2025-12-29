from fastapi import APIRouter, Depends, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List
import datetime
from .auth import get_spotify_client, get_app_client
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
    
    # Advanced Filters
    min_duration_sec: int = 90
    max_duration_sec: int = 270
    forbidden_keywords: List[str] = [" live ", "session", "לייב", "קאבר", "a capella", "acapella", "FSOE", "techno", "extended", "sped up", "speed up", "intro", "slow", "remaster", "instrumental"]
    
@router.get("/cache-info")
def get_cache_info():
    return scanner.get_artists_cache_info()

@router.post("/start")
async def start_scan(settings: ScanSettings, background_tasks: BackgroundTasks, sp=Depends(get_spotify_client)):
    engine_settings = settings.dict()
    
    if scanner.get_status()["is_running"]:
        return {"status": "error", "message": "Scan already running"}

    # Initialize App Client for high-performance scanning
    app_sp = get_app_client()

    background_tasks.add_task(scanner.scan_process, sp, engine_settings, app_sp)
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

class ExportRequest(BaseModel):
    name: str
    uris: List[str]

@router.post("/export")
def export_playlist(req: ExportRequest, sp=Depends(get_spotify_client)):
    if not req.uris:
        return {"status": "error", "message": "No tracks to export"}
        
    user_id = sp.current_user()['id']
    date_str = datetime.date.today().strftime("%Y-%m-%d")
    final_name = f"{req.name} ({date_str})"
    
    try:
        playlist = sp.user_playlist_create(user_id, final_name, public=False)
        
        # Add tracks in batches of 100
        for i in range(0, len(req.uris), 100):
            batch = req.uris[i:i+100]
            sp.playlist_add_items(playlist['id'], batch)
            
        return {"status": "success", "playlist_url": playlist['external_urls']['spotify']}
    except Exception as e:
        return {"status": "error", "message": str(e)}
