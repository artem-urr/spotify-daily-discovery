import os
import base64
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

auth_manager = SpotifyOAuth(
    client_id=os.getenv("SPOTIFY_CLIENT_ID"),
    client_secret=os.getenv("SPOTIFY_CLIENT_SECRET"),
    redirect_uri=os.getenv("SPOTIFY_REDIRECT_URI"),
    scope=scope,
    cache_path=None,
    open_browser=False,
    show_dialog=False,
)

refresh_token = os.getenv("SPOTIFY_REFRESH_TOKEN")

token_info = auth_manager.refresh_access_token(refresh_token)

sp = spotipy.Spotify(auth=token_info["access_token"])


def get_playlist_id(name):
    playlists = sp.current_user_playlists()

    while playlists:
        for p in playlists["items"]:
            if p["name"] == name:
                return p["id"]

        if playlists["next"]:
            playlists = sp.next(playlists)
        else:
            playlists = None

    raise Exception(f"Playlist not found: {name}")


discovery_id = get_playlist_id(DISCOVERY_PLAYLIST)
history_id = get_playlist_id(HISTORY_PLAYLIST)


def get_playlist_tracks(playlist_id):
    tracks = set()

    results = sp.playlist_tracks(playlist_id)

    while results:
        for item in results["items"]:
            track = item.get("track")

            if track and track.get("id"):
                tracks.add(track["id"])

        if results["next"]:
            results = sp.next(results)
        else:
            results = None

    return tracks


def get_liked_tracks():
    tracks = set()
    offset = 0

    while True:
        results = sp.current_user_saved_tracks(
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

def normalize_track(track):
    artist = track["artists"][0]["name"].lower()
    name = track["name"].lower()
    return f"{artist} - {name}"
    
def get_lastfm_tracks():
    tracks = set()

    try:
        url = (
            f"https://ws.audioscrobbler.com/2.0/"
            f"?method=user.getrecenttracks"
            f"&user={LASTFM_USERNAME}"
            f"&api_key={LASTFM_API_KEY}"
            f"&format=json"
            f"&limit=200"
        )

        response = requests.get(url, timeout=15)

        data = response.json()

        for item in data.get("recenttracks", {}).get("track", []):
            artist = item.get("artist", {}).get("#text", "").lower()
            name = item.get("name", "").lower()

            if artist and name:
                tracks.add(f"{artist} - {name}")

    except Exception as e:
        print(f"Last.fm unavailable: {e}")

    return tracks


print("Loading playlist history...")
history_tracks = get_playlist_tracks(history_id)

print("Loading liked tracks...")
liked_tracks = get_liked_tracks()

print("Loading current discovery playlist...")
existing_discovery = get_playlist_tracks(discovery_id)

blacklist = (
    history_tracks
    | liked_tracks
    | existing_discovery
)
blacklist_keys = set()

print(f"Blacklist size: {len(blacklist)}")

print("Loading top artists...")

short_term = sp.current_user_top_artists(
    limit=15,
    time_range="short_term"
)

medium_term = sp.current_user_top_artists(
    limit=15,
    time_range="medium_term"
)

recent_tracks = sp.current_user_recently_played(limit=20)

top_artists = sp.current_user_top_artists(
    limit=25,
    time_range="medium_term"
)

print("Today's seed artists:")

for artist in top_artists:
    print("-", artist["name"])


candidate_tracks = []
blacklist_keys = set()

top_artists = sp.current_user_top_artists(
    limit=25,
    time_range="medium_term"
)

for artist in top_artists["items"]:

    artist_name = artist["name"]

    print(f"Getting similar artists for: {artist_name}")

    try:

        url = (
            f"https://ws.audioscrobbler.com/2.0/"
            f"?method=artist.getsimilar"
            f"&artist={artist_name}"
            f"&api_key={LASTFM_API_KEY}"
            f"&format=json"
            f"&limit=10"
        )

        response = requests.get(url).json()

        similar_artists = (
            response
            .get("similarartists", {})
            .get("artist", [])
        )

        for similar in similar_artists:

            similar_name = similar.get("name")

            if not similar_name:
                continue

            try:

                search = sp.search(
                    q=similar_name,
                    type="track",
                    limit=25
                )

                tracks = search.get("tracks", {}).get("items", [])
                random.shuffle(tracks)

                for track in tracks:
                    candidate_tracks.append(track)

            except Exception:
                continue

    except Exception:
        continue

random.shuffle(candidate_tracks)

unique_candidates = {}

for track in candidate_tracks:
    track_id = track.get("id")

    if track_id:
        unique_candidates[track_id] = track

candidate_tracks = list(unique_candidates.values())
for t in candidate_tracks:
    try:
        blacklist_keys.add(normalize_track(t))
    except Exception:
        continue
        
print("Loading Last.fm history...")
lastfm_tracks = get_lastfm_tracks()

new_tracks = []
duration_ms = 0

for track in candidate_tracks:
    track_id = track.get("id")

    if not track_id:
        continue

    artist = track["artists"][0]["name"].lower()
    name = track["name"].lower()

    key = normalize_track(track)

    if track_id in blacklist:
        continue

    if key in lastfm_tracks:
        continue

    if key in blacklist_keys:
        continue

    popularity = track.get("popularity", 0)

    if popularity > 75:
        continue

    new_tracks.append(track_id)

    duration_ms += track["duration_ms"]

    if duration_ms >= 2 * 60 * 60 * 1000:
        break


print(f"Selected {len(new_tracks)} new tracks")


current_tracks = list(existing_discovery)

if current_tracks:
    print("Archiving previous discovery tracks...")
    sp.playlist_add_items(
        history_id,
        current_tracks
    )


print("Cleaning Daily Discovery playlist...")

current_playlist_items = sp.playlist_items(discovery_id)

uris_to_remove = []

for item in current_playlist_items["items"]:
    track = item.get("track")

    if track and track.get("uri"):
        uris_to_remove.append(track["uri"])

if uris_to_remove:
    sp.playlist_remove_all_occurrences_of_items(
        discovery_id,
        uris_to_remove
    )


if new_tracks:
    print("Uploading new recommendations...")

    sp.playlist_add_items(
        discovery_id,
        new_tracks
    )

print("Done.")