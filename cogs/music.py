import asyncio
import time
import aiohttp
import discord
import wavelink
from discord import app_commands
from discord.ext import commands
from utils import *

from sqlalchemy import Column, Integer, String, Boolean, desc
from sqlalchemy.ext.declarative import declarative_base
from db import Base, get_db
import uuid

import os
from dotenv import load_dotenv
load_dotenv()

# DB 테이블 정의
# ========================================================================================
class GuildMusicSettings(Base):
    __tablename__ = 'guild_music_settings'

    id = Column(Integer, primary_key=True)
    guild_id = Column(Integer, nullable=False)
    channel_id = Column(Integer, nullable=False)
    message_id = Column(Integer, nullable=False)

class Queues(Base):
    __tablename__ = 'queues'

    id = Column(Integer, nullable=False)
    guild_id = Column(Integer, nullable=False)
    member_id = Column(Integer, nullable=False)
    video_id = Column(String, nullable=False)
    video_title = Column(String, nullable=False)
    video_thumbnail = Column(String, nullable=False)
    video_duration = Column(Integer, nullable=False)
    is_spotify = Column(Boolean, nullable=False, default=False)
    isrc = Column(String, nullable=True)
    uuid = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))

# 음악 재생 설정 (Lavalink / wavelink)
#========================================================================================
# 자동 disconnect 타임아웃 (초)
AUTO_DISCONNECT_TIMEOUT = int(os.getenv('AUTO_DISCONNECT_TIMEOUT', '600'))
# 재생 시도 중복 방지 락
guild_locks = {}
# 자동 disconnect 타이머 (guild_id -> asyncio.Task)
disconnect_tasks = {}
# 스포티파이 활동 캐시 member_id -> discord.Spotify (스포티파이 활동은 계정 단위라 서버 구분 불필요)
spotify_activity_cache = {}
# 직전 로드 실패 곡 (guild_id -> video_id), 같은 곡 무한 재시도 방지
load_failed_tracks = {}

# 스포티파이 연동 동기화 (presence 이벤트 기반)
# 첫 이벤트는 즉시 동기화, 이후 이 시간 동안 들어온 이벤트는 모아서 마지막 기준으로 동기화 (여러 곡 연속 넘기기 대비)
SPOTIFY_SYNC_INTERVAL = 3
# 스포티파이 활동 중지 시 일시정지 유지 시간, 초과하면 연동 종료
SPOTIFY_PAUSE_TIMEOUT = 60
# 스포티파이와 재생 위치 차이가 이 값 이상이면 seek
SPOTIFY_SEEK_THRESHOLD_MS = 2000
# 동기화 스로틀 태스크 (member_id -> asyncio.Task), 대기 구간 중 이벤트 수신한 member_id
spotify_sync_tasks = {}
spotify_sync_pending = set()
# 활동 중지 일시정지 타이머 (guild_id -> asyncio.Task)
spotify_pause_tasks = {}
# 현재 연동 재생 중인 스포티파이 track_id (guild_id -> track_id)
spotify_track_ids = {}

def get_guild_lock(guild_id):
    # guild_id가 str/int 혼용되어 int로 통일
    return guild_locks.setdefault(int(guild_id), asyncio.Lock())

def cancel_spotify_pause(guild_id):
    # 활동 중지로 일시정지한 상태였으면 True
    task = spotify_pause_tasks.pop(int(guild_id), None)
    if task and not task.done():
        task.cancel()
    return task is not None

def get_spotify_activity(member):
    # 캐시 우선, 없으면 member.activities 폴백 (봇 시작 직후 presence 이벤트 수신 전 대비)
    cached = spotify_activity_cache.get(member.id)
    if cached:
        return cached
    return next(
        (a for a in member.activities if isinstance(a, discord.Spotify)),
        None
    )

# 자동 disconnect 타이머 관리
#========================================================================================
async def _auto_disconnect(guild_id, player: wavelink.Player):
    await asyncio.sleep(AUTO_DISCONNECT_TIMEOUT)
    if player.connected and not player.playing:
        await player.disconnect()
        disconnect_tasks.pop(guild_id, None)
        logger.info(f"Music || 대기열 없음 자동 연결 해제 | Guild: {guild_id}")

def start_disconnect_timer(guild_id, player: wavelink.Player):
    cancel_disconnect_timer(guild_id)
    task = asyncio.create_task(_auto_disconnect(guild_id, player))
    disconnect_tasks[guild_id] = task

def cancel_disconnect_timer(guild_id):
    task = disconnect_tasks.pop(guild_id, None)
    if task and not task.done():
        task.cancel()

async def disconnect_and_clear(guild, player: wavelink.Player):
    # 음성 연결 해제(lavalink 플레이어 삭제) + 대기열·타이머 정리 + 패널 업데이트
    async with get_guild_lock(guild.id):
        cancel_disconnect_timer(str(guild.id))
        cancel_spotify_pause(guild.id)
        spotify_track_ids.pop(guild.id, None)
        await player.disconnect()
        with get_db() as db:
            db.query(Queues).filter(Queues.guild_id==guild.id).delete()
            db.commit()
    await update_panel_message(guild)

# 음악 재생 관련 함수 (wavelink)
#========================================================================================
async def play_track(player: wavelink.Player, track: wavelink.Playable, start_ms: int = 0):
    # YouTube 스트림은 시작 위치를 지정해 재생하면 403 → 0초로 재생 후 실제 재생이 시작되면 seek
    await player.play(track)
    if start_ms <= 0:
        return

    # 재생 후 첫 playerUpdate 대기 (wavelink는 이전 곡 위치를 초기화하지 않아 갱신 시각으로 판단)
    loop = asyncio.get_running_loop()
    begin = loop.time()
    begin_ns = time.monotonic_ns()
    while loop.time() - begin < 5:
        if player._last_update and player._last_update > begin_ns and player._last_position > 0:
            break
        await asyncio.sleep(0.25)

    # 대기 중 스킵 등으로 곡이 바뀌었으면 seek 생략
    if player.current is None or player.current.identifier != track.identifier:
        return
    await player.seek(start_ms + int((loop.time() - begin) * 1000))

# finished: 곡이 끝까지 재생되어 호출된 경우 (스킵·중지 아님)
async def play_next_music(self, player: wavelink.Player, guild_id, finished=False):
    try:
        with get_db() as db:
            first_queue_db = db.query(Queues).filter(Queues.guild_id==guild_id).order_by(Queues.id).first()
            if not first_queue_db:
                start_disconnect_timer(str(guild_id), player)
                return

            # 멤버 정보 가져오기
            guild = self.bot.get_guild(int(guild_id))
            member = guild.get_member(int(first_queue_db.member_id))

            if member is None:
                return

            # 스포티파이 연동 재생 확인
            spotify_playback = None
            if first_queue_db.is_spotify:
                spotify_activity = get_spotify_activity(member)
                has_next = db.query(Queues).filter(Queues.guild_id==guild_id).order_by(Queues.id).offset(1).first() is not None

                # 유튜브 곡이 먼저 끝났는데 스포티파이는 아직 같은 곡이면 곡 변경 이벤트까지 대기
                if finished and not has_next and spotify_activity and spotify_activity.track_id == spotify_track_ids.get(int(guild_id)):
                    return

                # 재생 완료 곡 삭제
                db.delete(first_queue_db)
                db.commit()

                # 새로운 곡 DB에 추가 (대기열 우선)
                next_queue = db.query(Queues).filter(Queues.guild_id==guild_id).order_by(Queues.id).first()
                if not next_queue:
                    # 스포티파이 현재 곡 조회 → 유튜브 검색, 실패하면(활동 없음 등) 연동 종료
                    spotify_playback = await asyncio.to_thread(get_track_info, spotify_activity) if spotify_activity else None
                    search_result = await asyncio.to_thread(playback_youtube_search, spotify_playback) if spotify_playback else None
                    if not search_result:
                        start_disconnect_timer(str(guild_id), player)
                        await update_panel_message(guild)
                        return

                    try:
                        last_queue = db.query(Queues).filter(Queues.guild_id == guild_id).order_by(desc(Queues.id)).first()
                        if last_queue:
                            new_queue_id = last_queue.id+1
                        else:
                            new_queue_id = 1
                        new_queue = Queues(
                            id = new_queue_id,
                            guild_id=guild_id,
                            member_id=first_queue_db.member_id,
                            video_id=search_result['id'],
                            video_title=search_result['title'],
                            video_thumbnail=search_result['thumbnail'],
                            video_duration=time_str_to_int(search_result['duration']),
                            is_spotify = True,
                            isrc = spotify_playback['isrc']
                        )
                        db.add(new_queue)
                        db.commit()  # 레코드 저장
                    except Exception as ex:
                        db.rollback()
                        raise ValueError("스포티파이 대기열에 음악을 추가하는 중 오류가 발생했습니다.") from ex

                    next_music = new_queue
                else:
                    next_music = next_queue
            else:
                queue_to_delete = first_queue_db
                if queue_to_delete:
                    guild_id = queue_to_delete.guild_id
                    db.delete(queue_to_delete)  # 레코드 삭제
                    db.commit()  # 삭제된 내용 저장
                    next_music = db.query(Queues).filter(Queues.guild_id==guild_id).order_by(Queues.id).first()

            if next_music:
                # 다음 곡 요청자, 봇 채널 확인
                member = guild.get_member(int(next_music.member_id))
                member_voice = member.voice if member else None

                # 보이스 채널에 없으면 스킵
                if member_voice:
                    member_voice_channel = member_voice.channel
                else:
                    await play_next_music(self, player, guild_id)
                    return

                # 보이스 채널 다르면 같은 채널로 이동
                if player.connected and player.channel != member_voice_channel:
                    await player.move_to(member_voice_channel)

                start_seconds = 0

                if next_music.is_spotify and spotify_playback is None:
                    # 다음 곡 스포티파이 활동 가져오기
                    spotify_activity = get_spotify_activity(member)
                    spotify_playback = await asyncio.to_thread(get_track_info, spotify_activity) if spotify_activity else None
                    if not spotify_playback:
                        # 다음곡 요청한 유저가 스포티파이 재생중이 아니면 생략하고 그 다음 곡 진행
                        await play_next_music(self, player, guild_id)
                        return

                # 스포티파이 재생인 경우 시작 위치 계산
                if next_music.is_spotify:
                    spotify_track_ids[int(guild_id)] = spotify_playback['track_id']
                    start_poition_result = get_spotify_start_position(spotify_playback)
                    if start_poition_result["should_skip"]:
                        # 곧 끝나는 곡이면 재생하지 않고 곡 변경 이벤트까지 대기
                        await update_panel_message(guild)
                        return

                    start_seconds = start_poition_result["start_seconds"]

                # wavelink 로 트랙 검색 및 재생
                tracks = await wavelink.Playable.search(f"https://www.youtube.com/watch?v={next_music.video_id}")
                if not tracks:
                    logger.warning(f"Music || Lavalink에서 트랙을 찾을 수 없음 | Guild: {guild_id}, Video: {next_music.video_id}")
                    await play_next_music(self, player, guild_id)
                    return

                # 활동 중지로 일시정지 중이었으면 해제 (play는 일시정지 상태 유지)
                if cancel_spotify_pause(guild_id):
                    await player.pause(False)
                await play_track(player, tracks[0], start_seconds * 1000)
                cancel_disconnect_timer(str(guild_id))

            # 임베드 업데이트
            await update_panel_message(guild)

    except Exception as ex:
        logger.error(f"Error(play_next_music): {ex}")
        with get_db() as db:
            db.rollback()

async def play_music(self, player: wavelink.Player, guild_id, yt_id, interaction=None, spotify_playback=None):
    # 재생 프로세스 중복 요청 방지
    async with get_guild_lock(guild_id):
        # 스포티파이 연동이 대기 중(활동 중지 일시정지·곡 변경 대기)일 때 곡이 추가되면 연동 종료 후 바로 재생
        with get_db() as db:
            queue_count = db.query(Queues).filter(Queues.guild_id==guild_id).count()
        if queue_count > 1 and current_spotify_member(guild_id) is not None and (int(guild_id) in spotify_pause_tasks or player.current is None):
            if cancel_spotify_pause(guild_id):
                await player.pause(False)
            await advance_queue(self, player, guild_id)
            return

        if not player.playing:
            # 스포티파이 연동 재생인 경우
            if spotify_playback:
                spotify_track_ids[int(guild_id)] = spotify_playback['track_id']
                result = get_spotify_start_position(spotify_playback)
                if result["should_skip"]:
                    # 곡 변경 이벤트 수신 시 다음 곡부터 자동 재생
                    if interaction:
                        await interaction.followup.send("해당 곡은 곧 끝나기 때문에 다음 곡부터 재생합니다.")
                    return
                start_seconds = result["start_seconds"]
            else:
                start_seconds = 0

            # wavelink 로 트랙 검색
            tracks = await wavelink.Playable.search(f"https://www.youtube.com/watch?v={yt_id}")
            if not tracks:
                logger.error(f"Music || Lavalink에서 트랙을 찾을 수 없음 | Guild: {guild_id}, Video: {yt_id}")
                return

            try:
                await play_track(player, tracks[0], start_seconds * 1000)
                cancel_disconnect_timer(str(guild_id))
            except Exception as ex:
                logger.error(f"Music || 재생 오류 | Guild: {guild_id}, Video: {yt_id}, Err: {ex}")

# 스포티파이 연동 동기화 (presence 이벤트 기반)
#========================================================================================
def current_spotify_member(guild_id):
    # 현재 재생 곡(대기열 첫 번째)이 스포티파이 연동이면 연동 대상 member_id
    with get_db() as db:
        current = db.query(Queues).filter(Queues.guild_id==guild_id).order_by(Queues.id).first()
        if current and current.is_spotify:
            return current.member_id
    return None

async def advance_queue(self, player: wavelink.Player, guild_id):
    # 다음 곡으로 진행 (길드 락을 잡은 상태에서 호출)
    if player.current:
        await player.skip(force=True)  # track_end 이벤트에서 play_next_music 진행
    else:
        await play_next_music(self, player, str(guild_id))

def get_spotify_targets(self, member_id):
    # 해당 멤버의 스포티파이로 연동 재생 중인 (guild, player) 목록
    targets = []
    for guild in self.bot.guilds:
        player = guild.voice_client
        if player and player.connected and current_spotify_member(guild.id) == member_id:
            targets.append((guild, player))
    return targets

def schedule_spotify_sync(self, member_id):
    # 대기 구간 중이면 표시만, 아니면 즉시 동기화 시작
    if member_id in spotify_sync_tasks:
        spotify_sync_pending.add(member_id)
        return
    spotify_sync_pending.discard(member_id)
    spotify_sync_tasks[member_id] = asyncio.create_task(_spotify_sync_throttle(self, member_id))

async def _spotify_sync_throttle(self, member_id):
    try:
        await sync_spotify_member(self, member_id)
        # 대기 구간 동안 이벤트가 있었으면 최신 활동 기준으로 다시 동기화
        while True:
            await asyncio.sleep(SPOTIFY_SYNC_INTERVAL)
            if member_id not in spotify_sync_pending:
                break
            spotify_sync_pending.discard(member_id)
            await sync_spotify_member(self, member_id)
    finally:
        spotify_sync_tasks.pop(member_id, None)

async def sync_spotify_member(self, member_id):
    activity = spotify_activity_cache.get(member_id)
    if not activity:
        return

    for guild, player in get_spotify_targets(self, member_id):
        async with get_guild_lock(guild.id):
            try:
                if current_spotify_member(guild.id) != member_id:
                    continue
                if cancel_spotify_pause(guild.id):
                    await player.pause(False)

                # 곡이 바뀌었거나 재생 중인 곡이 없으면(곡 변경 대기 중) 현재 곡으로 재생
                if spotify_track_ids.get(guild.id) != activity.track_id or player.current is None:
                    await advance_queue(self, player, guild.id)
                    continue

                # 같은 곡이면 재생 위치 차이만 보정
                if activity.start:
                    expected_ms = int((discord.utils.utcnow() - activity.start).total_seconds() * 1000)
                    if 0 <= expected_ms < player.current.length and abs(expected_ms - player.position) > SPOTIFY_SEEK_THRESHOLD_MS:
                        await player.seek(expected_ms)
            except Exception as ex:
                logger.error(f"Music || 스포티파이 동기화 오류 | Guild: {guild.id}, Member: {member_id}, Err: {ex!r}")

async def on_spotify_stopped(self, member_id):
    # 활동 중지: 다음 대기열 있으면 바로 진행, 없으면 일시정지 후 타이머
    spotify_sync_pending.discard(member_id)

    for guild, player in get_spotify_targets(self, member_id):
        async with get_guild_lock(guild.id):
            try:
                if current_spotify_member(guild.id) != member_id:
                    continue
                with get_db() as db:
                    has_next = db.query(Queues).filter(Queues.guild_id==guild.id).count() > 1
                if has_next:
                    await advance_queue(self, player, guild.id)
                elif guild.id not in spotify_pause_tasks:
                    await player.pause(True)
                    spotify_pause_tasks[guild.id] = asyncio.create_task(_spotify_pause_timeout(self, guild, player, member_id))
            except Exception as ex:
                logger.error(f"Music || 스포티파이 활동 중지 처리 오류 | Guild: {guild.id}, Member: {member_id}, Err: {ex!r}")

async def _spotify_pause_timeout(self, guild, player: wavelink.Player, member_id):
    await asyncio.sleep(SPOTIFY_PAUSE_TIMEOUT)
    spotify_pause_tasks.pop(guild.id, None)
    async with get_guild_lock(guild.id):
        try:
            await player.pause(False)
            if player.connected and current_spotify_member(guild.id) == member_id:
                await advance_queue(self, player, guild.id)
                logger.info(f"Music || 스포티파이 활동 중지 {SPOTIFY_PAUSE_TIMEOUT}초 초과로 연동 종료 | Guild: {guild.id}, Member: {member_id}")
        except Exception as ex:
            logger.error(f"Music || 스포티파이 연동 종료 오류 | Guild: {guild.id}, Member: {member_id}, Err: {ex!r}")

# YouTube poToken 갱신 (pot-provider 발급 → Lavalink youtube 플러그인 반영)
# 토큰 TTL 약 12시간, Lavalink 재시작 시 초기화되므로 노드 연결 시 + 주기적으로 갱신
#========================================================================================
POT_PROVIDER_URL = os.getenv('POT_PROVIDER_URL', 'http://pot-provider:4416')
POT_REFRESH_INTERVAL = 6 * 60 * 60

async def refresh_po_token():
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
            async with session.post(f"{POT_PROVIDER_URL}/get_pot", json={}) as resp:
                resp.raise_for_status()
                data = await resp.json()
            body = {"poToken": data["poToken"], "visitorData": data["contentBinding"]}
            for node in wavelink.Pool.nodes.values():
                async with session.post(f"{node.uri}/youtube", json=body, headers={"Authorization": node.password}) as resp:
                    resp.raise_for_status()
        logger.info(f"Music || poToken 갱신 완료 | Nodes: {len(wavelink.Pool.nodes)}")
    except Exception as ex:
        logger.error(f"Music || poToken 갱신 실패 | Err: {ex!r}")

async def po_token_loop():
    while True:
        await asyncio.sleep(POT_REFRESH_INTERVAL)
        await refresh_po_token()

# Lavalink 재연결 감시
# wavelink 3.4.1은 Lavalink 종료 시 받는 CLOSE 메시지를 처리하지 못해 keep_alive 태스크가 죽고 재연결을 안 함
# → 죽은 연결을 정리하고 Pool.reconnect로 재연결 (재연결 실패/재시도 소진 노드도 같이 재시도)
#========================================================================================
LAVALINK_WATCHDOG_INTERVAL = 30

async def lavalink_watchdog_loop():
    while True:
        await asyncio.sleep(LAVALINK_WATCHDOG_INTERVAL)
        try:
            for node in wavelink.Pool.nodes.values():
                ws = node._websocket
                if node.status is wavelink.NodeStatus.CONNECTED and ws and ws.keep_alive_task and ws.keep_alive_task.done():
                    logger.warning(f"Music || Lavalink 연결 끊김 감지, 재연결 시도 | Node: {node.identifier}")
                    await ws.cleanup()
            await wavelink.Pool.reconnect()
        except Exception as ex:
            logger.error(f"Music || Lavalink 재연결 실패 | Err: {ex!r}")

# 디스코드 봇 이벤트
#========================================================================================
class Music(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.pot_task = None
        self.watchdog_task = None

    @app_commands.command(
        name="전용채널", 
        description="노래봇 전용 채널을 생성합니다. 이름을 정하지 않으면 '🎵노래봇-명령어'로 생성됩니다."
    )
    @app_commands.default_permissions(administrator=True)
    async def control_pannel(self, interaction: discord.Interaction, channel_name: str = "🎵노래봇-명령어"):
        await interaction.response.defer()

        guild = interaction.guild
        search_channel = discord.utils.get(guild.text_channels, name=channel_name)
        if search_channel:
            for channel in guild.text_channels:
                if channel.permissions_for(guild.me).send_messages:
                    await channel.send(f"{channel_name} 채널이 이미 존재합니다. 채널 삭제 후 봇을 다시 초대해주세요.")
                    break
            return

        created_channel = await guild.create_text_channel(channel_name)
        embed, view = await create_panel_form(guild)
        created_message = await created_channel.send(embed=embed, view=view)

        with get_db() as db:
            try:
                guild_info = db.query(GuildMusicSettings).filter_by(guild_id=guild.id).first()
                if guild_info:
                    db.delete(guild_info)
                    db.commit()
                new_guild_setting = GuildMusicSettings(
                    guild_id=guild.id,
                    channel_id=created_channel.id,
                    message_id=created_message.id
                )
                db.add(new_guild_setting)
                db.commit()
                await interaction.followup.send("전용채널이 생성되었습니다.")
                logger.info(f"Music || 전용채널 생성 성공 | Guild: {guild.id}, Channel: {created_channel.id}")
            except Exception:
                db.rollback()
                await created_channel.delete()
                await interaction.followup.send("DB 저장 중 오류가 발생했습니다. 다시 시도해주세요.")
                logger.exception(f"Music || DB저장 중 오류 발생 | Guild: {guild.id}, Channel: {created_channel.id}")
        
    @app_commands.command(
        name="패널재생성",
        description="전용채널에 있는 패널을 재생성합니다. 오류가 발생했을 때 사용해주세요."
    )
    @app_commands.default_permissions(administrator=True)
    async def recreate_panel(self, interaction: discord.Interaction):
        await interaction.response.defer()

        guild = interaction.guild
        with get_db() as db:
            try:
                guild_info = db.query(GuildMusicSettings).filter_by(guild_id=guild.id).first()
                if not guild_info:
                    await interaction.followup.send("패널이 생성되지 않았습니다. `/전용채널` 명령어를 먼저 사용하세요.")
                    return

                channel = self.bot.get_channel(guild_info.channel_id)
                if channel is None:
                    await interaction.followup.send("기존 패널 채널을 찾을 수 없습니다.")
                    return

                try:
                    message = await channel.fetch_message(guild_info.message_id)
                    await message.delete()
                except discord.NotFound:
                    logger.warning(f"Music || 기존 메시지를 찾을 수 없음 | Guild: {guild.id}, Channel: {channel.id}")

                embed, view = await create_panel_form(guild)
                panel_message = await channel.send(embed=embed, view=view)

                guild_info.message_id = panel_message.id
                db.commit()

                await interaction.followup.send("패널이 재생성되었습니다.")
                logger.info(f"Music || 패널 재생성 성공 | Guild: {guild.id}, Channel: {channel.id}")

            except Exception:
                db.rollback()
                await interaction.followup.send("패널 재생성 중 오류가 발생했습니다. 다시 시도해주세요.")
                logger.exception(f"Music || 패널 재생성 중 오류 발생 | Guild: {guild.id}")

    @app_commands.command(
        name="스포티파이", 
        description="사용자의 스포티파이 활동을 기준으로 노래를 재생합니다."
    )
    @app_commands.describe(member="연동할 사용자 (입력하지 않으면 본인 기준)")
    async def spotify_play(self, interaction: discord.Interaction, member: discord.Member = None):
        await interaction.response.defer(ephemeral=True)

        # 보이스채널 참가 여부 확인
        member_voice = interaction.user.voice
        if not member_voice:
            await interaction.followup.send("보이스채널에 참가 후 사용해주세요.")
            return

        target = member or interaction.guild.get_member(interaction.user.id)
        if target is None:
            await interaction.followup.send("사용자의 활동을 찾을 수 없습니다. 잠시 후 다시 시도해주세요.")
            return
        if target.bot:
            await interaction.followup.send("봇의 활동은 연동할 수 없습니다.")
            return
        # 재생/동기화가 대상 사용자 기준으로 동작하므로 같은 보이스채널에 있어야 함
        if target.id != interaction.user.id and (not target.voice or target.voice.channel != member_voice.channel):
            await interaction.followup.send("연동할 사용자가 같은 보이스채널에 있어야 합니다.")
            return

        # 스포티파이 활동 찾기
        spotify_activity = get_spotify_activity(target)
        if not spotify_activity:
            if target.id == interaction.user.id:
                await interaction.followup.send("스포티파이 활동이 없어요.\n스포티파이 계정을 디스코드에 연결 후 노래를 재생한 상태에서 시도해주세요.")
            else:
                await interaction.followup.send(f"{target.display_name}님의 스포티파이 활동이 없어요.")
            return

        # track_id로 현재곡 정보 조회
        spotify_playback = await asyncio.to_thread(get_track_info, spotify_activity)
        if not spotify_playback:
            await interaction.followup.send("현재곡 정보 검색 실패.")
            return

        # playback 정보로 유튜브 노래 검색
        search_result = await asyncio.to_thread(playback_youtube_search, spotify_playback)
        if not search_result:
            await interaction.followup.send("재생중인 스포티파이 곡으로 유튜브 영상 검색에 실패했습니다.")
            return
        
        try:
            with get_db() as db:
                # 대기열 추가 또는 재생
                last_queue = db.query(Queues).filter(Queues.guild_id == interaction.guild_id).order_by(desc(Queues.id)).first()
                if last_queue:
                    last_queue_id = last_queue.id
                    await interaction.followup.send(f"스포티파이 연동 재생을 대기열에 추가했습니다.")
                else:
                    last_queue_id = 0
                    await interaction.followup.send(f"스포티파이 연동 재생: {search_result['title']}을(를) 재생합니다.")
                
                member_voice_channel = member_voice.channel
                from utils.music_player import ChannelIdPlayer
                bot_voice_client = interaction.guild.voice_client

                if bot_voice_client and bot_voice_client.connected:
                    # 봇과 다른 채널이면 요청자 채널로 이동(대기열 비었을때)
                    if bot_voice_client.channel != member_voice_channel and not last_queue:
                        await bot_voice_client.disconnect()
                        player = await member_voice_channel.connect(cls=ChannelIdPlayer)
                    else:
                        player = bot_voice_client
                else:
                    player = await member_voice_channel.connect(cls=ChannelIdPlayer)

                # 대기열 DB에 추가
                new_queue = Queues(
                    guild_id=interaction.guild_id,
                    member_id=target.id,
                    video_id=search_result['id'],
                    video_title=search_result['title'],
                    video_thumbnail=search_result['thumbnail'],
                    video_duration=time_str_to_int(search_result['duration']),
                    id=last_queue_id+1,
                    isrc = spotify_playback['isrc'],
                    is_spotify=True
                )
                db.add(new_queue)
                db.commit()

                # 패널 업데이트
                await update_panel_message(interaction.guild)

                # 노래 재생
                asyncio.create_task(play_music(self, player, interaction.guild_id, search_result['id'], interaction, spotify_playback))
                logger.info(f"Music || 🎵 {search_result['title']} 재생 시작 | Guild: {interaction.guild_id}, Music Id: {search_result['id']}, Duration: {search_result['duration']}, Requester : {interaction.user.id}, Spotify: {target.id}")

                # 다른 사용자 활동으로 연동한 경우 대상에게 DM 알림
                if target.id != interaction.user.id:
                    try:
                        await target.send(
                            f"**{interaction.guild.name}** 서버에서 {interaction.user.display_name}님이 "
                            f"회원님의 스포티파이 활동으로 연동 재생을 시작했습니다."
                        )
                    except discord.Forbidden:
                        logger.info(f"Music || 연동 재생 DM 발송 실패(DM 차단) | Guild: {interaction.guild_id}, Member: {target.id}")
        except Exception as ex:
            with get_db() as db:
                db.rollback()
            await interaction.followup.send("오류가 발생했습니다. 다시 시도해주세요.")
            logger.error(f"Music || 스포티파이 연동 재생 오류 발생 | Guild: {interaction.guild_id}, Member: {interaction.user.id} Err: {ex}")

    # 봇 시작시 패널 재생성, 대기열 데이터 삭제
    @commands.Cog.listener()
    async def on_ready(self):

        if self.pot_task is None or self.pot_task.done():
            self.pot_task = asyncio.create_task(po_token_loop())
        if self.watchdog_task is None or self.watchdog_task.done():
            self.watchdog_task = asyncio.create_task(lavalink_watchdog_loop())
        with get_db() as db:
            try:
                # 대기열 데이터 삭제
                db.query(Queues).delete()
                db.commit()
                # 패널 재생성
                guild_settings = db.query(GuildMusicSettings).all()
                for setting in guild_settings:
                    guild = self.bot.get_guild(int(setting.guild_id))
                    if not guild:
                        continue
                    channel = guild.get_channel(int(setting.channel_id))
                    if not channel:
                        continue
                    message = await channel.fetch_message(int(setting.message_id))
                    if message:
                        await message.delete()
                    embed, view = await create_panel_form(guild)
                    created_message = await channel.send(embed=embed, view=view)
                    setting.message_id = created_message.id
                    db.commit()
            except Exception:
                db.rollback()
            finally:
                logger.info("Music || 봇 시작 패널 재생성 및 대기열 데이터 삭제 완료")

    # 전용 채널 메세지 감지, 음악 재생
    @commands.Cog.listener()
    async def on_message(self, message):
        # 봇 메세지인 경우 무시
        if message.author.bot:
            return
        # 봇 명령어인 경우 무시
        prefix = await self.bot.get_prefix(message)
        if message.content.startswith(prefix[2]):
            return
        message_id = message.id
        channel_id = message.channel.id
        guild_id = message.guild.id
        member = message.author

        try:
            with get_db() as db:
                # DB에서 전용채널 설정 가져오기
                db_guild_setting = db.query(GuildMusicSettings).filter(GuildMusicSettings.guild_id == guild_id, GuildMusicSettings.channel_id == channel_id).first()
                if not db_guild_setting:
                    return

                # 사용자가 음성채널에 있는지 확인
                if not member or not member.voice:
                    msg = await message.channel.send("음성 채널에 참가해주세요.")
                    asyncio.create_task(delete_message_later(msg, 3))
                    return

                # 유튜브 주소 검색, 쿼리 검색 확인
                try:
                    if message.content.startswith("https://www.youtube.com/watch?v=") or message.content.startswith("https://youtu.be/"):
                        if "&list=" in message.content:
                            search_result = (await asyncio.to_thread(video_search_url, message.content.split('&list=', 1)[0]))[0]
                        else:
                            search_result = (await asyncio.to_thread(video_search_url, message.content))[0]
                    else:
                        search_result = (await asyncio.to_thread(video_search, message.content))[0]
                except Exception as ex:
                    msg = await message.channel.send("검색 중 오류가 발생했습니다.")
                    asyncio.create_task(delete_message_later(msg, 3))
                    logger.error(f"Music || 노래 검색 오류 발생: {ex}")
                    return

                if not search_result:
                    return

                asyncio.create_task(delete_message_later(message, 3))

                # 대기열 추가 또는 재생
                last_queue = db.query(Queues).filter(Queues.guild_id == guild_id).order_by(desc(Queues.id)).first()
                if last_queue:
                    last_queue_id = last_queue.id
                    msg = await message.channel.send(f"{search_result['title']}을(를) 대기열에 추가했습니다.")
                else:
                    last_queue_id = 0
                    msg = await message.channel.send(f"{search_result['title']}을(를) 재생합니다.")
                asyncio.create_task(delete_message_later(msg, 3))
                
                member_voice_channel = member.voice.channel
                from utils.music_player import ChannelIdPlayer
                bot_voice_client = message.guild.voice_client

                if bot_voice_client and bot_voice_client.connected:
                    # 봇과 다른 채널이면 요청자 채널로 이동(대기열 비었을때)
                    if bot_voice_client.channel != member_voice_channel and not last_queue:
                        await bot_voice_client.disconnect()
                        player = await member_voice_channel.connect(cls=ChannelIdPlayer)
                    else:
                        player = bot_voice_client
                else:
                    player = await member_voice_channel.connect(cls=ChannelIdPlayer)

                # 대기열 DB에 추가
                new_queue = Queues(
                    guild_id=guild_id,
                    member_id=member.id,
                    video_id=search_result['id'],
                    video_title=search_result['title'],
                    video_thumbnail=search_result['thumbnail'],
                    video_duration=time_str_to_int(search_result['duration']),
                    id=last_queue_id+1
                )
                db.add(new_queue)
                db.commit()

            # 패널 업데이트
            await update_panel_message(message.guild)

            # 노래 재생
            asyncio.create_task(play_music(self, player, guild_id, search_result['id']))
            logger.info(f"Music || 🎵 {search_result['title']} 재생 시작 | Guild: {guild_id}, Music Id: {search_result['id']}, Duration: {search_result['duration']}, Requester : {member.id}")

        except Exception as ex:
            with get_db() as db:
                db.rollback()
            error_msg = await message.channel.send("오류가 발생했습니다. 다시 시도해주세요.")
            asyncio.create_task(delete_message_later(error_msg, 3))
            logger.error(f"Music || 노래 재생 오류 발생 | Guild: {guild_id}, Channel: {channel_id}, Query: {message.content}, Err: {ex}")

    # 활동 변화 감지, 스포티파이 활동 캐시 갱신
    @commands.Cog.listener()
    async def on_presence_update(self, before, after):
        spotify_activity = next(
            (a for a in after.activities if isinstance(a, discord.Spotify)),
            None
        )
        cached = spotify_activity_cache.get(after.id)
        if spotify_activity:
            spotify_activity_cache[after.id] = spotify_activity
        else:
            spotify_activity_cache.pop(after.id, None)

        # 같은 이벤트가 봇과 겹치는 서버 수만큼 수신 → 캐시와 달라진 경우만 처리
        if spotify_activity is None:
            if cached is not None:
                await on_spotify_stopped(self, after.id)
            return
        if cached and cached.track_id == spotify_activity.track_id:
            if not (cached.start and spotify_activity.start):
                return
            if abs((cached.start - spotify_activity.start).total_seconds()) < 1:
                return
        schedule_spotify_sync(self, after.id)

    # 음성 채널 아무도 없으면 연결 해제
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        # 이벤트 발생한 서버의 봇 음성 연결만 확인
        player = member.guild.voice_client
        if not player or not player.connected or not player.channel:
            return
        voice_channel = player.channel
        if any(not m.bot for m in voice_channel.members):
            return
        await disconnect_and_clear(member.guild, player)
        logger.info(f"Music || 음성 채널에 아무도 없어서 연결 해제 | Guild: {member.guild.id}, Channel: {voice_channel.id}")

    # Lavalink 노드 새 세션 연결 시 처리 (Lavalink 재시작하면 토큰·플레이어 초기화됨)
    @commands.Cog.listener()
    async def on_wavelink_node_ready(self, payload: wavelink.NodeReadyEventPayload):
        if payload.resumed:
            return

        # 이전 세션의 플레이어는 새 세션에 없어 재생 불가(404) → 음성 연결 해제 후 대기열 정리
        for vc in list(self.bot.voice_clients):
            if not isinstance(vc, wavelink.Player) or vc.node is not payload.node:
                continue
            guild = vc.guild
            try:
                await disconnect_and_clear(guild, vc)
                logger.warning(f"Music || Lavalink 세션 재생성으로 음성 연결 해제 및 대기열 초기화 | Guild: {guild.id}")
            except Exception as ex:
                logger.error(f"Music || Lavalink 세션 재생성 후 정리 실패 | Guild: {guild.id}, Err: {ex!r}")

        await refresh_po_token()

    # wavelink 트랙 종료 이벤트 (자동 다음 곡 재생)
    @commands.Cog.listener()
    async def on_wavelink_track_end(self, payload: wavelink.TrackEndEventPayload):
        player = payload.player
        if player is None:
            return
        guild_id = str(player.guild.id)
        reason_str = str(payload.reason).lower()
        reason_name = payload.reason.name.lower() if hasattr(payload.reason, "name") else reason_str
        reason_value = str(payload.reason.value).lower() if hasattr(payload.reason, "value") else reason_str

        is_valid_reason = False
        for r in (reason_name, reason_value, reason_str):
            if r in ("finished", "stopped", "load_failed", "loadfailed"):
                is_valid_reason = True
                break

        if not is_valid_reason:
            return

        # 같은 곡 연속 로드 실패 시 재시도 중단
        # (스포티파이 연동 곡은 실패해도 같은 곡을 다시 불러와 무한 재시도됨)
        if reason_name in ("load_failed", "loadfailed") or reason_value in ("load_failed", "loadfailed"):
            track_id = payload.track.identifier if payload.track else None
            if load_failed_tracks.get(guild_id) == track_id:
                load_failed_tracks.pop(guild_id, None)
                with get_db() as db:
                    first_queue = db.query(Queues).filter(Queues.guild_id==guild_id).order_by(Queues.id).first()
                    if first_queue and first_queue.video_id == track_id:
                        db.delete(first_queue)
                        db.commit()
                logger.warning(f"Music || 같은 곡 연속 재생 실패로 재시도 중단 | Guild: {guild_id}, Video: {track_id}")
                start_disconnect_timer(guild_id, player)
                await update_panel_message(player.guild)
                return
            load_failed_tracks[guild_id] = track_id
        else:
            load_failed_tracks.pop(guild_id, None)

        async with get_guild_lock(guild_id):
            await play_next_music(self, player, guild_id, finished="finished" in (reason_name, reason_value, reason_str))

#===============================================================================
panel_message_list = {
    'resume' : "▶ 재생",
    'pause' : "∥ 중지",
    'skip' : "▶| 스킵"
}

async def create_panel_form(guild,play_queue = []):
    view = discord.ui.View(timeout=None)
    # 버튼 생성
    play_btn = discord.ui.Button(label=panel_message_list['resume'], style=discord.ButtonStyle.secondary)
    pause_btn = discord.ui.Button(label=panel_message_list['pause'], style=discord.ButtonStyle.secondary)
    skip_btn = discord.ui.Button(label=panel_message_list['skip'], style=discord.ButtonStyle.secondary)
    if play_queue:
        if len(play_queue) > 1:
            options = []
            for idx, music in enumerate(play_queue[1:], start=1):
                title = music['title'] if not music['is_spotify'] else "스포티파이 연동 재생"
                options.append(discord.SelectOption(label=title, description=f"요청자: {music['author_name']}, 영상 길이: {music['duration']}", value=str(idx)))
            placeholder = f"다음 노래가 {len(play_queue)-1}개 있어요"
        else: 
            options = [discord.SelectOption(label="없어요."),]
            placeholder = "다음 노래가 없어요."
        embed = playing_embed_form(play_queue[0])
    else:
        embed = discord.Embed (title="재생중인 곡이 없어요.")
        options = [discord.SelectOption(label="없어요."),]
        placeholder = "다음 노래가 없어요."
    queue_dropdown = discord.ui.Select(placeholder=placeholder, options=options, min_values=1, max_values=1)

    # 재생 버튼
    async def play_btn_callback(interaction):
        player = guild.voice_client
        if not player:
            await interaction.response.send_message("음성 채널에 접속해 주세요.", ephemeral=True)
            return
        await interaction.response.edit_message(content="곡을 재생합니다.", view=view)
        await player.pause(False)
        logger.info(f"Music || 재생 버튼 입력 | Guild: {guild.id}, User: {interaction.user.id}")

    # 중지 버튼
    async def pause_btn_callback(interaction):
        player = guild.voice_client
        if player:
            await interaction.response.edit_message(content="곡이 중지되었습니다.", view=view)
            await player.pause(True)
            logger.info(f"Music || 중지 버튼 입력 | Guild: {guild.id}, User: {interaction.user.id}")

    # 스킵 버튼
    async def skip_btn_callback(interaction):
        player = guild.voice_client
        if player:
            await interaction.response.edit_message(content="곡이 스킵되었습니다.", view=view)
            await player.skip(force=True)
            logger.info(f"Music || 스킵 버튼 입력 | Guild: {guild.id}, User: {interaction.user.id}")

    #대기열 목록
    async def queue_dropdown_callback(interaction: discord.Interaction):
        player = guild.voice_client
        if len(play_queue) > 1 and player:
            # selected_option = int(queue_dropdown.values[0])
            # selected_music = play_queue.pop(selected_option)
            # play_queue.insert(1,selected_music)
            # voice_client.stop()
            # await interaction.response.send_message(f"{play_queue[1]['title']}을 재생합니다.",ephemeral=True)
            await interaction.response.send_message(f"아무 기능이 없어요. ",ephemeral=True)
        else:
            await interaction.response.send_message("아니 없어요",ephemeral=True)
    
    play_btn.callback = play_btn_callback  # 재생 버튼
    pause_btn.callback = pause_btn_callback  # 중지 버튼
    skip_btn.callback = skip_btn_callback  # 스킵 버튼
    queue_dropdown.callback = queue_dropdown_callback

    # 버튼을 포함한 뷰 생성
    view.add_item(queue_dropdown)
    view.add_item(play_btn)
    view.add_item(pause_btn)
    view.add_item(skip_btn)

    return embed,view

# 임베드 양식
def playing_embed_form(data):
    embed = discord.Embed(
        title = data['title'],
        url = f"https://www.youtube.com/watch?v={data['id']}",
        description="",
        color=discord.Color.default()
    )
    embed.set_image(url=data['thumbnail'])
    embed.set_author(name=f"{data['author_name']}", icon_url=data['author_avatar'])
    if data['is_spotify']:
        embed.set_footer(text="스포티파이 연동 재생", icon_url='https://storage.googleapis.com/pr-newsroom-wp/1/2023/05/Spotify_Primary_Logo_RGB_Green.png')
    embed.add_field(name="영상 길이", value=data['duration'], inline=True)

    return embed

# 노래 패널 업데이트
async def update_panel_message(guild):
    try:
        with get_db() as db:
            db_guild_music_settings = db.query(GuildMusicSettings).filter(GuildMusicSettings.guild_id == guild.id).first()
            panel_channel = guild.get_channel(db_guild_music_settings.channel_id)
            panel_message = await panel_channel.fetch_message(db_guild_music_settings.message_id)
            play_queue = db.query(Queues).filter(Queues.guild_id == guild.id).order_by(Queues.id).limit(21).all()
            queue_data = []
            for q in play_queue:

                member = guild.get_member(q.member_id)
                name_to_display = member.display_name if member.display_name else member.global_name
                member_avatar = str(member.display_avatar)
                if "?size" in member_avatar:
                    member_avatar = member_avatar.split("?")[0] + "?size=128"

                queue_data.append({
                    "title": q.video_title,
                    "duration": time_int_to_str(q.video_duration),
                    "author_name": name_to_display,
                    "author_avatar": member_avatar,
                    "thumbnail": q.video_thumbnail,
                    "id": q.video_id,
                    "is_spotify": q.is_spotify
                })
            embed, view = await create_panel_form(guild, queue_data)
            await panel_message.edit(embed=embed, view=view)
    except Exception as ex:
        print(f"Error(update_panel_message): {ex}")
        with get_db() as db:
            db.rollback()
#========================================================================================
async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Music(bot))