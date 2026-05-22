import os
import base64
import random
import requests
import spotipy

CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")
REFRESH_TOKEN = os.getenv("SPOTIFY_REFRESH_TOKEN")


def get_access_token():
    url = "https://accounts.spotify.com/api/token"

    auth = base64.b64encode(
        f"{CLIENT_ID}:{CLIENT_SECRET}".encode()
    ).decode()

    headers = {
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/x-www-form-urlencoded"
    }

    data = {
        "grant_type": "refresh_token",
        "refresh_token": REFRESH_TOKEN
    }

    r = requests.post(url, headers=headers, data=data)
    r.raise_for_status()

    return r.json()["access_token"]

access_token = get_access_token()
sp = spotipy.Spotify(auth=access_token)

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



sp = spotipy.Spotify(auth_manager=auth_manager)


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

        response = requests.get(url, timeout=15).json()

        for item in response["recenttracks"]["track"]:
            artist = item["artist"]["#text"].lower()
            name = item["name"].lower()

            tracks.add(f"{artist} - {name}")

    except Exception as e:
        print(f"Last.fm unavailable: {e}")

    return tracks

    response = requests.get(url).json()

    recent_tracks = response.get("recenttracks", {}).get("track", [])

    for item in recent_tracks:
        artist = item.get("artist", {}).get("#text", "").lower()
        name = item.get("name", "").lower()

        if artist and name:
            tracks.add(f"{artist} - {name}")

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

artist_map = {}

for artist in short_term["items"]:
    artist_map[artist["id"]] = artist

for artist in medium_term["items"]:
    artist_map[artist["id"]] = artist

for item in recent_tracks["items"]:
    track = item.get("track")

    if not track:
        continue

    for artist in track["artists"]:
        artist_map[artist["id"]] = artist

top_artists = list(artist_map.values())

random.shuffle(top_artists)

top_artists = top_artists[:20]

print("Today's seed artists:")

for artist in top_artists:
    print("-", artist["name"])


candidate_tracks = []

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
                    limit=10
                )

                tracks = (
                    search
                    .get("tracks", {})
                    .get("items", [])
                )

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

    key = f"{artist} - {name}"

    if track_id in blacklist:
        continue

    if key in lastfm_tracks:
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