import spotipy
from spotipy.oauth2 import SpotifyOAuth

sp_oauth = SpotifyOAuth(
    client_id="YOUR_CLIENT_ID",
    client_secret="YOUR_CLIENT_SECRET",
    redirect_uri="http://127.0.0.1:8888/callback",
    scope="playlist-modify-private playlist-modify-public user-top-read user-read-recently-played user-library-read"
)

print(sp_oauth.get_authorize_url())

response = input("Paste full redirected URL here: ")

code = sp_oauth.parse_response_code(response)
token_info = sp_oauth.get_access_token(code)

print("REFRESH TOKEN:")
print(token_info["refresh_token"])