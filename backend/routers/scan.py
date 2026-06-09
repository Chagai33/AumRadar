from fastapi import APIRouter, Depends, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List
import datetime
from .auth import get_spotify_client, get_app_client
from ..core.scanner import scanner

router = APIRouter()


def resolve_dynamic_dates(settings_dict: dict) -> dict:
    """Replace 'DYNAMIC' (Sun–Sat week) or 'LAST7' (rolling 7 days) with real dates."""
    start = settings_dict.get('start_date')
    end   = settings_dict.get('end_date')
    today = datetime.date.today()

    if start == 'DYNAMIC' or end == 'DYNAMIC':
        # Current calendar week: Sunday → Saturday
        days_since_sunday = (today.weekday() + 1) % 7
        week_start = today - datetime.timedelta(days=days_since_sunday)
        week_end   = week_start + datetime.timedelta(days=6)
        settings_dict = {**settings_dict, 'start_date': week_start.isoformat(), 'end_date': week_end.isoformat()}
    elif start == 'LAST7' or end == 'LAST7':
        # Rolling: last 7 days up to and including today
        settings_dict = {**settings_dict, 'start_date': (today - datetime.timedelta(days=7)).isoformat(), 'end_date': today.isoformat()}
    return settings_dict


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
    exclude_artists: List[str] = [] # List of Artist names or IDs to skip

    # Artist selection (optional — null means scan all)
    selected_artist_ids: Optional[List[str]] = None

    # Automation
    exclude_albums: bool = False  # If True, tracks from albums (4+ tracks same artist/album) are excluded from auto-export

class AutomationConfig(BaseModel):
    enabled: bool = False
    run_day: str = "friday" # monday, tuesday...
    run_time: str = "10:00"
    settings: ScanSettings

from ..core.automation import automation_manager

@router.get("/automation/config")
def get_automation_config():
    return automation_manager.load_config()

@router.post("/automation/config")
def save_automation_config(config: AutomationConfig):
    automation_manager.save_config(config.dict())
    return {"status": "saved", "config": config}

@router.post("/automation/run")
async def run_automation_headless(background_tasks: BackgroundTasks):
    config = automation_manager.load_config()
    if not config.get("enabled"):
        return {"status": "skipped", "reason": "Automation disabled"}
        
    try:
        headless_sp = automation_manager.get_headless_client()
        app_sp = get_app_client()
        
        # Use settings from config, resolving dynamic dates
        settings_dict = resolve_dynamic_dates(dict(config.get('settings', {})))

        # Determine Playlist Name with date range
        start = settings_dict.get('start_date', '')
        end   = settings_dict.get('end_date', '')
        playlist_name = f"Weekly Radar {start} – {end}" if start and end else "Weekly Radar"
        
        background_tasks.add_task(
            scanner.scan_process, 
            headless_sp, 
            settings_dict, 
            app_sp, 
            auto_export_name=playlist_name
        )
        
        return {"status": "triggered"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
    
@router.get("/artists")
def get_artists():
    artists = scanner._load_artists_cache()
    return sorted(
        [{
            "id": a["id"],
            "name": a["name"],
            "genres": a.get("genres", []),
            "followers": a.get("followers", {}).get("total", 0),
            "popularity": a.get("popularity", 0),
        } for a in artists],
        key=lambda x: x["name"].lower()
    )

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

@router.post("/dismiss-error")
def dismiss_error():
    scanner.dismiss_error()
    return {"status": "ok"}

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
