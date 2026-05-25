import os
import random
import time
from urllib.parse import quote_plus

import requests
import spotipy
from spotipy.oauth2 import SpotifyOAuth
from spotipy.exceptions import SpotifyException

# ================= CONFIG =================

LASTFM_API_KEY = os.getenv("LASTFM_API_KEY")
LASTFM_USERNAME = os.getenv("LASTFM_USERNAME")

DISCOVERY_PLAYLIST_ID = "4wZv59SXjd7Tjs6diXqzFv"
HISTORY_PLAYLIST_ID = "6VYNXarxZ1VL7NpI54Gx53"

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

# ================= AUTH =================

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

sp = spotipy.Spotify(
    auth=token_info["access_token"],
    requests_timeout=30,
    retries=10,
    status_retries=10,
    backoff_factor=2,
)

USER_ID = sp.current_user()["id"]

# ================= HELPERS =================


def spotify_call(func, *args, **kwargs):
    while True:
        try:
            return func(*args, **kwargs)

        except SpotifyException as e:
            if e.http_status == 429:
                retry_after = int(getattr(e, "headers", {}).get("Retry-After", 30))
                print(f"Spotify rate limit. Sleep {retry_after}s")
                time.sleep(retry_after + 2)
                continue
            raise

        except Exception as e:
            print(f"Spotify error: {e}")
            time.sleep(5)


def chunked(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def normalize_track(track):
    artist = track["artists"][0]["name"].strip().lower()
    title = track["name"].strip().lower()
    return f"{artist} - {title}"


def get_playlist(playlist_id):
    return spotify_call(
        sp.playlist,
        playlist_id,
        fields="id,name,owner(id),public,collaborative,snapshot_id",
    )


def get_playlist_items(playlist_id):
    items = []
    offset = 0

    while True:
        results = spotify_call(
            sp.playlist_items,
            playlist_id,
            offset=offset,
            limit=100,
            fields="items(track(id,uri,name,artists(name))),total,next",
        )

        batch = results.get("items", [])
        if not batch:
            break

        items.extend(batch)
        offset += len(batch)

    return items


def get_playlist_tracks(playlist_id):
    tracks = []
    for item in get_playlist_items(playlist_id):
        track = item.get("track")
        if track and track.get("id"):
            tracks.append(track)
    return tracks


def get_playlist_track_ids(playlist_id):
    return {
        track["id"]
        for track in get_playlist_tracks(playlist_id)
        if track.get("id")
    }


def get_liked_tracks():
    tracks = set()
    offset = 0

    while True:
        results = spotify_call(
            sp.current_user_saved_tracks,
            limit=50,
            offset=offset,
        )
        items = results.get("items", [])
        if not items:
            break

        for item in items:
            track = item.get("track")
            if track and track.get("id"):
                tracks.add(track["id"])

        offset += len(items)

    return tracks


def get_lastfm_tracks():
    tracks = set()

    if not LASTFM_API_KEY or not LASTFM_USERNAME:
        print("Last.fm credentials missing")
        return tracks

    try:
        url = (
            "https://ws.audioscrobbler.com/2.0/"
            "?method=user.getrecenttracks"
            f"&user={quote_plus(LASTFM_USERNAME)}"
            f"&api_key={LASTFM_API_KEY}"
            "&format=json"
            "&limit=200"
        )

        response = requests.get(url, timeout=20)
        response.raise_for_status()
        data = response.json()

        items = data.get("recenttracks", {}).get("track", [])
        if not items:
            print("Last.fm account empty")
            return tracks

        for item in items:
            artist = item.get("artist", {}).get("#text", "").strip().lower()
            title = item.get("name", "").strip().lower()
            if artist and title:
                tracks.add(f"{artist} - {title}")

    except Exception as e:
        print(f"Last.fm error: {e}")

    return tracks


def fetch_seed_artists():
    artist_map = {}

    short = spotify_call(
        sp.current_user_top_artists,
        limit=20,
        time_range="short_term",
    )
    for artist in short.get("items", []):
        artist_map[artist["id"]] = artist

    medium = spotify_call(
        sp.current_user_top_artists,
    )
