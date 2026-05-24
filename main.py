import os
import time
import random
import requests
import spotipy

from spotipy.oauth2 import SpotifyOAuth
from spotipy.exceptions import SpotifyException

# ---------------- CONFIG ----------------

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
    raise RuntimeError("Missing SPOTIPY_REDIRECT_URI")

refresh_token = os.getenv("SPOTIFY_REFRESH_TOKEN")
if not refresh_token:
    raise RuntimeError("Missing SPOTIFY_REFRESH_TOKEN")

auth_manager = SpotifyOAuth(
    client_id=os.getenv("SPOTIFY_CLIENT_ID"),
    client_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
    redirect_uri=redirect_uri,
    scope=scope,
    cache_path=None,
    open_browser=False,
    show_dialog=False,
)

token_info = auth_manager.refresh_access_token(refresh_token)

sp = spotipy.Spotify(auth=token_info["access_token"])


# ---------------- SPOTIFY SAFE REQUEST ----------------

def sp_request(func, *args, **kwargs):
    for attempt in range(6):
        try:
            return func(*args, **kwargs)

        except SpotifyException as e:
            if e.http_status == 429:
                retry_after = int(e.headers.get("Retry-After", "5"))
                print(f"[RATE LIMIT] Sleeping {retry_after}s")
                time.sleep(retry_after)
                continue

            raise

        except Exception as e:
            print(f"[Spotify error] {e}")
            time.sleep(2 ** attempt)

    raise RuntimeError("Spotify API failed after retries")


def safe_sleep(base=0.35):
    time.sleep(base + random.uniform(0, 0.25))


# ---------------- HELPERS ----------------

def normalize_track(track):
    artist = track["artists"][0]["name"].lower()
    name = track["name"].lower()
    return f"{artist} - {name}"


def get_playlist_id(name):
    playlists = sp_request(sp.current_user_playlists)

    while playlists:
        for p in playlists["items"]:
            if p["name"] == name:
                return p["id"]

        playlists = sp.next(playlists) if playlists.get("next") else None

    raise RuntimeError(f"Playlist not found: {name}")


def get_playlist_tracks(playlist_id):
    tracks = set()

    results = sp_request(sp.playlist_tracks, playlist_id)

    while results:
        for item in results["items"]:
            track = item.get("track")

            if track and track.get("id"):
                tracks.add(track["id"])

        results = sp.next(results) if results.get("next") else None

    return tracks


def get_playlist_track_uris(playlist_id):
    uris = []

    results = sp_request(sp.playlist_items, playlist_id)

    while results:
        for item in results["items"]:
            track = item.get("track")

            if track and track.get("uri"):
                uris.append(track["uri"])

        results = sp.next(results) if results.get("next") else None

    return uris


def get_liked_tracks():
    tracks = set()
    offset = 0

    while True:
        results = sp_request(
            sp.current_user_saved_tracks,
            limit=50,
            offset=offset
        )

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

        data = requests.get(url, timeout=15).json()

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

    short = sp_request(
        sp.current_user_top_artists,
        limit=15,
        time_range="short_term"
    )

    for t in short.get("items", []):
        artist_map[t["id"]] = t

    safe_sleep()

    medium = sp_request(
        sp.current_user_top_artists,
        limit=15,
        time_range="medium_term"
    )

    for t in medium.get("items", []):
        artist_map[t["id"]] = t

    safe_sleep()

    recent = sp_request(
        sp.current_user_recently_played,
        limit=20
    )

    for item in recent.get("items", []):
        track = item.get("track")

        if not track:
            continue

        for a in track.get("artists", []):
            artist_map[a["id"]] = a

    seed = list(artist_map.values())

    random.shuffle(seed)

    return seed[:15]


def fetch_candidates(seed_artists):
    candidates = []

    for artist in seed_artists:
        name = artist.get("name")

        if not name:
            continue

        try:
            url = (
                "https://ws.audioscrobbler.com/2.0/"
                "?method=artist.getsimilar"
                f"&artist={name}"
                f"&api_key={LASTFM_API_KEY}"
                "&format=json"
                "&limit=5"
            )

            data = requests.get(url, timeout=15).json()

            similar = data.get("similarartists", {}).get("artist", [])

            for sim in similar:
                sim_name = sim.get("name")

                if not sim_name:
                    continue

                safe_sleep(0.6)

                result = sp_request(
                    sp.search,
                    q=sim_name,
                    type="track",
                    limit=3
                )

                tracks = result.get("tracks", {}).get("items", [])

                candidates.extend(tracks)

        except Exception as e:
            print(f"Seed error {name}: {e}")

    return candidates


def add_tracks_batch(playlist_id, track_ids, batch_size=100):
    for i in range(0, len(track_ids), batch_size):
        batch = track_ids[i:i + batch_size]

        sp_request(
            sp.playlist_add_items,
            playlist_id,
            batch
        )

        safe_sleep(0.5)


# ---------------- MAIN ----------------

print("Loading playlists...")

history_id = get_playlist_id(HISTORY_PLAYLIST)
discovery_id = get_playlist_id(DISCOVERY_PLAYLIST)

history_tracks = get_playlist_tracks(history_id)
liked_tracks = get_liked_tracks()
existing_discovery = get_playlist_tracks(discovery_id)

existing_discovery_uris = get_playlist_track_uris(discovery_id)

blacklist = history_tracks | liked_tracks | existing_discovery

print(f"Blacklist size: {len(blacklist)}")

print("Fetching seed artists...")
seed_artists = fetch_seed_artists()

print("Fetching candidates...")
candidate_tracks = fetch_candidates(seed_artists)

random.shuffle(candidate_tracks)

unique = {
    t["id"]: t
    for t in candidate_tracks
    if t.get("id")
}

candidate_tracks = list(unique.values())

print("Loading Last.fm...")
lastfm_tracks = get_lastfm_tracks()

new_tracks = []
duration = 0

for track in candidate_tracks:
    track_id = track.get("id")

    if not track_id:
        continue

    if track_id in blacklist:
        continue

    if normalize_track(track) in lastfm_tracks:
        continue

    if track.get("popularity", 0) > 75:
        continue

    new_tracks.append(track_id)

    duration += track.get("duration_ms", 0)

    if duration >= 2 * 60 * 60 * 1000:
        break

print(f"Selected {len(new_tracks)} tracks")

# ---------------- ARCHIVE OLD PLAYLIST ----------------

if existing_discovery_uris:
    print("Archiving previous discovery playlist...")

    add_tracks_batch(
        history_id,
        existing_discovery_uris
    )

# ---------------- REPLACE PLAYLIST ----------------

print("Replacing discovery playlist...")

if new_tracks:
    first_batch = new_tracks[:100]

    sp_request(
        sp.playlist_replace_items,
        discovery_id,
        first_batch
    )

    remaining = new_tracks[100:]

    if remaining:
        add_tracks_batch(
            discovery_id,
            remaining
        )

else:
    sp_request(
        sp.playlist_replace_items,
        discovery_id,
        []
    )

print("Done.")
