import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import Column, Integer, String
from db import Base, get_db
from utils import logger


# DB 테이블 정의
# ========================================================================================
class ChannelAccess(Base):
    __tablename__ = 'channel_access'
    id = Column(Integer, primary_key=True, autoincrement=True)
    server_id = Column(Integer, nullable=False)
    access_channel_id = Column(Integer, nullable=False)
    access_message_id = Column(Integer, nullable=False)
    target_channel_id = Column(Integer, nullable=False)
    target_channel_name = Column(String, nullable=False)


# ========================================================================================
class ChannelAccessCog(commands.GroupCog, name="채널접근"):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="채널생성", description="비공개 텍스트 채널을 생성합니다.")
    @app_commands.checks.has_permissions(administrator=True)
    async def create_channel(self, interaction: discord.Interaction, 채널명: str):
        channel = await interaction.guild.create_text_channel(채널명)
        await channel.set_permissions(interaction.guild.default_role, read_messages=False)
        await interaction.response.send_message(f"채널 '{channel.name}'이 생성되었습니다.", ephemeral=True)

    @app_commands.command(name="메시지생성", description="채널 접근 권한 부여 메시지를 생성합니다.")
    @app_commands.checks.has_permissions(administrator=True)
    async def create_message(self, interaction: discord.Interaction, 채널명: str):
        target_channel = discord.utils.get(interaction.guild.channels, name=채널명)
        if not target_channel:
            await interaction.response.send_message(f"채널 '{채널명}'이 존재하지 않습니다.", ephemeral=True)
            return
        if not target_channel.permissions_for(interaction.user).read_messages:
            await interaction.response.send_message(f"'{채널명}' 채널에 대한 읽기 권한이 없습니다.", ephemeral=True)
            return
        try:
            await interaction.response.defer(ephemeral=True)
            message = await interaction.channel.send(f"__**{채널명}**__ 채널 권한 부여를 위해 아래 이모지를 눌러주세요.")
            await message.add_reaction("✅")
            with get_db() as db:
                db.add(ChannelAccess(
                    server_id=interaction.guild.id,
                    access_channel_id=interaction.channel.id,
                    access_message_id=message.id,
                    target_channel_id=target_channel.id,
                    target_channel_name=채널명
                ))
                db.commit()
            await interaction.followup.send("권한 부여 메시지가 생성되었습니다.", ephemeral=True)
            logger.info(f"ChannelAccess || {interaction.user.display_name}님이 {채널명} 채널 권한 부여 메시지 생성")
        except Exception as e:
            logger.error(f"ChannelAccess || {interaction.user.display_name}님이 {채널명} 채널 권한 부여 메시지 생성 실패: {e}")
            await interaction.followup.send(f"채널 '{채널명}' 권한 부여 메시지 생성 실패", ephemeral=True)

    @app_commands.command(name="메시지삭제", description="채널 접근 권한 부여 메시지를 삭제합니다.")
    @app_commands.checks.has_permissions(administrator=True)
    async def delete_message(self, interaction: discord.Interaction, 채널명: str):
        target_channel = discord.utils.get(interaction.guild.channels, name=채널명)
        if not target_channel:
            await interaction.response.send_message(f"채널 '{채널명}'이 존재하지 않습니다.", ephemeral=True)
            return
        if not target_channel.permissions_for(interaction.user).read_messages:
            await interaction.response.send_message(f"'{채널명}' 채널에 대한 읽기 권한이 없습니다.", ephemeral=True)
            return
        with get_db() as db:
            record = db.query(ChannelAccess).filter_by(
                access_channel_id=interaction.channel.id,
                target_channel_id=target_channel.id
            ).first()
            if not record:
                await interaction.response.send_message(f"채널 '{채널명}'에 대한 권한 부여 메시지가 없습니다.", ephemeral=True)
                return
            try:
                await interaction.response.defer(ephemeral=True)
                msg = await interaction.channel.fetch_message(record.access_message_id)
                await msg.delete()
                db.delete(record)
                db.commit()
                await interaction.followup.send("권한 부여 메시지가 삭제되었습니다.", ephemeral=True)
                logger.info(f"ChannelAccess || {interaction.user.display_name}님이 {채널명} 채널 권한 부여 메시지 삭제")
            except Exception as e:
                logger.error(f"ChannelAccess || {interaction.user.display_name}님이 {채널명} 채널 권한 부여 메시지 삭제 실패: {e}")
                await interaction.followup.send(f"채널 '{채널명}' 권한 부여 메시지 삭제 실패", ephemeral=True)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload):
        if payload.member and payload.member.bot:
            return
        if str(payload.emoji) != '✅':
            return
        with get_db() as db:
            record = db.query(ChannelAccess).filter_by(
                access_message_id=payload.message_id,
                access_channel_id=payload.channel_id
            ).first()
        if record:
            target_channel = self.bot.get_channel(record.target_channel_id)
            member = self.bot.get_guild(payload.guild_id).get_member(payload.user_id)
            await target_channel.set_permissions(member, read_messages=True, send_messages=True)
            logger.info(f"ChannelAccess || {member.display_name}님이 {target_channel.name} 채널 접근 권한을 부여받았습니다.")

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload):
        if str(payload.emoji) != '✅':
            return
        with get_db() as db:
            record = db.query(ChannelAccess).filter_by(
                access_message_id=payload.message_id,
                access_channel_id=payload.channel_id
            ).first()
        if record:
            target_channel = self.bot.get_channel(record.target_channel_id)
            member = self.bot.get_guild(payload.guild_id).get_member(payload.user_id)
            await target_channel.set_permissions(member, read_messages=False, send_messages=False)
            logger.info(f"ChannelAccess || {member.display_name}님이 {target_channel.name} 채널 접근 권한을 취소했습니다.")

    @commands.Cog.listener()
    async def on_guild_channel_delete(self, channel):
        with get_db() as db:
            # target_channel 삭제: 연결된 권한 부여 메시지도 함께 제거
            records = db.query(ChannelAccess).filter_by(target_channel_id=channel.id).all()
            for record in records:
                access_ch = self.bot.get_channel(record.access_channel_id)
                if access_ch:
                    try:
                        msg = await access_ch.fetch_message(record.access_message_id)
                        await msg.delete()
                    except Exception:
                        pass
            db.query(ChannelAccess).filter_by(target_channel_id=channel.id).delete()
            logger.info(f"ChannelAccess || 채널 삭제: target_channel={channel.name} 관련 레코드 정리")

            # access_channel 삭제: 메시지는 이미 소멸, DB 레코드만 제거
            db.query(ChannelAccess).filter_by(access_channel_id=channel.id).delete()
            db.commit()
            logger.info(f"ChannelAccess || 채널 삭제: access_channel={channel.name} 관련 레코드 정리")

    @commands.Cog.listener()
    async def on_guild_channel_update(self, before, after):
        if before.name == after.name:
            return
        with get_db() as db:
            records = db.query(ChannelAccess).filter_by(target_channel_id=after.id).all()
            for record in records:
                access_ch = self.bot.get_channel(record.access_channel_id)
                if access_ch:
                    try:
                        msg = await access_ch.fetch_message(record.access_message_id)
                        await msg.edit(content=f"__**{after.name}**__ 채널 권한 부여를 위해 아래 이모지를 눌러주세요.")
                    except Exception:
                        pass
            db.query(ChannelAccess).filter_by(target_channel_id=after.id).update({'target_channel_name': after.name})
            db.commit()
        logger.info(f"ChannelAccess || 채널 이름 변경: {before.name} → {after.name}, 레코드 업데이트")


async def setup(bot: commands.Bot):
    await bot.add_cog(ChannelAccessCog(bot))
