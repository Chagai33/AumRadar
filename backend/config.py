import os
from dotenv import load_dotenv

# Try to load from project root .env
load_dotenv()

# Try to load from legacy path if vars are missing
LEGACY_ENV_PATH = r"C:\Aum.MusicRepo\.env"
if not os.getenv("CLIENT_ID") and os.path.exists(LEGACY_ENV_PATH):
    print(f"Loading environment from legacy path: {LEGACY_ENV_PATH}")
    load_dotenv(LEGACY_ENV_PATH)

class Config:
    CLIENT_ID = os.getenv("CLIENT_ID")
    CLIENT_SECRET = os.getenv("CLIENT_SECRET")
    REDIRECT_URI = os.getenv("REDIRECT_URI", "http://127.0.0.1:8888/callback") # Match legacy script port
    SECRET_KEY = os.getenv("SECRET_KEY", "super_secret_key_change_me") 
    FRONTEND_URL = os.getenv("FRONTEND_URL", "http://127.0.0.1:5174")
    
    # Scopes
    SCOPE = 'playlist-modify-public playlist-modify-private user-follow-read user-follow-modify user-library-read user-library-modify user-read-email user-read-private'

settings = Config()
