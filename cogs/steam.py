import discord
from discord.ext import commands
from discord import app_commands
import aiohttp
import re
import asyncio
import urllib.parse
from datetime import datetime, timezone
from db import Base, get_db
from sqlalchemy import Column, String, Integer, DateTime
from utils import *
import json
import sys
import os

# JSON 파일 load
config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "config.json")

if not os.path.isfile(config_path):
    sys.exit("'config.json' not found in project root! Please add it and try again.")
else:
    with open(config_path, encoding="utf-8") as file:
        config = json.load(file)

# config.json 기본 지역 (폴백용)
DEFAULT_PRIMARY_REGION = config['steam']['primary_region']
DEFAULT_SECONDARY_REGION = config['steam']['secondary_region']

# ========================================================================================
# ST2: 통화/국가 매핑 데이터 (드롭다운 옵션 생성용)
# ========================================================================================
STEAM_REGIONS = {
    "KR": {"currency": "KRW", "language": "korean",   "flag": "🇰🇷", "name": "한국"},
    "JP": {"currency": "JPY", "language": "japanese", "flag": "🇯🇵", "name": "일본"},
    "US": {"currency": "USD", "language": "english",  "flag": "🇺🇸", "name": "미국"},
    "EU": {"currency": "EUR", "language": "english",  "flag": "🇪🇺", "name": "유럽"},
    "GB": {"currency": "GBP", "language": "english",  "flag": "🇬🇧", "name": "영국"},
    "TR": {"currency": "TRY", "language": "turkish",  "flag": "🇹🇷", "name": "튀르키예"},
    "RU": {"currency": "RUB", "language": "russian",  "flag": "🇷🇺", "name": "러시아"},
    "BR": {"currency": "BRL", "language": "portuguese","flag": "🇧🇷", "name": "브라질"},
    "CA": {"currency": "CAD", "language": "english",  "flag": "🇨🇦", "name": "캐나다"},
    "AU": {"currency": "AUD", "language": "english",  "flag": "🇦🇺", "name": "호주"},
    "IN": {"currency": "INR", "language": "english",  "flag": "🇮🇳", "name": "인도"},
    "CN": {"currency": "CNY", "language": "schinese", "flag": "🇨🇳", "name": "중국"},
}

# ========================================================================================
# ST2: DB 테이블 정의
# ========================================================================================
class SteamGuildSettings(Base):
    """서버별 스팀 지역 설정 (관리자 전용)"""
    __tablename__ = 'steam_guild_settings'
    guild_id = Column(Integer, primary_key=True)
    base_country_code = Column(String, nullable=False)
    base_currency = Column(String, nullable=False)
    base_language = Column(String, nullable=False, default='korean')
    compare_country_code = Column(String, nullable=False)
    compare_currency = Column(String, nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

class SteamUserSettings(Base):
    """개인별 스팀 지역 설정"""
    __tablename__ = 'steam_user_settings'
    user_id = Column(Integer, primary_key=True)
    base_country_code = Column(String, nullable=False)
    base_currency = Column(String, nullable=False)
    base_language = Column(String, nullable=False, default='korean')
    compare_country_code = Column(String, nullable=False)
    compare_currency = Column(String, nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))

# ========================================================================================
# ST2: DB CRUD 헬퍼
# ========================================================================================
def get_guild_settings(guild_id):
    with get_db() as db:
        return db.query(SteamGuildSettings).filter(SteamGuildSettings.guild_id == guild_id).first()

def get_user_settings(user_id):
    with get_db() as db:
        return db.query(SteamUserSettings).filter(SteamUserSettings.user_id == user_id).first()

def save_guild_settings(guild_id, base_cc, compare_cc):
    info_base = STEAM_REGIONS[base_cc]
    info_compare = STEAM_REGIONS[compare_cc]
    with get_db() as db:
        existing = db.query(SteamGuildSettings).filter(SteamGuildSettings.guild_id == guild_id).first()
        if existing:
            existing.base_country_code = base_cc
            existing.base_currency = info_base['currency']
            existing.base_language = info_base['language']
            existing.compare_country_code = compare_cc
            existing.compare_currency = info_compare['currency']
            existing.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        else:
            db.add(SteamGuildSettings(
                guild_id=guild_id,
                base_country_code=base_cc,
                base_currency=info_base['currency'],
                base_language=info_base['language'],
                compare_country_code=compare_cc,
                compare_currency=info_compare['currency'],
            ))
        db.commit()

def save_user_settings(user_id, base_cc, compare_cc):
    info_base = STEAM_REGIONS[base_cc]
    info_compare = STEAM_REGIONS[compare_cc]
    with get_db() as db:
        existing = db.query(SteamUserSettings).filter(SteamUserSettings.user_id == user_id).first()
        if existing:
            existing.base_country_code = base_cc
            existing.base_currency = info_base['currency']
            existing.base_language = info_base['language']
            existing.compare_country_code = compare_cc
            existing.compare_currency = info_compare['currency']
            existing.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        else:
            db.add(SteamUserSettings(
                user_id=user_id,
                base_country_code=base_cc,
                base_currency=info_base['currency'],
                base_language=info_base['language'],
                compare_country_code=compare_cc,
                compare_currency=info_compare['currency'],
            ))
        db.commit()

def delete_user_settings(user_id):
    with get_db() as db:
        db.query(SteamUserSettings).filter(SteamUserSettings.user_id == user_id).delete()
        db.commit()

def delete_guild_settings(guild_id):
    with get_db() as db:
        db.query(SteamGuildSettings).filter(SteamGuildSettings.guild_id == guild_id).delete()
        db.commit()

# ========================================================================================
# ST2: 지역 결정 로직 (우선순위)
# ========================================================================================
def resolve_regions(author_id, guild_id):
    """
    반환: list of dict, 각 원소는 {scope, country_code, currency, language}
    우선순위:
      - 서버+개인 모두: 개인 base, 서버 compare, 개인 compare (3개)
      - 서버만: 서버 base, 서버 compare (2개)
      - 개인만: 개인 base, 개인 compare (2개)
      - 둘 다 없음: config.json 폴백 (2개)
    """
    user_cfg = get_user_settings(author_id)
    guild_cfg = get_guild_settings(guild_id) if guild_id else None

    def _region(scope, cc, currency, language):
        return {"scope": scope, "country_code": cc, "currency": currency, "language": language}

    if user_cfg and guild_cfg:
        regions = [
            _region("개인", user_cfg.base_country_code, user_cfg.base_currency, user_cfg.base_language),
            _region("서버", guild_cfg.compare_country_code, guild_cfg.compare_currency, guild_cfg.base_language),
        ]
        # 서버 compare와 개인 compare가 같은 국가면 개인 compare는 중복 표시 생략
        if user_cfg.compare_country_code != guild_cfg.compare_country_code:
            regions.append(_region("개인", user_cfg.compare_country_code, user_cfg.compare_currency, user_cfg.base_language))
        return regions
    elif guild_cfg:
        return [
            _region("서버", guild_cfg.base_country_code, guild_cfg.base_currency, guild_cfg.base_language),
            _region("서버", guild_cfg.compare_country_code, guild_cfg.compare_currency, guild_cfg.base_language),
        ]
    elif user_cfg:
        return [
            _region("개인", user_cfg.base_country_code, user_cfg.base_currency, user_cfg.base_language),
            _region("개인", user_cfg.compare_country_code, user_cfg.compare_currency, user_cfg.base_language),
        ]
    else:
        # config.json 폴백
        return [
            _region("기본", DEFAULT_PRIMARY_REGION['country_code'], DEFAULT_PRIMARY_REGION['currency'], DEFAULT_PRIMARY_REGION['language']),
            _region("기본", DEFAULT_SECONDARY_REGION['country_code'], DEFAULT_SECONDARY_REGION['currency'], DEFAULT_PRIMARY_REGION['language']),
        ]

# ========================================================================================
# ST1: 환율 데이터 (open.er-api.com, USD 기준)
# ========================================================================================
EXCHANGE_API_URL = "https://open.er-api.com/v6/latest/USD"

# exchange.json 경로
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
DB_DIR = os.path.join(BASE_DIR, 'db')
EXCHANGE_FILE = os.path.join(DB_DIR, 'exchange.json')

if not os.path.exists(DB_DIR):
    os.makedirs(DB_DIR)

# 환율 데이터 캐시 (ST3.4: 스레드 안전성을 위해 Lock 사용)
exchange_data = None
exchange_lock = asyncio.Lock()

def _load_exchange_file():
    """exchange.json 파일에서 환율 데이터 로드 (없으면 None)"""
    global exchange_data
    if os.path.isfile(EXCHANGE_FILE):
        try:
            with open(EXCHANGE_FILE, 'r', encoding='utf-8') as f:
                exchange_data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"Steam || exchange.json 로드 실패: {e}")
            exchange_data = None

def save_exchange_config(data):
    with open(EXCHANGE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

# 초기 로드 (파일이 있으면 사용, 없으면 None — ST3.7: sys.exit 대신 폴백)
_load_exchange_file()

async def fetch_exchange_rate(session: aiohttp.ClientSession):
    """open.er-api.com 에서 환율 데이터 조회 (USD 기준)"""
    try:
        async with session.get(EXCHANGE_API_URL, timeout=aiohttp.ClientTimeout(total=15)) as res:
            if res.status != 200:
                logger.error(f'Steam || 환율 API 요청 실패: HTTP {res.status}')
                return None
            data = await res.json()
            if data.get("result") != "success":
                logger.error('Steam || 환율 API 응답 오류')
                return None
            return data
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        logger.error(f'Steam || 환율 API 요청 예외: {e}')
        return None

async def update_exchange_rate(session: aiohttp.ClientSession):
    """환율 데이터 갱신 (Lock 보호)"""
    global exchange_data
    data = await fetch_exchange_rate(session)
    if data:
        async with exchange_lock:
            exchange_data = data
            save_exchange_config(data)
        base_cur = DEFAULT_PRIMARY_REGION['currency']
        comp_cur = DEFAULT_SECONDARY_REGION['currency']
        rates = data.get('rates', {})
        if base_cur in rates and comp_cur in rates:
            logger.info(f"Steam || 환율 갱신: 1 {comp_cur} ≈ {rates[base_cur]/rates[comp_cur]:.2f} {base_cur} ({data.get('time_last_update_utc')})")
        else:
            logger.info(f"Steam || 환율 갱신 완료 ({data.get('time_last_update_utc')})")

def is_exchange_stale():
    """환율 데이터가 1시간 이상 경과했는지 확인 (ST1)"""
    if not exchange_data:
        return True
    last_update = exchange_data.get('time_last_update_unix')
    if not last_update:
        return True
    now_unix = int(datetime.now(timezone.utc).timestamp())
    return (now_unix - int(last_update)) > 3600

async def update_exchange_if_stale(session: aiohttp.ClientSession):
    if is_exchange_stale():
        await update_exchange_rate(session)

# 1시간마다 환율 갱신 (ST1)
async def exchange_rate_updater():
    async with aiohttp.ClientSession() as session:
        while True:
            await asyncio.sleep(60 * 60)
            await update_exchange_rate(session)

# ========================================================================================
# ST3.1: 비동기 게임 정보 조회
# ========================================================================================
async def get_game_info(session: aiohttp.ClientSession, app_id, regions):
    """
    regions: resolve_regions() 반환값 (가변 개수)
    반환: game_info dict (name, description, image, prices: list)
    """
    # 첫 지역으로 기본 정보(name, description, image) 조회
    first = regions[0]
    url_base = f"https://store.steampowered.com/api/appdetails?appids={app_id}&cc={first['country_code']}&l={first['language']}"
    try:
        async with session.get(url_base, timeout=aiohttp.ClientTimeout(total=15)) as res_base:
            if res_base.status != 200:
                logger.error(f"Steam || appdetails 요청 실패 (base): HTTP {res_base.status}")
                return None
            data_base = await res_base.json()
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        logger.error(f"Steam || appdetails 요청 예외 (base): {e}")
        return None

    if not data_base.get(str(app_id), {}).get("success", False):
        logger.warning(f"Steam || appdetails success=false (app_id={app_id})")
        return None
    result_base = data_base[str(app_id)]['data']

    game_info = {
        'app_id': app_id,
        'name': result_base['name'],
        'short_description': result_base.get('short_description', ''),
        'image': result_base.get('header_image', ''),
        'is_free': result_base.get('is_free', False),
        'prices': [],  # 각 지역별 가격 정보
    }

    if game_info['is_free']:
        return game_info

    # 환율 데이터 복사본 (ST3.4: Lock 없이 읽기)
    rates = None
    if exchange_data:
        rates = exchange_data.get('rates', {})

    # 각 지역별 가격 조회
    for idx, region in enumerate(regions):
        # 첫 지역은 이미 조회한 데이터 재사용, 나머지는 별도 조회
        if idx == 0:
            result = result_base
        else:
            url = f"https://store.steampowered.com/api/appdetails?appids={app_id}&cc={region['country_code']}&l={region['language']}"
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as res:
                    if res.status != 200:
                        continue
                    data = await res.json()
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.warning(f"Steam || appdetails 요청 예외 ({region['country_code']}): {e}")
                continue
            if not data.get(str(app_id), {}).get("success", False):
                continue
            result = data[str(app_id)]['data']

        price_overview = result.get('price_overview')
        if not price_overview:
            continue

        price_entry = {
            'scope': region['scope'],
            'country_code': region['country_code'],
            'currency': region['currency'],
            'final_formatted': price_overview.get('final_formatted'),
            'initial': price_overview.get('initial'),
            'final': price_overview.get('final'),
            'discount_percent': price_overview.get('discount_percent', 0),
        }

        # 환율 변환: base 통화(첫 지역) 기준으로 환산 (ST3.3: 키 누락 방어)
        if rates and idx > 0:
            base_currency = regions[0]['currency']
            target_currency = region['currency']
            if base_currency in rates and target_currency in rates and rates[target_currency] != 0:
                exchange = rates[base_currency] / rates[target_currency]
                base_symbol = regions[0]['currency']
                converted = int(price_overview['final'] * exchange / 100)
                price_entry['converted'] = f"{base_symbol} {converted:,}"

        game_info['prices'].append(price_entry)

    return game_info

def embed_form(author, game_info):
    safe_name = urllib.parse.quote(game_info['name'])
    store_url = f"https://store.steampowered.com/app/{game_info['app_id']}/{safe_name}/"
    embed = discord.Embed(
        title=game_info['name'],
        url=store_url,
        description=f"[Steam DB](https://steamdb.info/app/{game_info['app_id']})\n{game_info['short_description']}",
        color=discord.Color.default()
    )

    embed.set_author(
        name=f"{author.display_name}",
        icon_url=author.display_avatar.url if author.display_avatar else None
    )

    embed.set_image(url=game_info['image'])

    if game_info.get('is_free') or not game_info['prices']:
        embed.add_field(name="Price", value="Free", inline=False)
        return embed

    for price in game_info['prices']:
        scope_label = f"[{price['scope']}] " if price['scope'] != "기본" else ""
        field_name = f"{scope_label}Price ({price['country_code']})"

        if price['discount_percent'] > 0:
            initial = int(price['initial'] / 100)
            value = f"~~{initial:,}~~ (-{price['discount_percent']}%) -> {price['final_formatted']}"
        else:
            value = f"{price['final_formatted']}"

        if price.get('converted'):
            value += f" ({price['converted']})"

        embed.add_field(name=field_name, value=value, inline=False)

    return embed

# ========================================================================================
# ST2: 드롭다운 설정 View
# ========================================================================================
def _build_region_options():
    return [
        discord.SelectOption(
            label=f"{info['flag']} {info['name']} ({cc})",
            value=cc,
            description=f"{info['currency']} - {info['language']}"
        )
        for cc, info in STEAM_REGIONS.items()
    ]

class SteamSettingView(discord.ui.View):
    """2단계 드롭다운: base 선택 → compare 선택 → DB 저장"""
    def __init__(self, scope: str, target_id: int):
        super().__init__(timeout=120)
        self.scope = scope  # "guild" | "user"
        self.target_id = target_id
        self.selected_base = None

        base_select = discord.ui.Select(
            placeholder="기준(Base) 통화를 선택하세요",
            options=_build_region_options(),
            min_values=1, max_values=1
        )
        base_select.callback = self._base_callback
        self.add_item(base_select)

    async def _base_callback(self, interaction: discord.Interaction):
        self.selected_base = self.children[0].values[0]
        base_info = STEAM_REGIONS[self.selected_base]

        # View를 compare 선택으로 교체
        new_view = discord.ui.View(timeout=120)
        compare_select = discord.ui.Select(
            placeholder="비교(Compare) 통화를 선택하세요",
            options=_build_region_options(),
            min_values=1, max_values=1
        )
        compare_select.callback = lambda i: self._compare_callback(i, new_view)
        new_view.add_item(compare_select)

        await interaction.response.edit_message(
            content=f"✅ 기준: {base_info['flag']} {base_info['name']} ({self.selected_base})\n비교(Compare) 통화를 선택하세요.",
            view=new_view
        )

    async def _compare_callback(self, interaction: discord.Interaction, view: discord.ui.View):
        compare_cc = view.children[0].values[0]
        if compare_cc == self.selected_base:
            await interaction.response.send_message("❗ 기준 통화와 비교 통화가 같습니다. 다시 시도해주세요.", ephemeral=True)
            return

        # DB 저장
        if self.scope == "guild":
            save_guild_settings(self.target_id, self.selected_base, compare_cc)
        else:
            save_user_settings(self.target_id, self.selected_base, compare_cc)

        base_info = STEAM_REGIONS[self.selected_base]
        compare_info = STEAM_REGIONS[compare_cc]
        for child in view.children:
            child.disabled = True

        await interaction.response.edit_message(
            content=f"✅ 설정 완료!\n기준: {base_info['flag']} {base_info['name']} ({self.selected_base})\n비교: {compare_info['flag']} {compare_info['name']} ({compare_cc})",
            view=view
        )

# ========================================================================================
# ST2: 슬래시 명령어 그룹
# ========================================================================================
class SteamSettingCommand(app_commands.Group):
    def __init__(self, bot: commands.Bot):
        super().__init__(name="스팀설정", description="스팀 가격 비교 지역 설정")

    @app_commands.command(name="서버", description="서버의 스팀 비교 지역을 설정합니다. (관리자 전용)")
    @app_commands.default_permissions(manage_guild=True)
    async def set_guild(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("❗ 서버에서만 사용 가능합니다.", ephemeral=True)
            return
        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message("❗ 관리자 권한이 필요합니다.", ephemeral=True)
            return

        view = SteamSettingView(scope="guild", target_id=interaction.guild.id)
        await interaction.response.send_message(
            "🛒 서버 스팀 지역 설정 — 기준(Base) 통화를 선택하세요.",
            view=view, ephemeral=True
        )

    @app_commands.command(name="개인", description="개인 스팀 비교 지역을 설정합니다.")
    async def set_user(self, interaction: discord.Interaction):
        view = SteamSettingView(scope="user", target_id=interaction.user.id)
        await interaction.response.send_message(
            "🛒 개인 스팀 지역 설정 — 기준(Base) 통화를 선택하세요.",
            view=view, ephemeral=True
        )

    @app_commands.command(name="확인", description="현재 적용될 스팀 지역 조합을 확인합니다.")
    async def check_settings(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id if interaction.guild else None
        regions = resolve_regions(interaction.user.id, guild_id)

        embed = discord.Embed(title="🛒 스팀 지역 설정", color=discord.Color.green())
        for region in regions:
            info = STEAM_REGIONS.get(region['country_code'], {})
            flag = info.get('flag', '')
            name = info.get('name', region['country_code'])
            embed.add_field(
                name=f"{flag} {region['scope']} - {name} ({region['country_code']})",
                value=f"통화: {region['currency']}",
                inline=False
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="초기화", description="스팀 지역 설정을 초기화합니다.")
    @app_commands.describe(scope="초기화할 범위 (개인만 가능, 서버는 관리자)")
    @app_commands.choices(scope=[
        app_commands.Choice(name="개인", value="user"),
        app_commands.Choice(name="서버", value="guild"),
    ])
    async def reset_settings(self, interaction: discord.Interaction, scope: app_commands.Choice[str]):
        if scope.value == "guild":
            if not interaction.guild:
                await interaction.response.send_message("❗ 서버에서만 사용 가능합니다.", ephemeral=True)
                return
            if not interaction.user.guild_permissions.manage_guild:
                await interaction.response.send_message("❗ 관리자 권한이 필요합니다.", ephemeral=True)
                return
            delete_guild_settings(interaction.guild.id)
            await interaction.response.send_message("✅ 서버 스팀 설정이 초기화되었습니다.", ephemeral=True)
        else:
            delete_user_settings(interaction.user.id)
            await interaction.response.send_message("✅ 개인 스팀 설정이 초기화되었습니다.", ephemeral=True)

# ========================================================================================
# Cog
# ========================================================================================
class Steam(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.session = aiohttp.ClientSession()

    def cog_unload(self):
        asyncio.create_task(self.session.close())

    @commands.Cog.listener()
    async def on_message(self, message):
        # app_id 추출
        if message.content.startswith('https://store.steampowered.com/app'):
            match = re.search(r'/app/(\d+)', message.content)
            if not match:
                return
            app_id = match.group(1)

            guild_id = message.guild.id if message.guild else None
            regions = resolve_regions(message.author.id, guild_id)

            try:
                game_info = await get_game_info(self.session, app_id, regions)
                if not game_info:
                    await message.channel.send("❗ 게임 정보를 가져오지 못했습니다.")
                    return
                embed = embed_form(message.author, game_info)
                # ST3.6: 메시지 삭제 권한 예외 처리
                try:
                    await message.delete()
                except discord.Forbidden:
                    logger.warning(f"Steam || 메시지 삭제 권한 없음 (channel: {message.channel.id})")
                except discord.HTTPException as e:
                    logger.warning(f"Steam || 메시지 삭제 실패: {e}")
                await message.channel.send(embed=embed)
            except aiohttp.ClientError as e:
                logger.error(f"Steam || on_message 네트워크 오류: {e}")
            except KeyError as e:
                logger.error(f"Steam || on_message 데이터 키 누락: {e}")
            except Exception as e:
                logger.error(f"Steam || on_message 예외: {type(e).__name__}: {e}")
                return

async def setup(bot: commands.Bot) -> None:
    # ST1: 환율 초기화 (파일이 없거나 stale하면 갱신 시도)
    cog = Steam(bot)
    async with aiohttp.ClientSession() as init_session:
        await update_exchange_if_stale(init_session)
    asyncio.create_task(exchange_rate_updater())
    bot.tree.add_command(SteamSettingCommand(bot))
    await bot.add_cog(cog)
