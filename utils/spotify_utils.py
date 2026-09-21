from spotipy import Spotify
from spotipy.oauth2 import SpotifyClientCredentials
import time
from datetime import datetime

import os
from dotenv import load_dotenv
load_dotenv()

SPOTIFY_CLIENT_ID = os.getenv('SPOTIFY_CLIENT_ID')
SPOTIFY_SECRET_KEY = os.getenv('SPOTIFY_SECRET_KEY')

sp = Spotify(auth_manager=SpotifyClientCredentials(client_id=SPOTIFY_CLIENT_ID, client_secret=SPOTIFY_SECRET_KEY))

# 트랙 아이디로 isrc값 가져오기
def get_track_info(activity):

    # progress_ms값 구하기
    now = datetime.now(activity.start.tzinfo)
    elapsed = now - activity.start
    elapsed_ms = int(elapsed.total_seconds() * 1000)

    track_info = sp.track(activity.track_id)
    result = {
        "track_id": activity.track_id,
        "isrc": track_info["external_ids"].get("isrc"),
        "name": track_info.get("name"),
        "artist": track_info["artists"][0]["name"] if track_info.get("artists") else None,
        "duration_ms": track_info.get("duration_ms"),
        "progress_ms": elapsed_ms
    }
    return result

# 스포티파이 검색 함수
def spotify_search(query):
    auth_manager = SpotifyClientCredentials(client_id=SPOTIFY_CLIENT_ID, client_secret=SPOTIFY_SECRET_KEY)
    sp = Spotify(auth_manager=auth_manager)

    result = sp.search(q=query, type='track', market='KR', limit=5)
    items = result['tracks']['items']
    search_result = [{
        "title": item['album']['name'],
        "isrc": item['external_ids']['isrc'],
        "duration": item['duration_ms'],
        "thumbnail": item['album']['images'][0]['url'], # 해상도 기준 필요 지금은 고해상도 이미지 가져옴
        "artists": [artist['name'] for artist in item['album']['artists']]
    } for item in items]
    return search_result

def get_spotify_start_position(spotify_playback: dict, skip_threshold_sec: int = 10) -> dict:
    progress_ms = spotify_playback.get('progress_ms') or 0
    duration_ms = spotify_playback.get('duration_ms')
    # timestamp_ms = spotify_playback.get('timestamp')

    if duration_ms and (duration_ms - progress_ms < skip_threshold_sec * 1000):
        return { "should_skip": True }

    now_ms = int(time.time() * 1000)
    # delay_ms = max(0, now_ms - timestamp_ms)

    start_seconds = int((progress_ms) / 1000) + 2 # 2초 딜레이 보정

    return {
        "should_skip": False,
        "start_seconds": start_seconds
    }