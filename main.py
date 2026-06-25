import discord
import wavelink
import asyncio
from discord import app_commands
from discord.ext import commands
from db import engine, Base
from utils.logger import logger

import os
import sys
import json
from dotenv import load_dotenv
load_dotenv()

if not os.path.isfile(f"{os.path.realpath(os.path.dirname(__file__))}/config.json"):
    sys.exit("'config.json' not found! Please add it and try again.")
else:
    with open(f"{os.path.realpath(os.path.dirname(__file__))}/config.json", encoding="utf-8") as file:
        config = json.load(file)

discord_token = os.getenv('DISCORD_TOKEN')
# typecast_api =os.getenv('TYPECAST_API')
bot_prefix = config["prefix"]

# =========================

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True
intents.presences = True
intents.reactions = True
intents.voice_states = True
intents.guild_messages = True
intents.guild_reactions = True

bot = commands.Bot(command_prefix=bot_prefix, intents=intents)

class DiscordBot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(
            command_prefix=commands.when_mentioned_or(config["prefix"]),
            intents=intents,
            help_command=None,
        )
        self.config = config

    async def load_cogs(self) -> None:
        for file in os.listdir(f"{os.path.realpath(os.path.dirname(__file__))}/cogs"):
            if file.endswith(".py"):
                extension = file[:-3]
                try:
                    await self.load_extension(f"cogs.{extension}")
                    logger.info(f"Loaded extension '{extension}'")
                except Exception as e:
                    exception = f"{type(e).__name__}: {e}"
                    logger.error(f"Failed to load extension {extension}\n{exception}")
        # SQLAlchemy DB 설정
        Base.metadata.create_all(bind=engine)

    def _get_node_status(self) -> str:
        """음성 채널 점유 노드 수 / 전체 가용 노드 수 반환"""
        try:
            pool = wavelink.Pool
            nodes = list(pool.nodes.values())
            total = len(nodes)
            # 음성 채널에 접속(점유) 중인 플레이어 수 = 사용 중인 노드 수
            in_use = sum(1 for n in nodes if n.players and len(n.players) > 0)
            return f"({in_use}/{total})"
        except Exception as e:
            logger.error(f"노드 상태 조회 오류: {e}")
            return "(0/0)"

    async def _update_presence(self) -> None:
        """상태 메세지 갱신: bot_activity + 노드 상태"""
        node_status = self._get_node_status()
        activity_name = f"{config['bot_activity']} {node_status}"
        await self.change_presence(activity=discord.Game(name=activity_name))

    async def on_ready(self) -> None:
        await self._update_presence()
        synced = await self.tree.sync()
        logger.info(f"{len(synced)}개의 슬래시 명령어가 동기화됨!")
        # 주기적 상태 메세지 갱신 (노드 연결 상태 반영)
        if not getattr(self, "_presence_task", None) or self._presence_task.done():
            self._presence_task = asyncio.create_task(self._presence_loop())

    async def _presence_loop(self) -> None:
        """30초마다 상태 메세지 갱신"""
        while not self.is_closed():
            try:
                await self._update_presence()
            except Exception as e:
                logger.error(f"상태 메세지 갱신 오류: {e}")
            await asyncio.sleep(30)

    def _build_lavalink_nodes(self) -> list:
        """환경변수에서 Lavalink 노드 목록 생성.

        필수 환경변수:
        - LAVALINK_PASSWORD: 모든 노드 공통 비밀번호
        - LAVALINK_NODES: JSON 배열 (각 노드는 identifier/host 필수, port 선택·기본 2333)
          예: [{"identifier":"node1","host":"lavalink"},
               {"identifier":"node2","host":"lavalink2","port":2334}]

        누락 시 에러 로그 출력 후 봇을 종료(sys.exit)한다.
        """
        import json
        import sys

        password = os.getenv("LAVALINK_PASSWORD")
        if not password:
            logger.error("필수 환경변수 LAVALINK_PASSWORD가 설정되지 않았습니다. .env를 확인하세요.")
            sys.exit(1)

        nodes_json = os.getenv("LAVALINK_NODES")
        if not nodes_json:
            logger.error("필수 환경변수 LAVALINK_NODES가 설정되지 않았습니다. .env를 확인하세요.")
            sys.exit(1)

        try:
            node_configs = json.loads(nodes_json)
        except json.JSONDecodeError as e:
            logger.error(f"LAVALINK_NODES JSON 파싱 실패: {e}")
            sys.exit(1)

        if not isinstance(node_configs, list) or not node_configs:
            logger.error("LAVALINK_NODES는 최소 1개 이상의 노드가 포함된 JSON 배열이어야 합니다.")
            sys.exit(1)

        nodes = []
        for i, cfg in enumerate(node_configs):
            identifier = cfg.get("identifier")
            host = cfg.get("host")
            if not identifier:
                logger.error(f"LAVALINK_NODES[{i}]의 identifier가 누락되었습니다.")
                sys.exit(1)
            if not host:
                logger.error(f"LAVALINK_NODES[{i}]의 host가 누락되었습니다.")
                sys.exit(1)
            port = int(cfg.get("port", 2333))
            nodes.append(
                wavelink.Node(
                    identifier=identifier,
                    uri=f"http://{host}:{port}",
                    password=password,
                )
            )
        return nodes

    async def setup_hook(self) -> None:
        await self.load_cogs()
        # Lavalink 노드 연결 (다중 노드 지원)
        # 노드 설정은 .env의 LAVALINK_PASSWORD + LAVALINK_NODES(JSON 배열)로 관리
        nodes = self._build_lavalink_nodes()
        try:
            await wavelink.Pool.connect(nodes=nodes, client=self, cache_capacity=100)
            logger.info(f"Lavalink 노드 연결 성공 ({len(nodes)}개 노드)")
        except Exception as e:
            logger.error(f"Lavalink 노드 연결 실패: {e}")


load_dotenv()

bot = DiscordBot()
bot.run(discord_token)