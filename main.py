import os
import random
import time
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

# ================= HELPERS =================

def spotify_call(func, *args, **kwargs):
    while True:
        try:
            return func(*args, **kwargs)
        except SpotifyException as e:
            if e.http_status == 429:
                retry_after = int(e.headers.get("Retry-After", 30))
                print(f"⚠️ Rate limit. Sleep {retry_after}s")
                time.sleep(retry_after + 2)
                continue
            raise
        except Exception as e:
            print(f"⚠️ Spotify error: {e}")
            time.sleep(5)

def chunked(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]

def normalize_track(track):
    artist = track["artists"][0]["name"].strip().lower()
    title = track["name"].strip().lower()
    return f"{artist} - {title}"

def get_playlist_tracks(playlist_id):
    tracks = []
    offset = 0
    while True:
        results = spotify_call(
            sp.playlist_items,
            playlist_id,
            offset=offset,
            limit=100,
            fields="items(track(id,uri,name,artists,duration_ms,popularity)),next"
        )
        items = results.get("items", [])
        if not items:
            break
        for item in items:
            track = item.get("track")
            if track and track.get("id"):
                tracks.append(track)
        offset += 100
    return tracks

def get_playlist_track_ids(playlist_id):
    return {track["id"] for track in get_playlist_tracks(playlist_id) if track.get("id")}

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
        offset += 50
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
            f"&user={LASTFM_USERNAME}"
            f"&api_key={LASTFM_API_KEY}"
            "&format=json"
            "&limit=200"
        )
        response = requests.get(url, timeout=20)
        data = response.json()
        items = data.get("recenttracks", {}).get("track", [])
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
    short = spotify_call(sp.current_user_top_artists, limit=20, time_range="short_term")
    for artist in short.get("items", []):
        artist_map[artist["id"]] = artist
    medium = spotify_call(sp.current_user_top_artists, limit=20, time_range="medium_term")
    for artist in medium.get("items", []):
        artist_map[artist["id"]] = artist
    recent = spotify_call(sp.current_user_recently_played, limit=30)
    for item in recent.get("items", []):
        track = item.get("track")
        if track:
            for artist in track.get("artists", []):
                if artist.get("id"):
                    artist_map[artist["id"]] = artist
    artists = list(artist_map.values())
    random.shuffle(artists)
    return artists[:20]

def fetch_candidates(seed_artists):
    candidates = []
    for artist in seed_artists:
        artist_name = artist.get("name")
        if not artist_name:
            continue
        print(f"Finding similar artists for: {artist_name}")
        try:
            url = (
                "https://ws.audioscrobbler.com/2.0/"
                "?method=artist.getsimilar"
                f"&artist={artist_name}"
                f"&api_key={LASTFM_API_KEY}"
                "&format=json"
                "&limit=12"
            )
            response = requests.get(url, timeout=20).json()
            similar_artists = response.get("similarartists", {}).get("artist", [])
            for sim in similar_artists:
                sim_name = sim.get("name")
                if not sim_name:
                    continue
                try:
                    search = spotify_call(
                        sp.search,
                        q=f"artist:{sim_name}",
                        type="track",
                        limit=5,
                    )
                    tracks = search.get("tracks", {}).get("items", [])
                    candidates.extend(tracks)
                    time.sleep(0.2)
                except Exception as e:
                    print(f"Search failed for {sim_name}: {e}")
        except Exception as e:
            print(f"Similar artists failed for {artist_name}: {e}")
    unique = {}
    for track in candidates:
        track_id = track.get("id")
        if track_id:
            unique[track_id] = track
    return list(unique.values())

def add_tracks_to_playlist(playlist_id, track_uris):
    if not track_uris:
        return
    print(f"➕ Adding {len(track_uris)} tracks to playlist {playlist_id}")
    for batch in chunked(track_uris, 100):
        spotify_call(sp.playlist_add_items, playlist_id, batch)
    print(f"✅ Added {len(track_uris)} tracks")

def clear_playlist(playlist_id):
    current = get_playlist_tracks(playlist_id)
    if not current:
        print(f"Playlist {playlist_id} already empty")
        return
    print(f"🧹 Clearing playlist {playlist_id}: {len(current)} tracks")
    spotify_call(sp.playlist_replace_items, playlist_id, [])
    time.sleep(2)
    after_check = get_playlist_tracks(playlist_id)
    if after_check:
        print(f"⚠️ Replace didn't fully clear, removing individually")
        uris = [t["uri"] for t in after_check if t.get("uri")]
        for batch in chunked(uris, 100):
            spotify_call(sp.playlist_remove_all_occurrences_of_items, playlist_id, batch)
    print(f"✅ Playlist {playlist_id} cleared")

def verify_tracks_added(playlist_id, expected_uris):
    """Проверяет, что все треки из списка присутствуют в плейлисте."""
    current_ids = get_playlist_track_ids(playlist_id)
    expected_ids = {uri.split(":")[-1] for uri in expected_uris}
    return expected_ids.issubset(current_ids)

# ================= MAIN =================

def main():
    print("\n=== DAILY DISCOVERY ROTATION ===\n")

    # STEP 1 – получить текущие треки discovery
    print("📖 Reading current discovery playlist...")
    existing_discovery_tracks = get_playlist_tracks(DISCOVERY_PLAYLIST_ID)
    existing_discovery_uris = [t["uri"] for t in existing_discovery_tracks if t.get("uri")]
    print(f"Found {len(existing_discovery_uris)} tracks in discovery")

    # STEP 2 – архивировать их в history (только если есть треки)
    if existing_discovery_uris:
        print("📦 Archiving to history playlist...")
        add_tracks_to_playlist(HISTORY_PLAYLIST_ID, existing_discovery_uris)

        # Проверим, что треки действительно добавились
        time.sleep(2)
        if not verify_tracks_added(HISTORY_PLAYLIST_ID, existing_discovery_uris):
            print("❌ CRITICAL: Failed to archive tracks to history. Aborting to avoid data loss.")
            return  # Не очищаем discovery и не добавляем новые треки

        print("✅ Archive verified")
    else:
        print("Discovery is empty, nothing to archive")

    # STEP 3 – очистить discovery (только после успешной архивации)
    print("🗑️ Clearing discovery playlist...")
    clear_playlist(DISCOVERY_PLAYLIST_ID)

    # STEP 4 – собрать чёрный список (история + лайки)
    print("🔨 Building blacklist...")
    history_track_ids = get_playlist_track_ids(HISTORY_PLAYLIST_ID)
    liked_tracks = get_liked_tracks()
    blacklist = history_track_ids | liked_tracks
    print(f"Blacklist size: {len(blacklist)} (history: {len(history_track_ids)}, likes: {len(liked_tracks)})")

    # STEP 5 – найти новые треки
    print("🎯 Fetching seed artists...")
    seed_artists = fetch_seed_artists()
    print(f"Seed artists: {len(seed_artists)}")
    print("🔍 Fetching candidate tracks from Last.fm similars...")
    candidate_tracks = fetch_candidates(seed_artists)
    print(f"Candidate tracks before filtering: {len(candidate_tracks)}")
    random.shuffle(candidate_tracks)

    print("🎧 Loading Last.fm recent history...")
    lastfm_tracks = get_lastfm_tracks()
    print(f"Last.fm track signatures: {len(lastfm_tracks)}")

    new_track_uris = []
    total_duration_ms = 0
    MAX_DURATION_MS = 2 * 60 * 60 * 1000  # 2 часа

    for track in candidate_tracks:
        track_id = track.get("id")
        if not track_id:
            continue
        if track_id in blacklist:
            continue
        if normalize_track(track) in lastfm_tracks:
            continue
        popularity = track.get("popularity", 0)
        if popularity > 75:
            continue
        new_track_uris.append(track["uri"])
        total_duration_ms += track.get("duration_ms", 0)
        if total_duration_ms >= MAX_DURATION_MS:
            break

    print(f"✨ Selected {len(new_track_uris)} new tracks (duration ~{total_duration_ms/60000:.1f} min)")

    # STEP 6 – добавить новые треки в discovery
    if new_track_uris:
        print("🚀 Uploading fresh tracks to discovery playlist...")
        add_tracks_to_playlist(DISCOVERY_PLAYLIST_ID, new_track_uris)
    else:
        print("⚠️ No new tracks found – discovery remains empty")

    print("\n✅ Done.\n")

if __name__ == "__main__":
    main()
