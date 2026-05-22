import os
import random
import requests
import spotipy

from spotipy.oauth2 import SpotifyOAuth

LASTFM_API_KEY = os.getenv("LASTFM_API_KEY")
LASTFM_USERNAME = os.getenv("LASTFM_USERNAME")

DISCOVERY_PLAYLIST = "Daily Discovery"
HISTORY_PLAYLIST = "Daily Discovery History"

scope = (
    "playlist-modify-private "
    "playlist-modify-public "
    "playlist-read-private "
    "user-top-read "
    "user-read-recently-played "
    "user-library-read"
)

redirect_uri = os.getenv("SPOTIPY_REDIRECT_URI")

if not redirect_uri:
    raise RuntimeError("Missing SPOTIPY_REDIRECT_URI in environment variables")

auth_manager = SpotifyOAuth(
    client_id=os.getenv("SPOTIFY_CLIENT_ID"),
    client_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
    redirect_uri=redirect_uri,
    scope=scope,
    cache_path=None,
    open_browser=False,
    show_dialog=False,
)

refresh_token = os.getenv("SPOTIFY_REFRESH_TOKEN")
if not refresh_token:
    raise RuntimeError("Missing SPOTIFY_REFRESH_TOKEN")

token_info = auth_manager.refresh_access_token(refresh_token)

sp = spotipy.Spotify(auth=token_info["access_token"])


# ---------------- HELPERS ----------------

def normalize_track(track):
    artist = track["artists"][0]["name"].lower()
    name = track["name"].lower()
    return f"{artist} - {name}"


def get_playlist_id(name):
    playlists = sp.current_user_playlists()

    while playlists:
        for p in playlists["items"]:
            if p["name"] == name:
                return p["id"]

        playlists = sp.next(playlists) if playlists.get("next") else None

    raise Exception(f"Playlist not found: {name}")


def get_playlist_tracks(playlist_id):
    tracks = set()
    results = sp.playlist_tracks(playlist_id)

    while results:
        for item in results["items"]:
            track = item.get("track")
            if track and track.get("id"):
                tracks.add(track["id"])

        results = sp.next(results) if results.get("next") else None

    return tracks


def get_liked_tracks():
    tracks = set()
    offset = 0

    while True:
        results = sp.current_user_saved_tracks(limit=50, offset=offset)
        items = results["items"]

        if not items:
            break

        for item in items:
            track = item.get("track")
            if track and track.get("id"):
                tracks.add(track["id"])

        offset += 50

    return tracks


def get_lastfm_tracks():
    tracks = set()

    if not LASTFM_API_KEY or not LASTFM_USERNAME:
        return tracks

    try:
        url = (
            "https://ws.audioscrobbler.com/2.0/"
            "?method=user.getrecenttracks"
            f"&user={LASTFM_USERNAME}"
            f"&api_key={LASTFM_API_KEY}"
            "&format=json"
            "&limit=200"
        )

        response = requests.get(url, timeout=15)
        data = response.json()

        items = data.get("recenttracks", {}).get("track", [])

        for item in items:
            artist = item.get("artist", {}).get("#text", "").lower()
            name = item.get("name", "").lower()

            if artist and name:
                tracks.add(f"{artist} - {name}")

    except Exception as e:
        print(f"Last.fm unavailable: {e}")

    return tracks


def fetch_seed_artists():
    artist_map = {}

    for t in sp.current_user_top_artists(limit=15, time_range="short_term").get("items", []):
        artist_map[t["id"]] = t

    for t in sp.current_user_top_artists(limit=15, time_range="medium_term").get("items", []):
        artist_map[t["id"]] = t

    for item in sp.current_user_recently_played(limit=20).get("items", []):
        track = item.get("track")
        if not track:
            continue
        for a in track.get("artists", []):
            artist_map[a["id"]] = a

    seed = list(artist_map.values())
    random.shuffle(seed)

    return seed[:20]


def fetch_candidates(seed_artists):
    candidates = []

    for artist in seed_artists:
        name = artist.get("name")

        try:
            url = (
                "https://ws.audioscrobbler.com/2.0/"
                "?method=artist.getsimilar"
                f"&artist={name}"
                f"&api_key={LASTFM_API_KEY}"
                "&format=json"
                "&limit=10"
            )

            response = requests.get(url, timeout=15).json()
            similar = response.get("similarartists", {}).get("artist", [])

            for sim in similar:
                sim_name = sim.get("name")
                if not sim_name:
                    continue

                search = sp.search(q=sim_name, type="track", limit=5)
                candidates.extend(search.get("tracks", {}).get("items", []))

        except Exception as e:
            print(f"Seed error {name}: {e}")

    return candidates


# ---------------- MAIN ----------------

print("Loading playlists...")

history_id = get_playlist_id(HISTORY_PLAYLIST)
discovery_id = get_playlist_id(DISCOVERY_PLAYLIST)

history_tracks = get_playlist_tracks(history_id)
liked_tracks = get_liked_tracks()
existing_discovery = get_playlist_tracks(discovery_id)

blacklist = history_tracks | liked_tracks | existing_discovery

print(f"Blacklist size: {len(blacklist)}")

print("Fetching seeds...")
seed_artists = fetch_seed_artists()

print("Seed artists:")
for a in seed_artists:
    print("-", a.get("name"))

print("Fetching candidates...")
candidate_tracks = fetch_candidates(seed_artists)

random.shuffle(candidate_tracks)

unique = {t["id"]: t for t in candidate_tracks if t.get("id")}
candidate_tracks = list(unique.values())

print("Loading Last.fm...")
lastfm_tracks = get_lastfm_tracks()

new_tracks = []
duration = 0

for track in candidate_tracks:
    if not track.get("id"):
        continue

    if track["id"] in blacklist:
        continue

    if normalize_track(track) in lastfm_tracks:
        continue

    if track.get("popularity", 0) > 75:
        continue

    new_tracks.append(track["id"])
    duration += track.get("duration_ms", 0)

    if duration >= 2 * 60 * 60 * 1000:
        break

print(f"Selected {len(new_tracks)} tracks")

if existing_discovery:
    print("Archiving previous discovery...")
    sp.playlist_add_items(history_id, list(existing_discovery))

print("Clearing playlist...")
old = sp.playlist_items(discovery_id)

to_remove = [
    item["track"]["uri"]
    for item in old.get("items", [])
    if item.get("track") and item["track"].get("uri")
]

if to_remove:
    sp.playlist_remove_all_occurrences_of_items(discovery_id, to_remove)

if new_tracks:
    print("Uploading new tracks...")
    sp.playlist_add_items(discovery_id, new_tracks)

print("Done.")