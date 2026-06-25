import wavelink
from wavelink.exceptions import LavalinkException


# 기본 볼륨 (0~1000, 50 = 5%)
DEFAULT_VOLUME = 50


class ChannelIdPlayer(wavelink.Player):
    """wavelink.Player subclass that adds channelId to voice state PATCH (required by Lavalink 4.2.x+).

    wavelink 3.4.1 omits channelId from the VoiceState payload; Lavalink 4.2.x made it required.
    This subclass overrides _dispatch_voice_update to include it.
    또한 연결 시점에 기본 볼륨(50)을 Lavalink에 적용한다.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._volume = DEFAULT_VOLUME

    async def _dispatch_voice_update(self) -> None:
        assert self.guild is not None
        data: dict = self._voice_state["voice"]

        session_id: str | None = data.get("session_id", None)
        token: str | None = data.get("token", None)
        endpoint: str | None = data.get("endpoint", None)

        if not session_id or not token or not endpoint:
            return

        voice_payload: dict = {
            "sessionId": session_id,
            "token": token,
            "endpoint": endpoint,
        }
        if self.channel:
            voice_payload["channelId"] = str(self.channel.id)

        try:
            await self.node._update_player(self.guild.id, data={"voice": voice_payload})
        except LavalinkException:
            await self.disconnect()
        else:
            self._connection_event.set()
            # 음성 연결 성공 직후 Lavalink에 기본 볼륨 적용
            try:
                await self.set_volume(DEFAULT_VOLUME)
            except Exception:
                pass
