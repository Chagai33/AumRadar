import spotipy
from spotipy.oauth2 import SpotifyOAuth
from spotipy.exceptions import SpotifyException
import time
import os

# Connection details
CLIENT_ID = '796b5d9f1aee4442809aa268982ed067'
CLIENT_SECRET = 'de2724f4849246a8a9b1ae595e95d7dd'
REDIRECT_URI = 'http://127.0.0.1:8888/callback'
SCOPE = 'playlist-modify-public playlist-modify-private'

# File path to load artist IDs that bypass all filters
FILE_PATH = r'C:\Aum.MusicRepo\venv\Scripts\ExclusionArtists.txt'
NO_FILTER_ARTISTS = []

def load_no_filter_artists(file_path):
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"File not found at {file_path}")
    with open(file_path, 'r', encoding='utf-8') as f:
        return [line.strip() for line in f if line.strip()]

try:
    NO_FILTER_ARTISTS = load_no_filter_artists(FILE_PATH)
except FileNotFoundError as e:
    print(e)
    NO_FILTER_ARTISTS = []

def get_spotify_client():
    return spotipy.Spotify(
        auth_manager=SpotifyOAuth(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            redirect_uri=REDIRECT_URI,
            scope=SCOPE
        )
    )

def extract_playlist_id(playlist_link):
    # Basic extraction from typical playlist URL format
    return playlist_link.split("playlist/")[-1].split("?")[0]

def filter_tracks(tracks):
    filtered_tracks = []
    excluded_tracks = []

    # Collect all track names for checking "clean" vs "explicit"
    track_names = [t['track']['name'].lower() for t in tracks if t['track']]

    for item in tracks:
        if not item['track']:
            continue

        track = item['track']
        name = track['name'].lower()
        duration_ms = track['duration_ms']
        track_uri = track['uri']
        artist_id = track['artists'][0]['id'] if track['artists'] else None
        is_explicit = track.get('explicit', False)

        # Skip filters if artist is in NO_FILTER_ARTISTS
        if artist_id in NO_FILTER_ARTISTS:
            filtered_tracks.append(track_uri)
            continue

        # Name-based filters
        if any(bad_word in name for bad_word in [
            "live", "session", "לייב", "קאבר", "a capella", "FSOE",
            "techno", "extended", "sped up", "speed up",
            "intro", "slow", "remaster", "instrumental"
        ]):
            excluded_tracks.append(track_uri)
            continue

        # Duration-based filter
        if duration_ms < 90000 or duration_ms > 270000:
            excluded_tracks.append(track_uri)
            continue

        # **Updated explicit filtering logic**
        if not is_explicit:
            if any(n == name.replace("clean", "").strip() for n in track_names if n != name):
                excluded_tracks.append(track_uri)
                continue

        filtered_tracks.append(track_uri)

    return filtered_tracks, excluded_tracks

def remove_excluded_tracks(sp, playlist_id, excluded_tracks):
    chunk_size = 100
    for i in range(0, len(excluded_tracks), chunk_size):
        try:
            sp.playlist_remove_all_occurrences_of_items(
                playlist_id=playlist_id,
                items=excluded_tracks[i:i+chunk_size]
            )
        except SpotifyException as e:
            if e.http_status == 429:
                retry_after = int(e.headers.get('Retry-After', 0))
                print(f"Rate-limited. Retrying after {retry_after} seconds.")
                time.sleep(retry_after)
            else:
                print(f"SpotifyException: {e}")

def filter_playlist(playlist_link):
    sp = get_spotify_client()
    playlist_id = extract_playlist_id(playlist_link)

    # Get playlist tracks
    all_tracks = []
    results = sp.playlist_tracks(playlist_id, limit=100, offset=0)
    while results:
        all_tracks.extend(results['items'])
        if results['next']:
            results = sp.next(results)
        else:
            break

    # Apply filters
    filtered_tracks, excluded_tracks = filter_tracks(all_tracks)

    # Remove excluded tracks
    if excluded_tracks:
        remove_excluded_tracks(sp, playlist_id, excluded_tracks)
        print(f"Removed {len(excluded_tracks)} tracks from the playlist.")
    else:
        print("No tracks to remove.")

    print(f"Final kept tracks: {len(filtered_tracks)}")

if __name__ == "__main__":
    link = input("Enter a Spotify playlist link: ")
    filter_playlist(link)

