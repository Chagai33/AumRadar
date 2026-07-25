
import os
import json
import logging
import datetime
from .storage_manager import storage
from ..config import settings as app_settings
from spotipy.oauth2 import SpotifyOAuth
from spotipy import Spotify
from spotipy.cache_handler import CacheHandler

AUTOMATION_FILE = "cache/automation_config.json"
TOKENS_FILE = "cache/automation_tokens.json"


class StorageCacheHandler(CacheHandler):
    """spotipy token cache backed by our StorageManager (GCS in prod, local in
    dev). Lets SpotifyOAuth auto-refresh the user token on expiry AND persist the
    refreshed token — so a headless Job that runs >60 min never dies on an expired
    token. The new token is written back to TOKENS_FILE for the next run too."""

    def get_cached_token(self):
        return storage.load_json(TOKENS_FILE)

    def save_token_to_cache(self, token_info):
        storage.save_json(TOKENS_FILE, token_info)

class AutomationManager:
    def __init__(self):
        self.config = self.load_config()

    def load_config(self):
        return storage.load_json(AUTOMATION_FILE, {
            "enabled": False,
            "run_day": "friday",
            "run_time": "10:00",
            "settings": {}
        })

    def save_config(self, config_data):
        self.config = config_data
        storage.save_json(AUTOMATION_FILE, config_data)
        
    def save_tokens(self, token_info):
        """
        Save the token info specifically for automation usage.
        We need a persistent Refresh Token.
        """
        storage.save_json(TOKENS_FILE, token_info)

    def load_tokens(self):
        return storage.load_json(TOKENS_FILE)

    def get_headless_client(self):
        """
        Constructs a Spotify client whose USER token auto-refreshes on expiry.

        Uses auth_manager + a storage-backed cache handler instead of a static
        access token, so a long Job (>60 min) doesn't 401 mid-run: spotipy calls
        get_access_token() before each request, refreshes when the token is expired,
        and writes the new token back via StorageCacheHandler.

        NOTE: the scope MUST match the one the login granted (app_settings.SCOPE),
        or validate_token() treats the cached token as invalid and falls into the
        interactive auth flow — which cannot work headless.
        """
        if not self.load_tokens():
            raise Exception("No automation tokens found. Please run a manual scan first to authorize.")

        sp_oauth = SpotifyOAuth(
            client_id=app_settings.CLIENT_ID,
            client_secret=app_settings.CLIENT_SECRET,
            redirect_uri=app_settings.REDIRECT_URI,
            scope=app_settings.SCOPE,
            cache_handler=StorageCacheHandler(),
            open_browser=False,       # never pop a browser in a headless Job
            requests_timeout=20,      # bound the token-refresh POST (else defaults to None = unbounded)
        )
        return Spotify(auth_manager=sp_oauth)

    def should_run_now(self):
        # Logic to check if current time matches schedule (Not strictly needed if using Cron)
        # But good for double verification.
        if not self.config.get("enabled"):
            return False
            
        # Simplified: We assume the external trigger only fires when needed, 
        # or we check here. For now, we trust the trigger if enabled.
        return True

automation_manager = AutomationManager()
