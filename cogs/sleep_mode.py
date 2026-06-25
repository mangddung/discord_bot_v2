# Note: 'check_holiday' uses 'is_holiday' for Korean holidays only — replace or remove for other regions.

import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, time, timedelta
from db import Base, get_db
from sqlalchemy import Column, String, Integer
import asyncio
import pytz
from holidayskr import is_holiday
from utils import *
import os
import json
import sys
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# config 파일 불러오기
# ========================================================================================
config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "config.json")

if not os.path.isfile(config_path):
    sys.exit("'config.json' not found in project root! Please add it and try again.")
else:
    with open(config_path, encoding="utf-8") as file:
        config = json.load(file)

try:
    tz = ZoneInfo(config['timezone'])
    NOTICE_INTERVALS = config['sleep_mode']['notice_intervals']
except ZoneInfoNotFoundError:
    logger.error("Manage || Invalid timezone in config.json. Please input a valid IANA timezone (e.g. 'Asia/Seoul').")
    sys.exit(1)

# DB 테이블 정의
# ========================================================================================
class SleepMode(Base):
    __tablename__ = 'sleep_mode'
    user_id = Column(String, primary_key=True)
    username = Column(String)
    start_time = Column(String)
    end_time = Column(String)
    weekdays = Column(Integer)
    weekends = Column(Integer)
    enabled = Column(Integer, default=0)

# ========================================================================================

# 유효한 요일 입력값
VALID_WEEKDAYS = ["평일", "휴일", "매일"]

# 취침모드 설정 모달 폼
class SleepModeModal(discord.ui.Modal, title="취침모드 설정"):
    def __init__(self, weekdays_default: str = None, start_time_default: str = None, end_time_default: str = None):
        super().__init__()
        self.add_item(discord.ui.TextInput(
            label="요일 (평일, 휴일, 매일)",
            placeholder="평일, 휴일, 매일 중 하나 입력",
            default=weekdays_default
        ))
        self.add_item(discord.ui.TextInput(
            label="시작 시간 (HH:MM)",
            placeholder="예: 23:00",
            default=start_time_default
        ))
        self.add_item(discord.ui.TextInput(
            label="종료 시간 (HH:MM)",
            placeholder="예: 06:00",
            default=end_time_default
        ))

    @property
    def weekdays_input(self):
        return self.children[0]

    @property
    def start_time_input(self):
        return self.children[1]

    @property
    def end_time_input(self):
        return self.children[2]

    # 제출시 DB처리
    async def on_submit(self, interaction: discord.Interaction):
        weekdays = self.weekdays_input.value.strip()
        start_time = self.start_time_input.value.strip()
        end_time = self.end_time_input.value.strip()

        member_name = interaction.user.nick if interaction.user.nick else interaction.user.name

        # 요일 입력값 검증 (S4)
        if weekdays not in VALID_WEEKDAYS:
            await interaction.response.send_message("❗ 요일 입력이 잘못되었습니다. '평일', '휴일', '매일' 중 하나를 입력해주세요.", ephemeral=True)
            return

        try:
            datetime.strptime(start_time, "%H:%M")
            datetime.strptime(end_time, "%H:%M")
        except ValueError:
            await interaction.response.send_message("시간 형식이 잘못되었습니다. HH:MM 형식으로 입력해주세요.", ephemeral=True)
            return

        with get_db() as db:
            db.query(SleepMode).filter(SleepMode.user_id == str(interaction.user.id)).delete()
            db.add(SleepMode(
                user_id=str(interaction.user.id),
                username=interaction.user.name,
                start_time=start_time,
                end_time=end_time,
                weekdays=1 if weekdays in ["평일", "매일"] else 0,
                weekends=1 if weekdays in ["휴일", "매일"] else 0,
                enabled=1
            ))
            db.commit()

        logger.info(f"SleepMode || {member_name}({interaction.user.id})님 취침모드 설정 | {weekdays}, {start_time}~{end_time}")
        await interaction.response.send_message(f"{weekdays}, {start_time}~{end_time}으로 설정되었습니다.", ephemeral=True)

class SleepModeCommand(app_commands.Group):
    def __init__(self, bot: commands.Bot):
        super().__init__(name="취침모드", description="취침 모드 관련 명령어")
        self.bot = bot

    @app_commands.command(name="설정", description="취침 모드를 설정합니다.")
    async def set_sleep_mode(self, interaction: discord.Interaction):
        # 기존 설정 조회하여 모달에 기본값으로 채우기
        weekdays_default = None
        start_time_default = None
        end_time_default = None

        with get_db() as db:
            result = db.query(SleepMode).filter(SleepMode.user_id == str(interaction.user.id)).first()
            if result:
                # 요일 설정을 문자열로 변환
                if result.weekdays and result.weekends:
                    weekdays_default = "매일"
                elif result.weekdays:
                    weekdays_default = "평일"
                elif result.weekends:
                    weekdays_default = "휴일"
                start_time_default = result.start_time
                end_time_default = result.end_time

        await interaction.response.send_modal(SleepModeModal(
            weekdays_default=weekdays_default,
            start_time_default=start_time_default,
            end_time_default=end_time_default
        ))

    # DB 조회 및 메세지 전송
    @app_commands.command(name="켜기", description="취침 모드를 활성화합니다.")
    async def activate_sleep_mode(self, interaction: discord.Interaction):
        with get_db() as db:
            result = db.query(SleepMode).filter(SleepMode.user_id == str(interaction.user.id)).first()

            if not result:
                await interaction.response.send_message("❗ 취침 모드가 설정되지 않았습니다. `/취침모드 설정` 명령어를 사용해주세요.", ephemeral=True)
                return

            message = (f"{interaction.user.mention}, 현재 설정된 취침 모드 정보:\n"
                       f"시작 시간: {result.start_time}\n"
                       f"종료 시간: {result.end_time}\n"
                       f"주중 설정: {'활성화' if result.weekdays else '비활성화'}\n"
                       f"휴일 설정: {'활성화' if result.weekends else '비활성화'}")

            if not result.enabled:
                result.enabled = 1
                db.commit()
                message += "\n✅ 취침 모드가 활성화되었습니다."

        member_name = interaction.user.nick if interaction.user.nick else interaction.user.name
        logger.info(f"SleepMode || {member_name}({interaction.user.id})님 취침모드 활성화")
        await interaction.response.send_message(message, ephemeral=True)

    @app_commands.command(name="끄기", description="취침 모드를 비활성화합니다.")
    async def deactivate_sleep_mode(self, interaction: discord.Interaction):
        with get_db() as db:
            result = db.query(SleepMode).filter(SleepMode.user_id == str(interaction.user.id)).first()

            if not result:
                await interaction.response.send_message("❗ 취침모드 설정이 없습니다. `/취침모드 설정`으로 설정해주세요.", ephemeral=True)
                return

            if not result.enabled:
                await interaction.response.send_message("❗ 이미 비활성화 상태입니다.", ephemeral=True)
                return

            result.enabled = 0
            db.commit()

        member_name = interaction.user.nick if interaction.user.nick else interaction.user.name
        logger.info(f"SleepMode || {member_name}({interaction.user.id})님 취침모드 비활성화")
        await interaction.response.send_message("✅ 취침모드가 비활성화되었습니다.", ephemeral=True)

    # S7: 설정 확인 명령어
    @app_commands.command(name="확인", description="현재 취침 모드 설정을 확인합니다.")
    async def check_sleep_mode_setting(self, interaction: discord.Interaction):
        with get_db() as db:
            result = db.query(SleepMode).filter(SleepMode.user_id == str(interaction.user.id)).first()

            if not result:
                await interaction.response.send_message("❗ 취침모드 설정이 없습니다. `/취침모드 설정`으로 설정해주세요.", ephemeral=True)
                return

            # 요일 설정 문자열 생성
            if result.weekdays and result.weekends:
                weekdays_str = "매일"
            elif result.weekdays:
                weekdays_str = "평일"
            elif result.weekends:
                weekdays_str = "휴일"
            else:
                weekdays_str = "없음"

            embed = discord.Embed(
                title="💤 취침 모드 설정",
                color=discord.Color.blue()
            )
            embed.add_field(name="활성화 상태", value="✅ 활성화" if result.enabled else "❌ 비활성화", inline=False)
            embed.add_field(name="시작 시간", value=result.start_time, inline=True)
            embed.add_field(name="종료 시간", value=result.end_time, inline=True)
            embed.add_field(name="적용 요일", value=weekdays_str, inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

class SleepEvent(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.check_task = None  # S6: 중복 태스크 방지용

    # check_sleep_mode 함수 루프 등록 (S6: 중복 생성 방지)
    @commands.Cog.listener()
    async def on_ready(self):
        if self.check_task is None or self.check_task.done():
            self.check_task = self.bot.loop.create_task(check_sleep_mode(self))

    # 보이스 채널 변경시 추방 여부 확인
    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if after.channel is None:
            return

        with get_db() as db:
            result = db.query(SleepMode).filter(
                SleepMode.user_id == str(member.id),
                SleepMode.enabled == 1
            ).first()

        if not result or not member.voice:
            return

        member_name = member.nick if member.nick else member.name

        current_time = datetime.now(tz)
        start_dt = datetime.strptime(result.start_time, "%H:%M").replace(year=current_time.year, month=current_time.month, day=current_time.day).replace(tzinfo=tz)
        end_dt = datetime.strptime(result.end_time, "%H:%M").replace(year=current_time.year, month=current_time.month, day=current_time.day).replace(tzinfo=tz)

        if end_dt < start_dt:
            if time(0, 0) <= current_time.time() <= end_dt.time():
                start_dt -= timedelta(days=1)
            else:
                end_dt += timedelta(days=1)

        try:
            holiday = check_holiday(end_dt)
        except:
            logger.error(f"SleepMode || 날짜 형식 오류 발생 | {result}")
            return

        # 설정, 휴일 여부 비교하여 조건에 맞는 경우 스킵
        if (result.weekdays == holiday) and not (result.weekdays and result.weekends):
            return

        if start_dt <= current_time <= end_dt:
            # 추방 전 채널 정보 미리 저장 (move_to 후 after.channel이 None이 됨)
            channel_name = after.channel.name if after.channel else "Unknown"
            channel_id = after.channel.id if after.channel else "Unknown"
            await member.move_to(None)

            # Rate limit 대응: 사용자별 DM 쿨다운 (30초)
            now_ts = datetime.now(tz).timestamp()
            last_dm_ts = dm_cooldown_cache.get(str(member.id), 0)
            if now_ts - last_dm_ts >= DM_COOLDOWN_SECONDS:
                # S5: DM 전송 실패(차단 등) 시 예외 처리
                await safe_send_dm(member, "현재 취침 시간입니다. 보이스 채널에 접속할 수 없습니다.")
                dm_cooldown_cache[str(member.id)] = now_ts
            # S13: 상세 로그
            logger.info(f"SleepMode || {member_name}({member.id})님 취침모드 추방 | 시간: {result.start_time}~{result.end_time}, 채널: {channel_name}({channel_id})")

# Rate limit 대응: 사용자별 DM 쿨다운 설정 (초)
DM_COOLDOWN_SECONDS = 30
# {user_id: 마지막 DM 전송 timestamp}
dm_cooldown_cache = {}

# S5: DM 전송 실패(차단, rate limit 등) 시 예외 처리 헬퍼
async def safe_send_dm(member, content):
    try:
        await member.send(content)
        return True
    except discord.Forbidden:
        logger.warning(f"SleepMode || DM 전송 실패(차단됨) | User: {member.id}")
        return False
    except discord.HTTPException as e:
        # Rate limit(429) 등 HTTP 예외 처리 - 봇 크래시 방지
        logger.warning(f"SleepMode || DM 전송 실패(HTTP {e.status}) | User: {member.id}, Error: {e}")
        return False
    except Exception as e:
        logger.error(f"SleepMode || DM 전송 중 오류 | User: {member.id}, Error: {e}")
        return False

# 휴일인지 체크하는 함수(공휴일, 주말)
def check_holiday(dt):
    if not isinstance(dt, datetime):
        raise TypeError("올바른 날짜 형식이 아닙니다.")
    holiday = is_holiday(dt.strftime("%Y-%m-%d"))
    week = dt.weekday() >= 5
    return holiday or week

# S2: 알림 중복 전송 방지용 캐시 {user_id: {notice_interval: 전송일자}}
notice_sent_cache = {}

# 1분 간격으로 멤버가 추방 조건이 되는지 확인
async def check_sleep_mode(self):
    await self.bot.wait_until_ready()
    while not self.bot.is_closed():
        current_time = datetime.now(tz)
        today_str = current_time.strftime("%Y-%m-%d")

        with get_db() as db:
            results = db.query(SleepMode).filter(SleepMode.enabled == 1).all()

        for result in results:
            for guild in self.bot.guilds:
                member = guild.get_member(int(result.user_id))
                # S1: member가 None인 경우 member_name 접근 전에 검사
                if not member or not member.voice:
                    continue
                member_name = member.nick if member.nick else member.name

                start_dt = datetime.strptime(result.start_time, "%H:%M").replace(year=current_time.year, month=current_time.month, day=current_time.day).replace(tzinfo=tz)
                end_dt = datetime.strptime(result.end_time, "%H:%M").replace(year=current_time.year, month=current_time.month, day=current_time.day).replace(tzinfo=tz)

                if end_dt < start_dt:
                    if time(0, 0) <= current_time.time() <= end_dt.time():
                        start_dt -= timedelta(days=1)
                    else:
                        end_dt += timedelta(days=1)

                holiday = check_holiday(end_dt)
                if (result.weekdays == holiday) and not (result.weekdays and result.weekends):
                    continue

                if start_dt <= current_time <= end_dt:
                    await member.move_to(None)
                    # S5: DM 전송 실패 시 예외 처리
                    await safe_send_dm(member, "현재 취침 시간입니다. 보이스 채널에 접속할 수 없습니다.")
                    # S13: 상세 로그
                    logger.info(f"SleepMode || {member_name}({member.id})님 취침모드 추방 | 시간: {result.start_time}~{result.end_time}, Guild: {guild.id}")
                    continue

                # S2: 알림 중복 전송 방지
                for notice_interval in NOTICE_INTERVALS:
                    notice_time = start_dt - timedelta(minutes=notice_interval)
                    if notice_time <= current_time < (notice_time + timedelta(seconds=59)):
                        # 당일 해당 interval 알림이 이미 전송되었는지 확인
                        user_cache = notice_sent_cache.setdefault(str(member.id), {})
                        cache_key = str(notice_interval)
                        if user_cache.get(cache_key) == today_str:
                            continue  # 중복 전송 방지

                        sent = await safe_send_dm(member, f"곧 취침 시간입니다. {notice_interval}분 남았습니다.")
                        if sent:
                            user_cache[cache_key] = today_str
                            logger.info(f"SleepMode || {member_name}({member.id})님에게 {notice_interval}분전 메세지 전송 | Guild: {guild.id}")

        elapsed_time = (datetime.now(tz) - current_time).total_seconds()
        await asyncio.sleep(max(60 - elapsed_time, 0))

async def setup(bot: commands.Bot):
    bot.tree.add_command(SleepModeCommand(bot))
    await bot.add_cog(SleepEvent(bot))
