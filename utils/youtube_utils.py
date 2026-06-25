from yt_dlp import YoutubeDL
from .convert_utils import *

# yt-dlp 공통 옵션
_YDL_SEARCH_OPTS = {'quiet': True, 'no_warnings': True, 'extract_flat': True, 'default_search': 'ytsearch'}
_YDL_INFO_OPTS = {'quiet': True, 'no_warnings': True}


def _entry_to_video_data(entry):
    """yt-dlp entry dict -> video_data dict (기존 youtube-search-python 호환 형식)"""
    duration_sec = entry.get('duration') or 0
    try:
        duration_sec = int(duration_sec)
    except (ValueError, TypeError):
        duration_sec = 0

    thumbnails = entry.get('thumbnails') or []
    thumbnail = thumbnails[-1]['url'] if thumbnails else ''

    return {
        'title': entry.get('title', ''),
        'id': entry.get('id', ''),
        'duration': time_int_to_str(duration_sec),
        'thumbnail': thumbnail,
        'viewcount': view_int_to_str(int(entry.get('view_count') or 0)),
        'publishedtime': '',
        'description': entry.get('description') or '',
        'channel_name': entry.get('channel') or entry.get('uploader') or '',
        'channel_profile': '',
    }


def video_search(query, search_count=1):
    """쿼리로 유튜브 영상 검색 (yt-dlp 기반)"""
    try:
        with YoutubeDL(_YDL_SEARCH_OPTS) as ydl:
            info = ydl.extract_info(f'ytsearch{search_count}:{query}', download=False)
    except Exception:
        return None

    if not info or not info.get('entries'):
        return None

    video_data = []
    for entry in info['entries']:
        if not entry:
            continue
        video_data.append(_entry_to_video_data(entry))

    return video_data if video_data else None


def video_search_url(url):
    """유튜브 URL로 영상 정보 조회 (yt-dlp 기반)"""
    if '&' in url:
        url = url.split('&')[0]
    if 'youtu.be' in url:
        url = url.split('?')[0]
    try:
        with YoutubeDL(_YDL_INFO_OPTS) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception:
        return None

    if not info:
        return None

    return [_entry_to_video_data(info)]


def playback_youtube_search(playback):
    isrc_results = video_search(playback['isrc'])
    if isrc_results:
        isrc_first = isrc_results[0]
        if is_same_song_by_duration(isrc_first['duration'], playback['duration_ms']):
            return isrc_first

    title_query = f"{playback['name']} {playback.get('artist', '')}"
    title_results = video_search(title_query)
    if title_results:
        for video in title_results:
            if is_same_song_by_duration(video['duration'], playback['duration_ms']):
                return video

        return title_results[0]

    return None

def is_same_song_by_duration(yt_duration_str, spotify_duration_ms, threshold_sec=3):
    try:
        minutes, seconds = map(int, yt_duration_str.split(':'))
        yt_seconds = minutes * 60 + seconds
        spotify_seconds = spotify_duration_ms // 1000
        return abs(yt_seconds - spotify_seconds) <= threshold_sec
    except:
        return False
