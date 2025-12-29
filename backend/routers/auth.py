from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import RedirectResponse, JSONResponse
import spotipy
from spotipy.oauth2 import SpotifyOAuth, SpotifyClientCredentials
from ..config import settings
from ..core.automation import automation_manager
import time

router = APIRouter()

def get_spotify_oauth():
    return SpotifyOAuth(
        client_id=settings.CLIENT_ID,
        client_secret=settings.CLIENT_SECRET,
        redirect_uri=settings.REDIRECT_URI,
        scope=settings.SCOPE,
        cache_handler=None, # We handle caching in session
        show_dialog=True
    )

def get_app_client():
    """
    Returns a Spotify client authenticated with Client Credentials (App Token).
    Used for scanning albums/tracks where user context is not needed.
    Higher rate limits!
    """
    client_credentials_manager = SpotifyClientCredentials(
        client_id=settings.CLIENT_ID, 
        client_secret=settings.CLIENT_SECRET
    )
    return spotipy.Spotify(
        client_credentials_manager=client_credentials_manager,
        requests_timeout=20,
        retries=0,
        status_retries=0
    )

@router.get("/login")
def login():
    sp_oauth = get_spotify_oauth()
    auth_url = sp_oauth.get_authorize_url()
    return {"url": auth_url}

@router.get("/callback")
def callback(code: str, request: Request):
    sp_oauth = get_spotify_oauth()
    try:
        token_info = sp_oauth.get_access_token(code, check_cache=False)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Auth Failed: {str(e)}")
        
    # Store token in session
    request.session["token_info"] = token_info
    
    # Save for Automation (Headless)
    automation_manager.save_tokens(token_info)
    
    # Redirect to Frontend (assuming running on port 5173)
    return RedirectResponse(f"{settings.FRONTEND_URL}/dashboard")

@router.get("/me")
def get_current_user(request: Request):
    token_info = request.session.get("token_info")
    if not token_info:
        return JSONResponse({"authenticated": False}, status_code=401)
        
    # Check expiry and refresh if needed
    now = int(time.time())
    if token_info['expires_at'] - now < 60:
        sp_oauth = get_spotify_oauth()
        try:
            token_info = sp_oauth.refresh_access_token(token_info['refresh_token'])
            request.session["token_info"] = token_info
        except:
            return JSONResponse({"authenticated": False}, status_code=401)
            
    sp = spotipy.Spotify(auth=token_info['access_token'])
    try:
        user = sp.current_user()
        return {"authenticated": True, "user": user}
    except:
        return JSONResponse({"authenticated": False}, status_code=401)

@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return {"authenticated": False, "message": "Logged out"}

def get_spotify_client(request: Request):
    token_info = request.session.get("token_info")
    if not token_info:
        raise HTTPException(status_code=401, detail="Not Authenticated")
        
    now = int(time.time())
    if token_info['expires_at'] - now < 60:
        sp_oauth = get_spotify_oauth()
        try:
            token_info = sp_oauth.refresh_access_token(token_info['refresh_token'])
            request.session["token_info"] = token_info
        except:
             raise HTTPException(status_code=401, detail="Session Expired")
    
    return spotipy.Spotify(
        auth=token_info['access_token'], 
        requests_timeout=20, 
        retries=0, 
        status_retries=0
    )
