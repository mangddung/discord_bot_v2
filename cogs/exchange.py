import math
import re
import textwrap

import discord
from discord import app_commands
from discord.ext import commands

from utils import exchange_rate

# "1,000usd", "1000 usd", "usd" -> (금액 문자열, 통화 코드)
_AMOUNT_TICKER = re.compile(r"^\s*([0-9,]*\.?[0-9]*)\s*([A-Za-z]{3})\s*$")


def parse_amount_ticker(text: str):
    """(금액, 통화 코드) 반환. 금액 생략 시 1. 형식 오류/0 이하/비정상 값이면 None"""
    m = _AMOUNT_TICKER.match(text)
    if not m:
        return None
    num = m.group(1).replace(",", "")
    try:
        amount = float(num) if num else 1.0
    except ValueError:
        return None
    if not math.isfinite(amount) or amount <= 0:
        return None
    return amount, m.group(2).upper()


def _fmt(value: float) -> str:
    return f"{value:,.2f}" if value >= 1 else f"{value:.4g}"


def _fmt_amount(value: float) -> str:
    return f"{value:,.8f}".rstrip("0").rstrip(".")


class Exchange(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="환율", description="환율 조회/환산. 예) /환율, /환율 jpy, /환율 1000usd krw")
    @app_commands.describe(base="[금액]기준통화 (기본 USD, 예: 1000usd, 1,000 jpy)", target="비교 통화 (기본 KRW)")
    async def exchange(self, interaction: discord.Interaction, base: str = "USD", target: str = "KRW") -> None:
        parsed = parse_amount_ticker(base)
        if not parsed:
            await interaction.response.send_message("❗ 형식이 올바르지 않습니다. 예) `1000usd`, `1,000 jpy`, `usd`", ephemeral=True)
            return
        amount, base_code = parsed
        target_code = target.strip().upper()

        data = exchange_rate.get_data()
        if not data:
            await interaction.response.send_message("❗ 환율 데이터를 아직 불러오지 못했습니다. 잠시 후 다시 시도해주세요.", ephemeral=True)
            return

        rates = exchange_rate.get_rates()
        unknown = [c for c in (base_code, target_code) if c not in rates]
        if unknown:
            await interaction.response.send_message(f"❗ 지원하지 않는 통화 코드: {', '.join(unknown)} (`/환율목록` 참고)", ephemeral=True)
            return
        if base_code == target_code:
            await interaction.response.send_message("❗ 기준 통화와 비교 통화가 같습니다.", ephemeral=True)
            return

        rate = exchange_rate.rate(base_code, target_code)
        if rate is None:
            await interaction.response.send_message("❗ 환율을 계산할 수 없습니다.", ephemeral=True)
            return

        updated = f"-# 갱신: <t:{int(data['time_last_update_unix'])}:f>"
        has_amount = bool(re.search(r"[0-9]", base))
        if has_amount:
            msg = (
                f"💱 **{_fmt_amount(amount)} {base_code} = {_fmt(amount * rate)} {target_code}**\n"
                f"(1 {base_code} = {_fmt(rate)} {target_code})\n{updated}"
            )
        else:
            msg = f"💱 **1 {base_code} = {_fmt(rate)} {target_code}**"
            change = exchange_rate.change_percent(base_code, target_code)
            if change is not None:
                arrow = "▲" if change > 0 else "▼" if change < 0 else "―"
                msg += f"\n{arrow} {abs(change):.2f}% (24시간 전 대비)"
            msg += f"\n{updated}"
        await interaction.response.send_message(msg, ephemeral=True)

    @app_commands.command(name="환율목록", description="/환율에서 사용 가능한 통화 코드 목록")
    async def exchange_list(self, interaction: discord.Interaction) -> None:
        codes = sorted(exchange_rate.get_rates())
        if not codes:
            await interaction.response.send_message("❗ 환율 데이터를 아직 불러오지 못했습니다. 잠시 후 다시 시도해주세요.", ephemeral=True)
            return
        body = textwrap.fill(" ".join(codes), width=48)
        await interaction.response.send_message(f"💱 사용 가능한 통화 코드 ({len(codes)}개)\n```\n{body}\n```", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await exchange_rate.start()
    await bot.add_cog(Exchange(bot))
