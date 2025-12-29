from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from .config import settings
from .routers import auth, scan
import os

from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

app = FastAPI(title="Antigravity Spotify Connect")

# Middleware
# Always force Secure and SameSite=None. This is required for cross-domain auth (Netlify <-> Cloud Run)
# and is verified to work on localhost in modern browsers.
app.add_middleware(
    SessionMiddleware, 
    secret_key=settings.SECRET_KEY, 
    https_only=True, 
    same_site="none"
)

origins = [
    "http://localhost:5173", 
    "http://localhost:5174", 
    "http://127.0.0.1:5174",
    "https://aumradar-838002431698.europe-west1.run.app"
]
env_origins = os.getenv("ALLOWED_ORIGINS")
if env_origins:
    origins.extend(env_origins.split(","))

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins, # Frontend URL
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(auth.router, tags=["Auth"])
app.include_router(scan.router, prefix="/api", tags=["Scan"])

# Serve React Frontend
# We need to check if the path exists to prefer running locally without dist vs production
if os.path.exists("frontend/dist"):
    app.mount("/assets", StaticFiles(directory="frontend/dist/assets"), name="assets")

    @app.get("/{full_path:path}")
    async def serve_react_app(full_path: str):
        # Allow API calls to pass through (though they should be caught by routers above)
        if full_path.startswith("api"):
            return {"error": "API route not found"}
            
        # Serve index.html for all other routes (SPA handling)
        return FileResponse("frontend/dist/index.html")
else:
    @app.get("/")
    def read_root():
        return {"message": "API Running (Frontend not built)", "docs": "/docs"}
