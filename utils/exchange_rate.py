"""환율 데이터 공통 모듈 (open.er-api.com, USD 기준).

Steam 가격 비교와 /환율 명령어가 함께 사용한다.
API 값은 하루 1회만 바뀌므로, 값이 바뀔 때 직전 값을 prev 파일에 보관해 '24시간 전 대비' 변동률에 쓴다.
"""
import asyncio
import json
import os
from datetime import datetime, timezone

import aiohttp

from .logger import logger

API_URL = "https://open.er-api.com/v6/latest/USD"

_BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
_DB_DIR = os.path.join(_BASE_DIR, 'db')
_FILE = os.path.join(_DB_DIR, 'exchange.json')
_PREV_FILE = os.path.join(_DB_DIR, 'exchange_prev.json')

REFRESH_INTERVAL = 4 * 60 * 60  # 갱신 주기(초)
STALE_SECONDS = 3600            # 시작 시 이 시간보다 오래된 데이터면 즉시 갱신

os.makedirs(_DB_DIR, exist_ok=True)


def _load(path):
    if os.path.isfile(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"환율 || {os.path.basename(path)} 로드 실패: {e}")
    return None


def _save(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


_data = _load(_FILE)
_prev = _load(_PREV_FILE)
_lock = asyncio.Lock()
_task = None


def get_data():
    return _data


def get_rates():
    """USD 기준 환율 dict (데이터 없으면 빈 dict)"""
    return _data.get('rates', {}) if _data else {}


def rate(base, target):
    """1 base = ? target (통화 코드가 없으면 None)"""
    rates = get_rates()
    if base not in rates or target not in rates or rates[base] == 0:
        return None
    return rates[target] / rates[base]


def change_percent(base, target):
    """직전 갱신값 대비 base/target 환율 변동률(%). 비교할 전날 값이 없으면 None"""
    prev_rates = _prev.get('rates', {}) if _prev else {}
    now = rate(base, target)
    if now is None or base not in prev_rates or target not in prev_rates or prev_rates[base] == 0 or prev_rates[target] == 0:
        return None
    return (now / (prev_rates[target] / prev_rates[base]) - 1) * 100


async def fetch(session: aiohttp.ClientSession):
    try:
        async with session.get(API_URL, timeout=aiohttp.ClientTimeout(total=15)) as res:
            if res.status != 200:
                logger.error(f'환율 || API 요청 실패: HTTP {res.status}')
                return None
            data = await res.json()
            if data.get("result") != "success":
                logger.error('환율 || API 응답 오류')
                return None
            return data
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        logger.error(f'환율 || API 요청 예외: {e}')
        return None


async def update(session: aiohttp.ClientSession):
    global _data, _prev
    data = await fetch(session)
    if not data:
        return
    async with _lock:
        # API 갱신 시각이 바뀐 경우에만 직전 값을 prev로 넘김 (같은 값을 다시 받으면 유지)
        if _data and _data.get('time_last_update_unix') != data.get('time_last_update_unix'):
            _prev = _data
            _save(_PREV_FILE, _prev)
        _data = data
        _save(_FILE, data)
    logger.info(f"환율 || 갱신 완료 ({data.get('time_last_update_utc')})")


def is_stale():
    if not _data or not _data.get('time_last_update_unix'):
        return True
    return (int(datetime.now(timezone.utc).timestamp()) - int(_data['time_last_update_unix'])) > STALE_SECONDS


async def _updater():
    async with aiohttp.ClientSession() as session:
        while True:
            await asyncio.sleep(REFRESH_INTERVAL)
            await update(session)


async def start():
    """초기 갱신 후 주기 갱신 태스크 시작 (여러 cog에서 호출해도 한 번만 실행)"""
    global _task
    if _task is not None:
        return
    _task = asyncio.create_task(_updater())
    if is_stale():
        async with aiohttp.ClientSession() as session:
            await update(session)
