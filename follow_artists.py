import spotipy
from spotipy.oauth2 import SpotifyOAuth

# הגדרת פרטי האפליקציה שלך
CLIENT_ID = '796b5d9f1aee4442809aa268982ed067'
CLIENT_SECRET = 'de2724f4849246a8a9b1ae595e95d7dd'
REDIRECT_URI = 'http://127.0.0.1:8888/callback'
SCOPE = 'playlist-read-private user-follow-modify'

# התחברות עם OAuth2
sp = spotipy.Spotify(auth_manager=SpotifyOAuth(client_id=CLIENT_ID,
                                               client_secret=CLIENT_SECRET,
                                               redirect_uri=REDIRECT_URI,
                                               scope=SCOPE))


# פונקציה להוצאת רשימת האמנים ועשיית Follow
def follow_artists_from_playlist(playlist_url):
    # קבלת ID הפלייליסט מה-URL
    playlist_id = playlist_url.split('/')[-1].split('?')[0]

    # שליפת כל הפריטים מהפלייליסט
    results = sp.playlist_tracks(playlist_id)
    tracks = results['items']

    # אחזור על כל השירים בפלייליסט
    artist_ids = set()  # נשתמש בסט כדי להימנע מכפילויות
    for track in tracks:
        # הוצאת האמנים הראשיים ואמני המשנה
        for artist in track['track']['artists']:
            artist_ids.add(artist['id'])

    # ביצוע Follow לכל האמנים
    artist_ids = list(artist_ids)  # הפיכת הסט לרשימה
    for i in range(0, len(artist_ids), 50):  # 50 זה המספר המקסימלי בבקשה אחת
        sp.user_follow_artists(artist_ids[i:i + 50])
        print(f"Followed {len(artist_ids[i:i + 50])} artists")


# דוגמה לשימוש בפונקציה
playlist_url = input("Please enter the playlist URL: ")
follow_artists_from_playlist(playlist_url)
