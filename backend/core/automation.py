
import os
import json
import logging
import datetime
from .storage_manager import storage
from ..config import settings as app_settings
from spotipy.oauth2 import SpotifyOAuth
from spotipy import Spotify

AUTOMATION_FILE = "cache/automation_config.json"
TOKENS_FILE = "cache/automation_tokens.json"

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
        Constructs a Spotify Client using the saved Refresh Token.
        """
        token_info = self.load_tokens()
        if not token_info:
            raise Exception("No automation tokens found. Please run a manual scan first to authorize.")

        # Create OAuth object
        sp_oauth = SpotifyOAuth(
            client_id=app_settings.SPOTIFY_CLIENT_ID,
            client_secret=app_settings.SPOTIFY_CLIENT_SECRET,
            redirect_uri=app_settings.SPOTIFY_REDIRECT_URI,
            scope="user-library-read user-follow-read playlist-modify-private playlist-modify-public user-top-read"
        )

        # Refresh token logic
        if sp_oauth.is_token_expired(token_info):
            new_token = sp_oauth.refresh_access_token(token_info['refresh_token'])
            self.save_tokens(new_token)
            token_info = new_token

        return Spotify(auth=token_info['access_token'])

    def should_run_now(self):
        # Logic to check if current time matches schedule (Not strictly needed if using Cron)
        # But good for double verification.
        if not self.config.get("enabled"):
            return False
            
        # Simplified: We assume the external trigger only fires when needed, 
        # or we check here. For now, we trust the trigger if enabled.
        return True

automation_manager = AutomationManager()
