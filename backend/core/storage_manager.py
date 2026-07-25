import os
import json
import datetime
import logging
from typing import Any, Dict, Optional

# Try importing google cloud storage, handle if not installed (for local dev without it)
try:
    from google.cloud import storage
    GCS_AVAILABLE = True
except ImportError:
    GCS_AVAILABLE = False

# Hard timeout (seconds) for every GCS network call. Kept short so a hung request
# fails cleanly inside Cloud Run's ~10s SIGTERM→SIGKILL grace window instead of
# blocking the whole scan. GCS writes are atomic, so a timed-out write just leaves
# the previous version intact — no corruption.
GCS_TIMEOUT = 6

class StorageManager:
    def __init__(self):
        self.bucket_name = os.getenv("BUCKET_NAME") # We will set this env var in Cloud Run
        self.project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
        self.use_cloud = GCS_AVAILABLE and self.bucket_name is not None
        
        self.local_cache_dir = "cache"
        if not self.use_cloud:
            os.makedirs(self.local_cache_dir, exist_ok=True)
            print(f"StorageManager: Using LOCAL storage in '{self.local_cache_dir}'")
        else:
            print(f"StorageManager: Using GOOGLE CLOUD STORAGE bucket '{self.bucket_name}'")
            self.client = storage.Client()
            self.bucket = self.client.bucket(self.bucket_name)

    def _get_local_path(self, filename: str) -> str:
        # If filename already has the dir, don't double it. 
        # But scanner.py defines paths like "cache/scan_state.json".
        # We should probably normalize to just the filename for GCS, 
        # and keep local path for local.
        
        # Simple approach: If user passes "cache/foo.json", 
        # GCS blob name = "cache/foo.json"
        # Local path = "cache/foo.json"
        return filename

    def save_json(self, filename: str, data: Any):
        if self.use_cloud:
            try:
                blob = self.bucket.blob(filename)
                blob.upload_from_string(
                    json.dumps(data, default=str),
                    content_type='application/json',
                    timeout=GCS_TIMEOUT
                )
                return True
            except Exception as e:
                print(f"Error saving to GCS ({filename}): {e}")
                return False
        else:
            try:
                path = self._get_local_path(filename)
                # Ensure dir exists if filename contains dirs
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, 'w') as f:
                    json.dump(data, f, default=str)
                return True
            except Exception as e:
                print(f"Error saving local file ({filename}): {e}")
                return False

    def load_json(self, filename: str, default: Any = None) -> Any:
        if self.use_cloud:
            try:
                blob = self.bucket.blob(filename)
                if not blob.exists(timeout=GCS_TIMEOUT):
                    return default
                data = json.loads(blob.download_as_string(timeout=GCS_TIMEOUT))
                return data
            except Exception as e:
                # print(f"Error loading from GCS ({filename}): {e}")
                return default
        else:
            path = self._get_local_path(filename)
            if not os.path.exists(path):
                return default
            try:
                with open(path, 'r') as f:
                    return json.load(f)
            except:
                return default

    def exists(self, filename: str) -> bool:
        if self.use_cloud:
            try:
                blob = self.bucket.blob(filename)
                return blob.exists(timeout=GCS_TIMEOUT)
            except:
                return False
        else:
            return os.path.exists(self._get_local_path(filename))

    def get_metadata(self, filename: str) -> Dict[str, Any]:
        """Returns dict with 'size', 'last_updated' (datetime iso format)"""
        if self.use_cloud:
            try:
                blob = self.bucket.get_blob(filename, timeout=GCS_TIMEOUT)
                if not blob:
                    return {}
                return {
                    "size": blob.size,
                    "last_updated": blob.updated.isoformat() if blob.updated else None
                }
            except:
                return {}
        else:
            path = self._get_local_path(filename)
            if os.path.exists(path):
                stat = os.stat(path)
                mtime = datetime.datetime.fromtimestamp(stat.st_mtime)
                return {
                    "size": stat.st_size,
                    "last_updated": mtime.isoformat()
                }
            return {}

    def delete_file(self, filename: str):
        """Delete a file from storage. Silently ignores if not found."""
        if self.use_cloud:
            try:
                blob = self.bucket.blob(filename)
                blob.delete(timeout=GCS_TIMEOUT)
            except Exception as e:
                print(f"Error deleting from GCS ({filename}): {e}")
        else:
            path = self._get_local_path(filename)
            try:
                if os.path.exists(path):
                    os.remove(path)
            except Exception as e:
                print(f"Error deleting local file ({filename}): {e}")

# Global instance
storage = StorageManager()
