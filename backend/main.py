from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware
from .config import settings
from .routers import auth, scan, cleanup, recon, bootstrap
import os

app = FastAPI(title="Aum Radar")

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
    "http://127.0.0.1:5174"
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
app.include_router(cleanup.router, prefix="/api", tags=["Cleanup"])
app.include_router(recon.router, prefix="/api", tags=["Recon"])
app.include_router(bootstrap.router, prefix="/api", tags=["Bootstrap"])

@app.get("/")
def read_root():
    return {"message": "API Running", "docs": "/docs"}
