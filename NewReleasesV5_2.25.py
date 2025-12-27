import os
import sys
import json
import time
import datetime
from datetime import date
import logging
import requests
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from spotipy.exceptions import SpotifyException
from dotenv import load_dotenv
from flask import Flask, request
from concurrent.futures import ThreadPoolExecutor, as_completed
import re
import collections

# Load environment variables from .env file
load_dotenv(r"C:\Aum.MusicRepo\.env")

# Constants and configuration
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REDIRECT_URI = 'http://127.0.0.1:8888/callback'
SCOPE = ('playlist-modify-public playlist-modify-private user-follow-read '
         'user-follow-modify user-library-read user-library-modify user-read-email user-read-private')
ARTISTS_FILE = 'followed_artists.json'
EXCLUSION_FILE = 'ExclusionArtists.txt'
NO_FILTER_FILE = 'ArtistNoFilter.txt'
CACHE_FILE = 'script_cache.json'
LOG_FILENAME = 'NewReleasesLogs.log'

# Configure logging to file and stdout
logging.basicConfig(level=logging.INFO, handlers=[logging.FileHandler(LOG_FILENAME), logging.StreamHandler(sys.stdout)])

def log_message(message):
    logging.info(message)

# --- Utility Functions ---
def load_ids_from_file(file_name):
    try:
        if os.path.exists(file_name):
            with open(file_name, 'r', encoding='utf-8') as f:
                return [line.strip() for line in f if line.strip()]
        else:
            log_message(f"File {file_name} not found. Proceeding without it.")
            return []
    except Exception as e:
        log_message(f"Error loading file {file_name}: {e}")
        return []

def load_artists_from_file(file_name):
    try:
        if os.path.exists(file_name):
            with open(file_name, 'r', encoding='utf-8') as f:
                return json.load(f)
        else:
            log_message(f"File {file_name} not found. Proceeding without it.")
            return {}
    except Exception as e:
        log_message(f"Error loading file {file_name}: {e}")
        return {}

def save_json_to_file(file_name, data):
    try:
        with open(file_name, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        log_message(f"Error saving to file {file_name}: {e}")

# --- Spotify Functions ---
def get_spotify_client():
    sp_oauth = SpotifyOAuth(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        redirect_uri=REDIRECT_URI,
        scope=SCOPE
    )
    return spotipy.Spotify(auth_manager=sp_oauth)

def get_followed_artists(sp):
    artists = []
    next_page = None
    while True:
        try:
            results = sp.current_user_followed_artists(limit=50, after=next_page)
            while results:
                artists.extend(results['artists']['items'])
                if len(artists) % 50 == 0:
                    first_artist_name = artists[-50]['name']
                    log_message(f"Fetched {len(artists)} artists. First artist in this batch: {first_artist_name}")
                next_page = results['artists']['cursors']['after']
                if next_page:
                    results = sp.current_user_followed_artists(limit=50, after=next_page)
                    time.sleep(5)
                else:
                    results = None
            break
        except SpotifyException as e:
            log_message(f"SpotifyException occurred: {e}. Retrying in 5 seconds...")
            time.sleep(10)
        except Exception as e:
            log_message(f"Error fetching followed artists: {e}. Retrying in 5 seconds...")
            time.sleep(10)
    artists_dict = {artist['id']: artist for artist in artists}
    save_json_to_file(ARTISTS_FILE, artists_dict)
    return artists_dict

def check_for_artist_changes(sp):
    old_artists = load_artists_from_file(ARTISTS_FILE)
    new_artists = get_followed_artists(sp)
    total_artists = len(new_artists)
    log_message(f"Found {total_artists} artists.")
    removed_artists = set(old_artists) - set(new_artists)
    added_artists = set(new_artists) - set(old_artists)
    save_json_to_file(ARTISTS_FILE, new_artists)
    if removed_artists or added_artists:
        log_message(f"Found changes in followed artists. Removed: {len(removed_artists)}, Added: {len(added_artists)}")
    return list(new_artists.values())

# Global cache for artist albums
ALBUM_CACHE = {}

def get_artist_albums(sp, artist_id):
    if artist_id in ALBUM_CACHE:
        return ALBUM_CACHE[artist_id]
    while True:
        try:
            albums = sp.artist_albums(artist_id, include_groups='single,album', limit=5)['items']
            ALBUM_CACHE[artist_id] = albums
            return albums
        except SpotifyException as e:
            if e.http_status == 429:
                retry_after = int(e.headers.get('Retry-After', 0))
                log_message(f"Error 429: Too Many Requests. Retrying after {retry_after} seconds.")
                time.sleep(retry_after)
            else:
                log_message(f"SpotifyException occurred: {e}")
                return []
        except Exception as e:
            log_message(f"Error fetching albums for artist {artist_id}: {e}")
            return []

def get_normalized_key(track):
    normalized_name = track['name'].lower().strip()
    artists = [artist['name'].lower().strip() for artist in track.get('artists', [])][:2]
    return (normalized_name, tuple(artists))

def filter_tracks(tracks, no_filter_artists):
    filtered_tracks = []
    excluded_tracks = []
    basic_tracks = []
    forbidden_words = [" live ", "session", "לייב", "קאבר", "a capella", "acapella", "FSOE",
                       "techno", "extended", "sped up", "speed up", "intro", "slow", "remaster", "instrumental"]

    for track in tracks:
        name = track['name'].lower()
        duration_ms = track['duration_ms']
        artist_id = track['artists'][0]['id']
        if artist_id in no_filter_artists:
            filtered_tracks.append(track['uri'])
            continue
        if any(forbidden in name for forbidden in forbidden_words):
            excluded_tracks.append(track['uri'])
            continue
        if duration_ms < 90000 or duration_ms > 270000:
            excluded_tracks.append(track['uri'])
            continue
        basic_tracks.append(track)

    groups = {}
    for track in basic_tracks:
        key = get_normalized_key(track)
        groups.setdefault(key, []).append(track)

    for key, group in groups.items():
        explicit_tracks = [t for t in group if t.get('explicit', False)]
        non_explicit_tracks = [t for t in group if not t.get('explicit', False)]
        if explicit_tracks:
            for t in explicit_tracks:
                filtered_tracks.append(t['uri'])
            for t in non_explicit_tracks:
                excluded_tracks.append(t['uri'])
            log_message(
                f"Group {key}: explicit found. Filtered {len(explicit_tracks)} explicit tracks, excluded {len(non_explicit_tracks)} non-explicit tracks.")
        else:
            for t in group:
                filtered_tracks.append(t['uri'])
            log_message(f"Group {key}: no explicit found. Added {len(group)} tracks to filtered.")

    print(f"Filtered tracks count: {len(filtered_tracks)}")
    print(f"Excluded tracks count: {len(excluded_tracks)}")
    log_message(f"Filtered tracks count: {len(filtered_tracks)}")
    log_message(f"Excluded tracks count: {len(excluded_tracks)}")
    return filtered_tracks, excluded_tracks

def create_playlist(sp, user_id, name):
    while True:
        try:
            playlist = sp.user_playlist_create(user_id, name, public=True)
            return playlist['id']
        except SpotifyException as e:
            if e.http_status == 429:
                retry_after = int(e.headers.get('Retry-After', 0))
                log_message(f"Error 429: Too Many Requests. Retrying after {retry_after} seconds.")
                time.sleep(retry_after)
            else:
                log_message(f"SpotifyException occurred while creating playlist: {e}")
                return None
        except Exception as e:
            log_message(f"Error creating playlist: {e}")
            return None

def add_tracks_to_playlist(sp, playlist_id, tracks):
    CHUNK_SIZE = 100
    for i in range(0, len(tracks), CHUNK_SIZE):
        chunk = tracks[i:i + CHUNK_SIZE]
        while True:
            try:
                sp.user_playlist_add_tracks(user=sp.current_user()['id'], playlist_id=playlist_id, tracks=chunk)
                log_message(f"Added {len(chunk)} tracks to playlist {playlist_id}")
                break
            except SpotifyException as e:
                if e.http_status == 429:
                    retry_after = int(e.headers.get('Retry-After', 0))
                    log_message(f"Error 429: Too Many Requests. Retrying after {retry_after} seconds.")
                    time.sleep(retry_after)
                else:
                    log_message(f"SpotifyException occurred while adding tracks to playlist {playlist_id}: {e}")
                    return
            except Exception as e:
                log_message(f"Error adding tracks to playlist {playlist_id}: {e}")
                return

def get_tracks_for_albums_in_batch(sp, album_ids):
    all_tracks = {}
    batch_size = 20
    idx = 0
    while idx < len(album_ids):
        chunk = album_ids[idx:idx + batch_size]
        try:
            albums_data = sp.albums(chunk)
            for album in albums_data['albums']:
                if album and 'id' in album:
                    aid = album['id']
                    if 'tracks' in album and album['tracks']['items']:
                        all_tracks[aid] = album['tracks']['items']
                    else:
                        all_tracks[aid] = []
        except SpotifyException as e:
            if e.http_status == 429:
                retry_after = int(e.headers.get('Retry-After', 0))
                log_message(f"429 Too Many Requests. Retrying after {retry_after} seconds.")
                time.sleep(retry_after)
            else:
                log_message(f"SpotifyException: {e}")
        except Exception as e:
            log_message(f"Error in batch fetch: {e}")
        idx += batch_size
        time.sleep(0.2)
    return all_tracks

def get_new_releases(sp, artist_id, start_date, end_date):
    new_releases = []
    try:
        releases = get_artist_albums(sp, artist_id)
        for album in releases:
            try:
                release_date = datetime.datetime.strptime(album['release_date'], '%Y-%m-%d').date()
                if start_date <= release_date <= end_date:
                    new_releases.append(album)
            except ValueError:
                log_message(f"Ignoring album '{album['name']}' with incomplete release date: {album['release_date']}")
                continue
    except SpotifyException as e:
        log_message(f"SpotifyException occurred while fetching new releases for artist {artist_id}: {e}")
        if e.http_status == 429:
            log_message("Rate limit hit, retrying after a short delay...")
            time.sleep(10)
            return get_new_releases(sp, artist_id, start_date, end_date)
    except Exception as e:
        log_message(f"Error fetching new releases for artist {artist_id}: {e}")
    return new_releases

def process_artist(sp, artist, exclusion_artists, no_filter_artists, start_date, end_date):
    artist_id = artist['id']
    if artist_id in exclusion_artists:
        return ([], [])
    log_message(f"Processing artist: {artist['name']}")
    new_releases = get_new_releases(sp, artist_id, start_date, end_date)
    filtered = []
    excluded = []
    if new_releases:
        album_ids = [release['id'] for release in new_releases]
        batched_tracks = get_tracks_for_albums_in_batch(sp, album_ids)
        for release in new_releases:
            log_message(f"Found new release: {release['name']} by {artist['name']} on {release['release_date']}")
            aid = release['id']
            try:
                if aid in batched_tracks:
                    f_tracks, e_tracks = filter_tracks(batched_tracks[aid], no_filter_artists)
                    filtered.extend(f_tracks)
                    excluded.extend(e_tracks)
            except SpotifyException as e:
                log_message(f"SpotifyException occurred while processing tracks for release {release['name']}: {e}")
            except Exception as e:
                log_message(f"Error processing tracks for release {release['name']}: {e}")
    return (filtered, excluded)

def save_new_releases_to_playlist(sp, start_date=None, end_date=None, artist_range=None):
    today = datetime.date.today()
    if not start_date or not end_date:
        start_date = today - datetime.timedelta(days=7)
        end_date = today

    log_message(f"Searching for new releases between {start_date} and {end_date}")
    user_id = sp.current_user()['id']
    artists = load_artists_from_file(ARTISTS_FILE)
    exclusion_artists = load_ids_from_file(EXCLUSION_FILE)
    no_filter_artists = load_ids_from_file(NO_FILTER_FILE)

    if artist_range:
        start, end = artist_range
        artists = dict(list(artists.items())[start - 1:end])
    else:
        artists = artists

    all_new_tracks = []
    all_excluded_tracks = []
    artists_list = list(artists.values())
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(process_artist, sp, artist, exclusion_artists, no_filter_artists, start_date,
                                   end_date): artist for artist in artists_list}
        for future in as_completed(futures):
            f_tracks, e_tracks = future.result()
            all_new_tracks.extend(f_tracks)
            all_excluded_tracks.extend(e_tracks)

    if all_new_tracks:
        playlist_name = f"New Releases {start_date.strftime('%d.%m.%Y')} - {end_date.strftime('%d.%m.%Y')}"
        playlist_id = create_playlist(sp, user_id, playlist_name)
        if playlist_id:
            add_tracks_to_playlist(sp, playlist_id, all_new_tracks)
            log_message(f"Playlist created: {playlist_name}")

    if all_excluded_tracks:
        excluded_playlist_name = "Exclusion Songs"
        excluded_playlist_id = create_playlist(sp, user_id, excluded_playlist_name)
        if excluded_playlist_id:
            add_tracks_to_playlist(sp, excluded_playlist_id, all_excluded_tracks)
            log_message(f"Playlist created: {excluded_playlist_name}")

    log_message(f"Search completed for the period {start_date} to {end_date}")

# --- REORDER FUNCTION ---
def reorder_playlist_by_album_group(sp, playlist_id):
    """
    קוראת את כל השירים בפלייליסט, ומסדרת מחדש כך שכל קבוצה של שירים
    (אותו אמן ואותו שם אלבום) עם 4 שירים ומעלה תועבר כבלוק לסוף הפלייליסט,
    תוך שמירה על הסדר המקורי בתוך הקבוצה.
    שאר השירים נשארים במקומם.
    """
    BATCH_SIZE = 100
    try:
        results = sp.playlist_items(playlist_id)
    except SpotifyException as e:
        log_message(f"שגיאה בשליפת פריטי הפלייליסט: {e}")
        return
    tracks = results['items']
    while results['next']:
        results = sp.next(results)
        tracks.extend(results['items'])
    total_tracks = len(tracks)
    log_message(f"סה\"כ שירים בפלייליסט: {total_tracks}")

    group_dict = {}
    for idx, item in enumerate(tracks):
        track = item.get('track')
        if not track:
            continue
        album = track.get('album')
        if not album:
            continue
        album_name = album.get('name', 'Unknown')
        artists = track.get('artists', [])
        primary_artist = artists[0].get('name', 'Unknown') if artists else 'Unknown'
        key = (primary_artist, album_name)
        group_dict.setdefault(key, []).append((idx, item))

    qualified_groups = {key: items for key, items in group_dict.items() if len(items) >= 4}
    if not qualified_groups:
        log_message("לא נמצאו קבוצות עם 4 שירים ומעלה – אין שינוי.")
        return

    qualified_indices = set()
    for items in qualified_groups.values():
        for idx, _ in items:
            qualified_indices.add(idx)

    non_group_tracks = [tracks[i] for i in range(len(tracks)) if i not in qualified_indices]

    sorted_groups = []
    album_log = []
    for key, items in qualified_groups.items():
        sorted_items = sorted(items, key=lambda x: x[0])
        first_index = sorted_items[0][0]
        sorted_groups.append((first_index, sorted_items))
    sorted_groups.sort(key=lambda x: x[0])

    album_tracks_sorted = []
    for _, group_items in sorted_groups:
        for orig_idx, item in sorted(group_items, key=lambda x: x[0]):
            track = item.get('track', {})
            track_name = track.get('name', 'Unknown')
            album_name = track.get('album', {}).get('name', 'Unknown')
            album_log.append((orig_idx, track_name, album_name))
            album_tracks_sorted.append(item)

    new_order_items = non_group_tracks + album_tracks_sorted
    new_order_uris = [item['track']['uri'] for item in new_order_items if item.get('track')]
    original_order_uris = [item['track']['uri'] for item in tracks if item.get('track')]

    if new_order_uris == original_order_uris:
        log_message("אין שינוי – סדר הפלייליסט כבר כפי שנדרש.")
        return
    else:
        log_message("מעביר את השירים בסדר חדש (עדכון ב-batches)...")
        try:
            first_batch = new_order_uris[:BATCH_SIZE]
            sp.playlist_replace_items(playlist_id, first_batch)
            log_message(f"הוחלפו פריטים 0 עד {BATCH_SIZE - 1}")
        except SpotifyException as e:
            log_message(f"שגיאה בהחלפת ה-batch הראשון: {e}")
            return

        for i in range(BATCH_SIZE, len(new_order_uris), BATCH_SIZE):
            batch = new_order_uris[i:i+BATCH_SIZE]
            try:
                sp.playlist_add_items(playlist_id, batch)
                log_message(f"נוספו פריטים {i} עד {i + len(batch) - 1}")
            except SpotifyException as e:
                log_message(f"שגיאה בהוספת batch החל מאינדקס {i}: {e}")
                return
        log_message("הפלייליסט סודר מחדש בהצלחה.")
        non_group_count = len(non_group_tracks)
        log_message("תנועת שירי הקבוצות (מיקומים חדשים):")
        for new_idx, (orig_idx, track_name, album_name) in enumerate(album_log, start=non_group_count):
            log_message(f"הועבר '{track_name}' מהאלבום '{album_name}' (מיקום מקורי {orig_idx}) -> מיקום חדש {new_idx}")

# --- Flask Web Interface ---
app = Flask(__name__)

@app.route('/', methods=['GET', 'POST'])
def index():
    result = ""
    last_modified_info = ""
    artist_count_info = ""

    if os.path.exists(ARTISTS_FILE):
        last_modified_time = os.path.getmtime(ARTISTS_FILE)
        last_modified_date = datetime.date.fromtimestamp(last_modified_time).strftime('%d.%m.%Y')
        days_since_last_update = (datetime.date.today() - datetime.date.fromtimestamp(last_modified_time)).days
        artists_in_file = load_artists_from_file(ARTISTS_FILE)
        num_artists = len(artists_in_file)
        last_modified_info = f"הקובץ עודכן לאחרונה בתאריך {last_modified_date}, לפני {days_since_last_update} ימים."
        artist_count_info = f"מספר האמנים בקובץ: {num_artists}"
    else:
        last_modified_info = "טרם נוצר קובץ אמנים."
        artist_count_info = "מספר האמנים בקובץ: 0"

    if request.method == 'POST':
        update_artists = request.form.get('update_artists', 'no')
        date_option = request.form.get('date_option', 'last7')
        artist_range_str = request.form.get('artist_range', '')
        days_back_str = request.form.get('days_back', '')
        custom_start_str = request.form.get('custom_start', '')
        custom_end_str = request.form.get('custom_end', '')

        today = datetime.date.today()

        if date_option == 'lastX':
            try:
                days_back = int(days_back_str)
            except ValueError:
                days_back = 7
            start_date = today - datetime.timedelta(days=days_back)
            end_date = today
        elif date_option == 'lastWeek':
            start_date = today - datetime.timedelta(days=today.weekday() + 1)
            end_date = start_date + datetime.timedelta(days=6)
        elif date_option == 'custom':
            try:
                start_date = datetime.date.fromisoformat(custom_start_str)
                end_date = datetime.date.fromisoformat(custom_end_str)
            except ValueError:
                start_date = today - datetime.timedelta(days=7)
                end_date = today
        else:
            start_date = today - datetime.timedelta(days=7)
            end_date = today

        artist_range = None
        if artist_range_str:
            try:
                parts = artist_range_str.split('-')
                if len(parts) == 2:
                    artist_range = (int(parts[0].strip()), int(parts[1].strip()))
            except:
                artist_range = None

        try:
            sp = get_spotify_client()
            if update_artists.lower() == 'yes':
                artists = check_for_artist_changes(sp)
            else:
                artists = load_artists_from_file(ARTISTS_FILE)
                if not artists:
                    log_message("No artists found, updating artist list...")
                    artists = check_for_artist_changes(sp)
            save_new_releases_to_playlist(sp, start_date, end_date, artist_range)
            result = f"סיימנו עדכון לתאריכים {start_date} עד {end_date}."
        except Exception as e:
            result = f"שגיאה התרחשה: {e}"

    # Read log file to display
    logs = ""
    if os.path.exists(LOG_FILENAME):
        with open(LOG_FILENAME, 'r', encoding='utf-8') as f:
            logs = f.read()

    # הוספת קישור לדף סידור מחדש כך שהמשתמש לא יצטרך להקליד /reorder ידנית
    extra_link = '<p><a href="/reorder">לחץ כאן לסידור מחדש של פלייליסט (אלבומים בסוף)</a></p>'

    return f"""
    <html>
      <head>
         <title>Spotify New Releases Update</title>
         <meta charset="utf-8">
         <style>
           body {{
               font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif;
               margin: 20px; 
               background: #f4f4f4;
           }}
           h1, h2, h3, h4 {{
               font-weight: 600;
           }}
           .container {{
               max-width: 800px;
               margin: 0 auto;
               background: #fff;
               padding: 20px;
               box-shadow: 0 0 10px rgba(0,0,0,0.1);
           }}
           label {{
               display: inline-block;
               margin-top: 10px;
               font-weight: 600;
           }}
           input[type="text"], input[type="number"], input[type="date"], select {{
               width: 250px;
               padding: 8px;
               margin-top: 5px;
               border: 1px solid #ccc;
               border-radius: 4px;
           }}
           .radio-group {{
               margin: 10px 0;
           }}
           .radio-group label {{
               margin-right: 15px;
               font-weight: normal;
           }}
           input[type="radio"] {{
               margin-right: 5px;
           }}
           .button {{
               background-color: #007bff;
               color: white;
               padding: 10px 20px;
               border: none;
               border-radius: 4px;
               cursor: pointer;
               margin-top: 20px;
           }}
           .button:hover {{
               background-color: #0056b3;
           }}
           textarea {{
               width: 100%;
               height: 300px;
               margin-top: 10px;
               border: 1px solid #ccc;
               border-radius: 4px;
               padding: 10px;
               font-family: monospace;
               font-size: 0.9em;
           }}
           .info-box {{
               background: #e9ecef;
               padding: 10px;
               margin-bottom: 15px;
               border-radius: 4px;
           }}
         </style>
      </head>
      <body>
        <div class="container">
          <h1>Spotify New Releases Update</h1>
          <div class="info-box">
             <strong>{last_modified_info}</strong><br>
             <strong>{artist_count_info}</strong>
          </div>
          <form method="post">
            <div class="radio-group">
              <label>לעדכן את רשימת האמנים?</label><br>
              <label><input type="radio" name="update_artists" value="yes">כן</label>
              <label><input type="radio" name="update_artists" value="no" checked>לא</label>
            </div>
            <label>טווח אמנים (לדוגמה 300-400):</label><br>
            <input type="text" name="artist_range" placeholder="Optional">
            <h3>בחירת טווח תאריכים:</h3>
            <div class="radio-group">
              <label><input type="radio" name="date_option" value="lastX" checked> אחרון X ימים</label><br>
              <label><input type="radio" name="date_option" value="lastWeek"> השבוע האחרון (ראשון-שבת)</label><br>
              <label><input type="radio" name="date_option" value="custom"> מותאם (YYYY-MM-DD)</label>
            </div>
            <label>מספר ימים (לאפשרות 'אחרון X ימים'):</label><br>
            <input type="number" name="days_back" value="7"><br>
            <label>תאריך התחלה (לאפשרות 'מותאם'):</label><br>
            <input type="date" name="custom_start" placeholder="YYYY-MM-DD"><br>
            <label>תאריך סיום (לאפשרות 'מותאם'):</label><br>
            <input type="date" name="custom_end" placeholder="YYYY-MM-DD"><br>
            <input type="submit" class="button" value="Run Update">
          </form>
          {extra_link}
          <h2>תוצאה:</h2>
          <p>{result if result else "לא בוצע עדכון."}</p>
          <h2>Log Output:</h2>
          <textarea readonly>{logs}</textarea>
        </div>
      </body>
    </html>
    """

# --- New Endpoint for Reordering Playlist ---
@app.route('/reorder', methods=['GET', 'POST'])
def reorder_endpoint():
    result = ""
    if request.method == 'POST':
        playlist_url = request.form.get('playlist_url', '').strip()
        if not playlist_url:
            result = "יש לספק URL של פלייליסט."
        else:
            match = re.search(r'playlist/([a-zA-Z0-9]+)', playlist_url)
            if not match:
                result = "URL לא תקין."
            else:
                playlist_id = match.group(1)
                try:
                    sp = get_spotify_client()
                    reorder_playlist_by_album_group(sp, playlist_id)
                    result = "הפלייליסט סודר מחדש בהצלחה."
                except Exception as e:
                    result = f"שגיאה במהלך הסידור: {e}"
    return f"""
    <html>
      <head>
         <title>Reorder Playlist</title>
         <meta charset="utf-8">
         <style>
           body {{
               font-family: "Segoe UI", Tahoma, Geneva, Verdana, sans-serif;
               margin: 20px; 
               background: #f4f4f4;
           }}
           .container {{
               max-width: 600px;
               margin: 0 auto;
               background: #fff;
               padding: 20px;
               box-shadow: 0 0 10px rgba(0,0,0,0.1);
           }}
           input[type="text"] {{
               width: 100%;
               padding: 8px;
               margin-top: 10px;
               border: 1px solid #ccc;
               border-radius: 4px;
           }}
           .button {{
               background-color: #28a745;
               color: white;
               padding: 10px 20px;
               border: none;
               border-radius: 4px;
               cursor: pointer;
               margin-top: 20px;
           }}
           .button:hover {{
               background-color: #218838;
           }}
         </style>
      </head>
      <body>
        <div class="container">
          <h1>Reorder Playlist</h1>
          <form method="post">
            <label>הזן URL של הפלייליסט:</label>
            <input type="text" name="playlist_url" placeholder="https://open.spotify.com/playlist/..." />
            <input type="submit" class="button" value="סדר מחדש" />
          </form>
          <p>{result}</p>
        </div>
      </body>
    </html>
    """

if __name__ == '__main__':
    app.run(debug=True)
