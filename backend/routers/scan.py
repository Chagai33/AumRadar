from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException
from pydantic import BaseModel
from typing import Optional, List
import datetime
from .auth import get_spotify_client, get_app_client
from ..core.scanner import scanner
from ..core.job_trigger import trigger_scan_job

router = APIRouter()


def resolve_dynamic_dates(settings_dict: dict) -> dict:
    """Replace 'DYNAMIC' (Sat–Fri release week) or 'LAST7' (rolling 7 days) with real dates."""
    start = settings_dict.get('start_date')
    end   = settings_dict.get('end_date')
    today = datetime.date.today()

    if start == 'DYNAMIC' or end == 'DYNAMIC':
        # Release week: Saturday → Friday, anchored to the MOST RECENT Friday (today if
        # it IS Friday). New music drops Friday = the window's last day; a late run on
        # Sat/Sun/Mon still resolves to the week that just ENDED (not a future window),
        # and consecutive weeks tile with no gap. Matches the frontend 'sat_to_fri'.
        # STAGE4.md §19.3 / §20.1.  weekday(): Mon=0 … Fri=4 Sat=5 Sun=6.
        days_since_friday = (today.weekday() - 4) % 7
        week_end   = today - datetime.timedelta(days=days_since_friday)    # most recent Friday
        week_start = week_end - datetime.timedelta(days=6)                # the Saturday before
        settings_dict = {**settings_dict, 'start_date': week_start.isoformat(), 'end_date': week_end.isoformat()}
    elif start == 'LAST7' or end == 'LAST7':
        # Rolling: last 7 days up to and including today
        settings_dict = {**settings_dict, 'start_date': (today - datetime.timedelta(days=7)).isoformat(), 'end_date': today.isoformat()}
    return settings_dict


DEFAULT_FORBIDDEN_KEYWORDS = [" live ", "session", "לייב", "קאבר", "a capella", "acapella", "FSOE",
                              "techno", "extended", "sped up", "speed up", "intro", "slow",
                              "remaster", "instrumental"]


class ScanSettings(BaseModel):
    start_date: str
    end_date: str
    include_followed: bool = True
    include_liked_songs: bool = False
    min_liked_songs: int = 1
    album_types: List[str] = ['single']
    refresh_artists: bool = False

    # Advanced Filters
    min_duration_sec: int = 90
    max_duration_sec: int = 270
    forbidden_keywords: List[str] = DEFAULT_FORBIDDEN_KEYWORDS
    exclude_artists: List[str] = [] # List of Artist names or IDs to skip

    # Artist selection (optional — null means scan all)
    selected_artist_ids: Optional[List[str]] = None

    # Automation
    exclude_albums: bool = True  # If True, tracks from albums (4+ tracks same artist/album) are excluded from auto-export

class AutomationConfig(BaseModel):
    enabled: bool = False
    run_day: str = "friday" # monday, tuesday...
    run_time: str = "10:00"
    settings: ScanSettings

from ..core.automation import automation_manager
from ..core.storage_manager import storage

@router.get("/automation/config")
def get_automation_config():
    return automation_manager.load_config()

@router.post("/automation/config")
def save_automation_config(config: AutomationConfig):
    automation_manager.save_config(config.dict())
    return {"status": "saved", "config": config}


# ---- Dashboard filter settings -------------------------------------------------
# Stored server-side so they survive a refresh and follow the user to another
# browser. They used to live only in the browser's localStorage, where the
# automation-config load overwrote them on every page load.

USER_SETTINGS_FILE = "cache/user_settings.json"


class UserFilterSettings(BaseModel):
    min_duration_sec: int = 90
    max_duration_sec: int = 270
    forbidden_keywords: List[str] = DEFAULT_FORBIDDEN_KEYWORDS
    exclude_artists: List[str] = []
    album_types: List[str] = ['single']
    include_followed: bool = True
    include_liked_songs: bool = False
    min_liked_songs: int = 1


_USER_SETTINGS_KEYS = tuple(UserFilterSettings().dict().keys())


@router.get("/settings")
def get_user_settings():
    """saved=False means nothing was ever stored — the settings returned are a seed
    (the automation config's filters if one exists, else the defaults), and the
    frontend may still migrate a legacy localStorage copy over them."""
    stored = storage.load_json(USER_SETTINGS_FILE)
    if stored:
        return {"saved": True, "settings": UserFilterSettings(**stored).dict()}

    auto = automation_manager.load_config().get("settings") or {}
    # Empty lists would seed *over* the defaults with nothing — skip them.
    seed = {k: v for k, v in auto.items() if k in _USER_SETTINGS_KEYS and v not in (None, [])}
    return {"saved": False, "settings": UserFilterSettings(**seed).dict()}


@router.post("/settings")
def save_user_settings(body: UserFilterSettings):
    if not storage.save_json(USER_SETTINGS_FILE, body.dict()):
        raise HTTPException(status_code=503, detail="Could not persist settings")
    return {"saved": True, "settings": body}

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
        
        result = trigger_scan_job(
            "scheduled",
            sp=headless_sp,
            app_sp=app_sp,
            settings=settings_dict,
            auto_export_name=playlist_name,
            background_tasks=background_tasks,
        )
        return {"status": "triggered", "trigger": result}
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

def _reject_if_running():
    # get_status() already downgrades a stale (>120s heartbeat) scan to
    # not-running, so a True here means a genuinely live scan → refuse a second.
    if scanner.get_status().get("is_running"):
        raise HTTPException(status_code=409, detail="A scan is already running")


@router.post("/start")
async def start_scan(settings: ScanSettings, background_tasks: BackgroundTasks, sp=Depends(get_spotify_client)):
    _reject_if_running()
    # Resolve dynamic dates NOW so the Job — and the checkpoint it writes — see
    # concrete dates (a resume must run the exact same range).
    engine_settings = resolve_dynamic_dates(settings.dict())
    app_sp = get_app_client()
    result = trigger_scan_job("manual", sp=sp, app_sp=app_sp,
                              settings=engine_settings, background_tasks=background_tasks)
    if result.get("status") == "error":
        raise HTTPException(status_code=503, detail=result.get("message", "could not start scan"))
    return {"status": "started", "settings": engine_settings, "trigger": result}

@router.post("/refresh-artists")
async def refresh_artists(background_tasks: BackgroundTasks, sp=Depends(get_spotify_client)):
    """Update ONLY the followed-artists cache (no release scan) — runs as the same
    Cloud Run Job in 'refresh_artists' mode so it survives a closed tab."""
    _reject_if_running()
    app_sp = get_app_client()
    result = trigger_scan_job("refresh_artists", sp=sp, app_sp=app_sp,
                              background_tasks=background_tasks)
    if result.get("status") == "error":
        raise HTTPException(status_code=503, detail=result.get("message", "could not start refresh"))
    return {"status": "started", "trigger": result}

@router.get("/status")
def get_scan_status():
    return scanner.get_status()

@router.get("/results")
def get_scan_results():
    return scanner.get_results()

@router.get("/checkpoint")
def get_checkpoint():
    # Resumable-scan summary for the frontend banner (exists / resumable / counts).
    return scanner.get_checkpoint_info()

class AutoResumeToggle(BaseModel):
    enabled: bool

@router.get("/auto-resume")
def get_auto_resume():
    # Whether a blocked scan auto-resumes when the rate-limit clears (user toggle).
    return scanner.get_auto_resume()

@router.post("/auto-resume")
def set_auto_resume(body: AutoResumeToggle):
    return scanner.set_auto_resume(body.enabled)

@router.post("/resume")
async def resume_scan(background_tasks: BackgroundTasks, sp=Depends(get_spotify_client)):
    # Continue a blocked/interrupted scan from its checkpoint on the frozen artist
    # snapshot — as a Cloud Run Job in prod, or an in-process task in local dev.
    _reject_if_running()
    app_sp = get_app_client()
    result = trigger_scan_job("resume", sp=sp, app_sp=app_sp, background_tasks=background_tasks)
    if result.get("status") == "error":
        raise HTTPException(status_code=503, detail=result.get("message", "could not resume scan"))
    return {"status": "resumed", "trigger": result}

@router.post("/stop")
def stop_scan():
    scanner.stop_scan()
    return {"status": "stopping"}

@router.post("/dismiss-error")
def dismiss_error():
    scanner.dismiss_error()
    return {"status": "ok"}

def _validate_scan_id(scan_id: str):
    if not all(c.isdigit() or c == '_' for c in scan_id):
        raise HTTPException(status_code=400, detail="Invalid scan ID")

@router.get("/history")
def get_scan_history():
    return scanner.get_history_index()

@router.get("/history/{scan_id}")
def get_history_scan(scan_id: str):
    _validate_scan_id(scan_id)
    data = scanner.get_history_scan(scan_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Scan not found")
    return data

@router.delete("/history/{scan_id}")
def delete_history_scan(scan_id: str):
    _validate_scan_id(scan_id)
    scanner.delete_history_entry(scan_id)
    return {"status": "deleted"}

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
