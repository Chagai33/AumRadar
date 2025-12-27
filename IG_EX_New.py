import spotipy, logging, time, csv, gspread
from spotipy.oauth2 import SpotifyOAuth
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import NoSuchElementException, TimeoutException, WebDriverException
from oauth2client.service_account import ServiceAccountCredentials

# Replace with your Spotify API credentials
CLIENT_ID = '796b5d9f1aee4442809aa268982ed067'
CLIENT_SECRET = 'de2724f4849246a8a9b1ae595e95d7dd'
REDIRECT_URI = 'http://127.0.0.1:8888/callback'

# Initialize Spotify API client
sp = spotipy.Spotify(auth_manager=SpotifyOAuth(client_id=CLIENT_ID,
                                               client_secret=CLIENT_SECRET,
                                               redirect_uri=REDIRECT_URI,
                                               scope='playlist-read-private user-follow-read user-follow-modify'))

# Google Sheets setup
scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/spreadsheets",
         "https://www.googleapis.com/auth/drive.file", "https://www.googleapis.com/auth/drive"]
creds = ServiceAccountCredentials.from_json_keyfile_name('C:\\Aum.Music\\credentials.json', scope)
client = gspread.authorize(creds)
spreadsheet_url = "https://docs.google.com/spreadsheets/d/1j8jRlbBJi_6GREbj9vhlUvv6SQXAFkZp32M4WLy0Wkk"
sheet = client.open_by_url(spreadsheet_url).worksheet("IG Artist")

# Configure logging
log_file_path = 'script_log.log'
logging.basicConfig(filename=log_file_path, level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


def get_playlist_artists(sp, playlist_url):
    # Get playlist information
    playlist = sp.playlist(playlist_url)
    if playlist is None:
        logging.error(f"Error fetching playlist: {playlist_url}")
        return None
    # Extract artist information
    artists_info = []
    for track in playlist['tracks']['items']:
        for artist in track['track']['artists']:
            artist_name = artist['name']
            spotify_url = artist['external_urls']['spotify']
            artists_info.append((artist_name, spotify_url))
    # Return artist info, playlist name, release date and genre if available
    return artists_info, playlist['name'], playlist.get('release_date', 'None'), playlist.get('genres', ['Unknown'])[0]


def smooth_scroll(driver, button_class):
    try:
        button = WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.CLASS_NAME, button_class))
        )
        driver.execute_script("arguments[0].scrollIntoView(true);", button)
        return True
    except TimeoutException:
        return False


def smooth_scroll_to_element(driver, css_selector):
    try:
        element = driver.find_element_by_css_selector(css_selector)
        driver.execute_script("arguments[0].scrollIntoView(true);", element)
        time.sleep(1)  # Wait one second after scrolling
        return True
    except NoSuchElementException:
        return False


def check_in_db(name):
    """Simple check for artist name in the database."""
    try:
        ig_user_col = sheet.col_values(4)  # IG user column
        artist_name_col = sheet.col_values(1)  # Artist Name column
    except Exception as e:
        logging.error(f"Error accessing Google Sheet: {e}")
        return "No"
    if name in artist_name_col:
        index = artist_name_col.index(name)
        return sheet.cell(index + 1, 4).value or "NONE"
    return "NONE"


def check_instagram_profile(username):
    base_url = "https://www.instagram.com/"
    url = base_url + username
    try:
        temp_driver = webdriver.Chrome()
        temp_driver.get(url)
        time.sleep(2)
        not_found_text = "Sorry, this page isn't available."
        return not (not_found_text in temp_driver.page_source)
    except Exception as e:
        logging.error(f"Error checking Instagram profile {username}: {e}")
        return False
    finally:
        temp_driver.quit()


def scrape_artist_pages(driver, artists_info):
    instagram_accounts = {}
    for name, spotify_url in artists_info:
        instagram_username = "NONE"    # Initialize before the try block
        driver.get(spotify_url)
        try:
            if smooth_scroll(driver, 'iyhPyoc2dxd1Eno74TCO'):
                WebDriverWait(driver, 2).until(
                    EC.element_to_be_clickable((By.CLASS_NAME, 'iyhPyoc2dxd1Eno74TCO'))
                ).click()
                try:
                    instagram_links = WebDriverWait(driver, 3).until(
                        EC.presence_of_all_elements_located((By.CLASS_NAME, 'vcaxBkqK2bKJpUMoXqnP'))
                    )
                    for link in instagram_links:
                        instagram_url = link.get_attribute('href')
                        if 'instagram.com/' in instagram_url:
                            instagram_username = instagram_url.split('instagram.com/')[-1].rstrip('/').split('/')[0].split('?')[0]
                            break
                except (NoSuchElementException, TimeoutException) as e:
                    logging.warning(f"Element not found or timeout occurred: {e}")
        except (NoSuchElementException, TimeoutException) as e:
            logging.warning(f"Element not found or timeout occurred: {e}")

        # Get db_status (simple check)
        db_status = check_in_db(name)
        print(f"{name}: {instagram_username} (In DB: {db_status})")
        key = (name, instagram_username, db_status)

        # Validate Instagram Profile Logic
        if instagram_username != "NONE" and db_status != "NONE":
            spotify_valid = check_instagram_profile(instagram_username)
            db_valid = check_instagram_profile(db_status)
            if db_valid:
                valid_username = db_status
            elif spotify_valid:
                valid_username = instagram_username
            else:
                valid_username = "NONE"
            key = (name, valid_username, db_status)

        instagram_accounts[key] = instagram_accounts.get(key, 0) + 1

    return instagram_accounts


def save_to_csv(instagram_accounts):
    csv_file_path = 'C:\\Users\\administrator\\Downloads\\instagram_accounts.csv'
    while True:
        try:
            with open(csv_file_path, mode='w', newline='', encoding='utf-8-sig') as file:
                writer = csv.writer(file)
                writer.writerow(['Artist Name', 'Instagram Username', 'Count', 'In DB'])
                for (name, username, in_db), count in instagram_accounts.items():
                    writer.writerow([name, username, count, in_db])
            logging.info("Instagram accounts successfully saved to CSV file.")
            break
        except PermissionError as e:
            logging.error(f"PermissionError: Unable to write to the CSV file. Error: {e}")
            print("PermissionError: Close the CSV file and press Enter to retry.")
            input()


def save_to_text(instagram_accounts, playlist_number="None", playlist_name="None", releases_dates="None", genre="None", new_followed_artists=None):
    with open('C:\\Users\\administrator\\Downloads\\instagram_accounts.txt', mode='w', encoding='utf-8') as file:
        file.write("*ᗆum.Music*\n")
        file.write(f"ᑭしᗩᎩしᏆᔑᎢ *{playlist_number}*\n")
        file.write(f"彡 *{playlist_name}* 彡\n\n")
        file.write(f"{releases_dates} Releases\n.\n")
        genre_parts = genre.split("♩")
        genre_formatted = "\n♩".join(genre_parts[1:]) if len(genre_parts) > 1 else genre
        file.write(f"\n♩{genre_formatted}\n\n.\n")
        file.write("*Spotify*\n\n\n")
        file.write("*YouTube*\n\n")
        file.write(".\n📸*IG:* @\n")
        file.write("אם בא לכם לעזור לי לקדם את הפרויקט\n")
        file.write("אשמח שתוסיפו לי עוקב באינסטה,\n")
        file.write("זה לוקח שנייה ועוזר לאלגוריתם להתעורר על צד ימין 🙏\n")
        file.write("instagram.com/aum.music\n")
        file.write("\n\n\n\n\n")
        file.write(f"\n{releases_dates} Releases\n")
        file.write(f"Week #{''.join(filter(str.isdigit, playlist_number))}\n")
        file.write(f"彡 {playlist_name} 彡\n\n")
        file.write("Link in Bio\n.\n")
        file.write(f"\n♩{genre_formatted}\n\n.\n")
        file.write(".\n" * 3)
        file.write("📸@\n")
        file.write(".\n" * 5)
        file.write("#aummusic #aumusic\n")
        file.write("#newmusicrelease #discovermusic #newmusicfriday\n")
        file.write("#spotifyplaylists #playlistcurator\n")
        file.write("#newbeats #newreleases #discoverartists\n")
        file.write("\n" * 5)
        total_artists = len(instagram_accounts)
        found = sum(1 for _, _, in_db in instagram_accounts if in_db == "Yes")
        found_in_db = sum(1 for value in instagram_accounts.values() if value != 'No')
        not_found = total_artists - found - found_in_db
        for (name, username, in_db), count in instagram_accounts.items():
            username_to_write = username if username != 'NONE' else (in_db if in_db != 'No' else '')
            file.write(f"{name}\n@{username_to_write}\n.\n")
        file.write("\n" * 5)
        file.write(f"Total Artists: {total_artists}\nFound: {found}\nFound in DB: {found_in_db}\nNot Found: {not_found}\n")
        if new_followed_artists:
            file.write("\nNewly Followed Artists on Spotify:\n")
            for artist in new_followed_artists:
                file.write(f"{artist}\n")
            file.write(f"Total Newly Followed: {len(new_followed_artists)}\n")


def get_all_followed_artists():
    followed_artists = set()
    results = sp.current_user_followed_artists(limit=50)
    while results:
        artists = results['artists']
        for item in artists['items']:
            followed_artists.add(item['id'])
        if artists['next']:
            results = sp.next(artists)
        else:
            break
    return followed_artists


def get_playlist_artist_ids(playlist_id):
    artist_ids = set()
    results = sp.playlist_tracks(playlist_id)
    while results:
        for item in results['items']:
            track = item['track']
            if track:
                for artist in track['artists']:
                    artist_ids.add(artist['id'])
        if results['next']:
            results = sp.next(results)
        else:
            break
    return artist_ids


def follow_new_artists_in_playlist(playlist_url):
    playlist_id = playlist_url.split('/')[-1].split('?')[0]
    followed_artist_ids = get_all_followed_artists()
    playlist_artist_ids = get_playlist_artist_ids(playlist_id)
    new_artists = []
    for artist_id in playlist_artist_ids:
        if artist_id not in followed_artist_ids:
            sp.user_follow_artists([artist_id])
            artist_info = sp.artist(artist_id)
            new_artists.append(artist_info['name'])
    return new_artists


def main():
    playlist_url = input("Enter the Spotify playlist URL: ")
    artists_info, full_playlist_name, releases_dates, genre = get_playlist_artists(sp, playlist_url)
    try:
        if "彡" in full_playlist_name:
            parts = full_playlist_name.split("彡")
            # Extract name and number as shown in the working example
            playlist_name = parts[1].strip() if len(parts) > 1 else full_playlist_name
            playlist_number = parts[-1].strip()
        else:
            playlist_name = full_playlist_name
            playlist_number = "None"
    except Exception as e:
        print(f"Error processing playlist name: {e}")
        playlist_name, playlist_number = "None", "None"

    driver = webdriver.Chrome()
    try:
        instagram_accounts = scrape_artist_pages(driver, artists_info)
        found_in_db = sum(1 for (name, user, db) in instagram_accounts if db == "Yes")
        print(f"\nProcessed {len(artists_info)} artists.")
        print(f"Found Instagram accounts for {found_in_db} artists.")
        print(f"Instagram accounts not found for {len(artists_info) - found_in_db} artists.")

        create_csv = input("Would you like to save the results to a CSV file? (y/n): ").lower()
        if create_csv == 'y':
            save_to_csv(instagram_accounts)
            print("Instagram accounts saved to CSV file.")

        create_text = input("Would you like to save the results to a text file? (y/n): ").lower()
        if create_text == 'y':
            new_artists = None
            follow_artists = input("Would you like to follow the artists from this playlist on Spotify? (y/n): ").lower()
            if follow_artists == 'y':
                new_artists = follow_new_artists_in_playlist(playlist_url)
                if new_artists:
                    print("New artists followed on Spotify:")
                    for artist in new_artists:
                        print(artist)
                else:
                    print("You are already following all the artists in this playlist.")
            save_to_text(instagram_accounts, playlist_number, playlist_name, releases_dates, genre, new_followed_artists=new_artists)
            print("Instagram accounts saved to text file.")
    finally:
        driver.quit()


if __name__ == "__main__":
    logging.info("Script started")
    main()
    logging.info("Script finished")
