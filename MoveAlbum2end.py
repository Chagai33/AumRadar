import spotipy
from spotipy.oauth2 import SpotifyOAuth
from spotipy.exceptions import SpotifyException
import re
import collections

# פרטי התחברות
CLIENT_ID = '796b5d9f1aee4442809aa268982ed067'
CLIENT_SECRET = 'de2724f4849246a8a9b1ae595e95d7dd'
REDIRECT_URI = 'http://127.0.0.1:8888/callback'
SCOPE = 'playlist-modify-public playlist-modify-private'
BATCH_SIZE = 100  # מגבלת API

def reorder_playlist_by_album_group(sp, playlist_id):
    """
    קוראת את כל השירים בפלייליסט, ומסדרת מחדש כך שכל קבוצה של שירים (אותו אמן ואותו שם אלבום)
    עם 4 שירים ומעלה תועבר כבלוק לסוף הפלייליסט, תוך שמירה על הסדר המקורי בתוך הקבוצה.
    שאר השירים נשארים במקומם.
    """
    # שליפת כל השירים (תמיכה ב-pagination)
    tracks = []
    try:
        results = sp.playlist_items(playlist_id)
    except SpotifyException as e:
        print("שגיאה בשליפת פריטי הפלייליסט:", e)
        return
    tracks.extend(results['items'])
    while results['next']:
        results = sp.next(results)
        tracks.extend(results['items'])
    total_tracks = len(tracks)
    print(f"סה\"כ שירים בפלייליסט: {total_tracks}")

    # בניית מילון לקבוצות: key = (שם האמן הראשי, שם האלבום), value = רשימת (אינדקס, פריט)
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

    # סינון קבוצות עם 4 שירים ומעלה
    qualified_groups = {key: items for key, items in group_dict.items() if len(items) >= 4}
    if not qualified_groups:
        print("לא נמצאו קבוצות עם 4 שירים ומעלה – אין שינוי.")
        return

    # קביעת אינדקסים של כל השירים השייכים לקבוצות המועברות
    qualified_indices = set()
    for items in qualified_groups.values():
        for idx, _ in items:
            qualified_indices.add(idx)

    # שירים שלא שייכים לקבוצות המועברות – נשארים במקומם, לפי הסדר המקורי
    non_group_tracks = [tracks[i] for i in range(len(tracks)) if i not in qualified_indices]

    # עבור כל קבוצה, שומרים את הסדר הפנימי ומסמנים את האינדקס הראשון להחלטת סדר הקבוצות
    sorted_groups = []
    album_log = []  # לפרטי לוג: (אינדקס מקורי, שם שיר, שם אלבום)
    for key, items in qualified_groups.items():
        # מיון לפי אינדקס בתוך הקבוצה
        sorted_items = sorted(items, key=lambda x: x[0])
        first_index = sorted_items[0][0]
        sorted_groups.append((first_index, sorted_items))
    # מיון הקבוצות לפי הופעתן הראשונה בפלייליסט
    sorted_groups.sort(key=lambda x: x[0])

    # יצירת רשימה של כל השירים מהקבוצות, לפי הסדר שנקבע
    album_tracks_sorted = []
    for _, group_items in sorted_groups:
        for orig_idx, item in group_items:
            track = item.get('track', {})
            track_name = track.get('name', 'Unknown')
            album_name = track.get('album', {}).get('name', 'Unknown')
            album_log.append((orig_idx, track_name, album_name))
            album_tracks_sorted.append(item)

    # הסדר החדש: שירי שאינם בקבוצות + קבוצות משולבות בסוף
    new_order_items = non_group_tracks + album_tracks_sorted
    new_order_uris = [item['track']['uri'] for item in new_order_items if item.get('track')]
    original_order_uris = [item['track']['uri'] for item in tracks if item.get('track')]

    if new_order_uris == original_order_uris:
        print("אין שינוי – סדר הפלייליסט כבר כפי שנדרש.")
        return
    else:
        print("מעביר את השירים בסדר חדש (עדכון ב-batches)...")
        try:
            first_batch = new_order_uris[:BATCH_SIZE]
            sp.playlist_replace_items(playlist_id, first_batch)
            print(f"הוחלפו פריטים 0 עד {BATCH_SIZE - 1}")
        except SpotifyException as e:
            print("שגיאה בהחלפת ה-batch הראשון:", e)
            return

        for i in range(BATCH_SIZE, len(new_order_uris), BATCH_SIZE):
            batch = new_order_uris[i:i+BATCH_SIZE]
            try:
                sp.playlist_add_items(playlist_id, batch)
                print(f"נוספו פריטים {i} עד {i + len(batch) - 1}")
            except SpotifyException as e:
                print(f"שגיאה בהוספת batch החל מאינדקס {i}:", e)
                return
        print("הפלייליסט סודר מחדש בהצלחה.")

    # הדפסת לוג מפורט של קבוצות השירים שהועברו
    non_group_count = len(non_group_tracks)
    print("תנועת שירי הקבוצות (מיקומים חדשים):")
    for new_idx, (orig_idx, track_name, album_name) in enumerate(album_log, start=non_group_count):
        print(f"הועבר '{track_name}' מהאלבום '{album_name}' (מיקום מקורי {orig_idx}) -> מיקום חדש {new_idx}")

if __name__ == '__main__':
    # קבלת URL מהמשתמש והוצאת מזהה הפלייליסט
    playlist_url = input("הזן URL של הפלייליסט: ").strip()
    match = re.search(r'playlist/([a-zA-Z0-9]+)', playlist_url)
    if not match:
        print("URL לא תקין.")
    else:
        playlist_id = match.group(1)
        try:
            sp = spotipy.Spotify(auth_manager=SpotifyOAuth(
                client_id=CLIENT_ID,
                client_secret=CLIENT_SECRET,
                redirect_uri=REDIRECT_URI,
                scope=SCOPE))
        except SpotifyException as e:
            print("אימות מול Spotify נכשל:", e)
            exit(1)
        reorder_playlist_by_album_group(sp, playlist_id)
