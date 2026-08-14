"""WeCom bot long-connection WebSocket transport.

Protocol (mirrors the official ``@wecom/aibot-node-sdk`` WSClient):

1. Connect to ``wss://openws.work.weixin.qq.com``.
2. Send ``aibot_subscribe`` with ``{bot_id, secret, scene, plug_version}``; the
   server replies ``{headers:{req_id}, errcode:0}``.
3. Heartbeat ``ping`` every 30s; 2 consecutive missed pongs ⇒ reconnect.
4. Server pushes ``aibot_msg_callback`` (messages) / ``aibot_event_callback``
   (events). Outbound replies echo the callback's ``headers.req_id``.
5. Every outbound frame is acked by ``{headers:{req_id}, errcode, errmsg}``.

The transport is both the network receiver (feeds ``pipeline.submit``) and the
outbound client the adapter uses (reply / proactive send / chunked media
upload), since long-connection mode has no separate HTTP channel.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import secrets
import time
from typing import Any

import structlog
import websockets

from aio_agent_platform.channels.adapter import Transport, TransportState
from aio_agent_platform.channels.pipeline import ChannelInboundPipeline
from aio_agent_platform.channels.wecom_bot.events import normalize_event

logger = structlog.get_logger()

_DEFAULT_WS_URL = "wss://openws.work.weixin.qq.com"

_WS_HEARTBEAT_INTERVAL = 30.0
_WS_RECONNECT_BASE_DELAY = 1.0
_WS_RECONNECT_MAX_DELAY = 30.0
_MAX_MISSED_PONG = 2
_AUTH_TIMEOUT = 10.0
_ACK_TIMEOUT = 5.0
_OPEN_TIMEOUT = 10.0

_SCENE = 1  # SCENE_WECOM_OPENCLAW（官方插件值）
_PLUG_VERSION = "1.0.0"

# 单个分片上限 512KB（Base64 编码前），最多 100 片 ≈ 50MB。
_CHUNK_SIZE = 512 * 1024
_MAX_CHUNKS = 100

# 流式通道过期错误码（会话超时 ~6 分钟）——需降级为主动发送完整文本。
_STREAM_EXPIRED_ERRCODE = 846608


class StreamExpiredError(RuntimeError):
    """The stream reply channel expired; the caller should fall back to proactive send."""


class AckError(RuntimeError):
    """The server returned a non-zero errcode for an outbound frame."""


def _gen_req_id(prefix: str) -> str:
    return f"{prefix}_{int(time.time() * 1000)}_{secrets.token_hex(6)}"


class WeComBotTransport(Transport):
    """WebSocket transport for WeCom API-mode smart bots (long connection)."""

    def __init__(
        self,
        pipeline: ChannelInboundPipeline,
        bot_id: str,
        secret: str,
        ws_url: str = _DEFAULT_WS_URL,
    ):
        self.pipeline = pipeline
        self.bot_id = bot_id
        self.secret = secret
        self.ws_url = ws_url
        self.state = TransportState.DISCONNECTED

        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._ws: Any | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._ping_req_ids: set[str] = set()
        self._ping_pending = False
        self._missed_pong = 0

    # ---- lifecycle ----

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop())
        logger.info("wecom_bot_transport_started", bot_id=self.bot_id)

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._ws = None
        self.state = TransportState.DISCONNECTED
        logger.info("wecom_bot_transport_stopped", bot_id=self.bot_id)

    async def handle_webhook(self, request) -> Any:
        raise NotImplementedError("wecom_bot uses WebSocket long connection, not webhook")

    # ---- main loop ----

    async def _run_loop(self) -> None:
        """Connect → authenticate → receive; reconnect with exponential backoff."""
        delay = _WS_RECONNECT_BASE_DELAY
        while not self._stop_event.is_set():
            # 每次重连都清空心跳状态：否则上一连接心跳丢失留下的
            # _ping_pending=True / _missed_pong=2 会让新连接第一次 tick
            # 就误判「心跳丢失」→ 秒断 → 无限抖动。
            self._reset_ping_state()
            try:
                self.state = TransportState.CONNECTING
                # proxy=None: websockets discovers macOS system proxies by
                # default and hard-fails on SOCKS without python-socks.
                # ping_interval=None: 关掉 websockets 协议层 keepalive（默认 20s）。
                # 企微服务器不按 RFC 回 pong（发回带 MASK 的非法帧），协议 ping 会把
                # 连接判定为 protocol error 每 ~40s 杀掉；keepalive 由下面的 JSON 心跳负责。
                async with websockets.connect(
                    self.ws_url,
                    open_timeout=_OPEN_TIMEOUT,
                    proxy=None,
                    ping_interval=None,
                ) as ws:
                    self._ws = ws
                    self.state = TransportState.CONNECTED
                    await self._subscribe(ws)
                    delay = _WS_RECONNECT_BASE_DELAY  # connected — reset backoff
                    logger.info("wecom_bot_ws_connected", bot_id=self.bot_id)
                    ping_task = asyncio.create_task(self._ping_loop(ws))
                    try:
                        async for raw in ws:
                            if self._stop_event.is_set():
                                break
                            await self._handle_frame(raw)
                    finally:
                        self._ws = None
                        ping_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await ping_task
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.state = TransportState.ERROR
                logger.warning(
                    "wecom_bot_ws_connection_error",
                    bot_id=self.bot_id,
                    error=str(e),
                    reconnect_delay=delay,
                )
            self._reject_all_pending()
            if self._stop_event.is_set():
                break
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
                break
            except TimeoutError:
                delay = min(delay * 2, _WS_RECONNECT_MAX_DELAY)
        self._ws = None
        self.state = TransportState.DISCONNECTED

    def _reset_ping_state(self) -> None:
        self._ping_req_ids.clear()
        self._ping_pending = False
        self._missed_pong = 0

    async def _subscribe(self, ws) -> None:
        """Send the auth frame and wait for its ack."""
        req_id = _gen_req_id("aibot_subscribe")
        body: dict[str, Any] = {
            "bot_id": self.bot_id,
            "secret": self.secret,
            "scene": _SCENE,
            "plug_version": _PLUG_VERSION,
        }
        await ws.send(json.dumps(
            {"cmd": "aibot_subscribe", "headers": {"req_id": req_id}, "body": body}
        ))
        raw = await asyncio.wait_for(ws.recv(), timeout=_AUTH_TIMEOUT)
        try:
            ack = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            raise RuntimeError(f"invalid auth ack: {raw[:200]!r}") from None
        if ack.get("errcode") != 0:
            raise RuntimeError(
                f"wecom bot auth failed: {ack.get('errmsg') or ack.get('errcode')}"
            )
        logger.info("wecom_bot_authenticated", bot_id=self.bot_id)

    async def _ping_loop(self, ws) -> None:
        """Send a JSON ``ping`` every interval; force a reconnect if unacked."""
        while True:
            await asyncio.sleep(_WS_HEARTBEAT_INTERVAL)
            if self._stop_event.is_set():
                return
            if self._ping_pending:
                self._missed_pong += 1
                if self._missed_pong >= _MAX_MISSED_PONG:
                    logger.warning("wecom_bot_heartbeat_lost", bot_id=self.bot_id)
                    # 直接 abort 而不是 close()：close 要等对端 close 握手回包，
                    # 心跳已死时对端往往无响应，白等 10s 才重连。abort 立即断开。
                    with contextlib.suppress(Exception):
                        ws.transport.abort()
                    return
            req_id = _gen_req_id("ping")
            self._ping_req_ids.add(req_id)
            self._ping_pending = True
            try:
                await ws.send(json.dumps({"cmd": "ping", "headers": {"req_id": req_id}}))
            except Exception:
                return

    # ---- inbound ----

    async def _handle_frame(self, raw: str) -> None:
        try:
            frame = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            logger.warning("wecom_bot_invalid_frame", raw_preview=repr(raw[:120]))
            return
        if not isinstance(frame, dict):
            return

        headers = frame.get("headers") or {}
        req_id = headers.get("req_id") or ""

        # Ack for a heartbeat ping.
        if req_id in self._ping_req_ids:
            self._ping_req_ids.discard(req_id)
            self._ping_pending = False
            self._missed_pong = 0
            return

        # Ack for an outbound frame (reply / proactive send / upload step).
        pending = self._pending.get(req_id)
        if pending is not None and not pending.done():
            pending.set_result(frame)
            return

        cmd = frame.get("cmd")
        if cmd == "aibot_msg_callback":
            try:
                inbound = normalize_event(self.pipeline.channel.id, frame)
            except Exception:
                logger.exception("wecom_bot_event_normalize_failed")
                return
            if inbound is not None:
                self.pipeline.submit(inbound)
        elif cmd == "aibot_event_callback":
            event = (frame.get("body") or {}).get("event") or {}
            if event.get("eventtype") == "disconnected_event":
                # 被新连接踢下线：SDK 语义是主动停止重连避免双实例互踢。
                logger.warning(
                    "wecom_bot_kicked_by_server",
                    bot_id=self.bot_id,
                    reason=event.get("reason"),
                )
            # 其余事件（enter_chat 等）v1 忽略。

    # ---- outbound ----

    async def _send_and_wait(self, cmd: str, req_id: str, body: dict) -> dict:
        """Send a frame and await its ack (errcode 0) or raise."""
        ws = self._ws
        if ws is None:
            raise RuntimeError("wecom bot websocket not connected")
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[req_id] = future
        try:
            await ws.send(json.dumps(
                {"cmd": cmd, "headers": {"req_id": req_id}, "body": body}
            ))
            ack = await asyncio.wait_for(future, timeout=_ACK_TIMEOUT)
        finally:
            self._pending.pop(req_id, None)

        errcode = ack.get("errcode")
        if errcode not in (0, None):
            if errcode == _STREAM_EXPIRED_ERRCODE:
                raise StreamExpiredError(f"stream expired: {ack.get('errmsg')}")
            raise AckError(
                f"{cmd} failed errcode={errcode} errmsg={ack.get('errmsg')}"
            )
        return ack

    async def reply(self, req_id: str, body: dict) -> dict:
        """Send a passive reply echoing the callback's ``req_id``."""
        return await self._send_and_wait("aibot_respond_msg", req_id, body)

    async def send_message(self, chatid: str, body: dict) -> dict:
        """Actively push a message to a chat (single chat: ``chatid`` = userid)."""
        return await self._send_and_wait(
            "aibot_send_msg", _gen_req_id("aibot_send_msg"), {"chatid": chatid, **body}
        )

    async def upload_media(
        self, data: bytes, *, media_type: str, filename: str
    ) -> dict:
        """Upload a file over the WS in 512KB chunks (init → chunk×N → finish)."""
        total_size = len(data)
        total_chunks = (total_size + _CHUNK_SIZE - 1) // _CHUNK_SIZE
        if total_chunks > _MAX_CHUNKS:
            raise RuntimeError(
                f"file too large: {total_chunks} chunks exceeds max {_MAX_CHUNKS}"
            )

        init_ack = await self._send_and_wait(
            "aibot_upload_media_init",
            _gen_req_id("aibot_upload_media_init"),
            {
                "type": media_type,
                "filename": filename,
                "total_size": total_size,
                "total_chunks": total_chunks,
                "md5": hashlib.md5(data).hexdigest(),
            },
        )
        upload_id = (init_ack.get("body") or {}).get("upload_id")
        if not upload_id:
            raise RuntimeError(f"upload init failed, no upload_id: {init_ack}")

        for index in range(total_chunks):
            chunk = data[index * _CHUNK_SIZE : (index + 1) * _CHUNK_SIZE]
            await self._send_and_wait(
                "aibot_upload_media_chunk",
                _gen_req_id("aibot_upload_media_chunk"),
                {
                    "upload_id": upload_id,
                    "chunk_index": index,
                    "base64_data": base64.b64encode(chunk).decode(),
                },
            )

        finish_ack = await self._send_and_wait(
            "aibot_upload_media_finish",
            _gen_req_id("aibot_upload_media_finish"),
            {"upload_id": upload_id},
        )
        media_id = (finish_ack.get("body") or {}).get("media_id")
        if not media_id:
            raise RuntimeError(f"upload finish failed, no media_id: {finish_ack}")
        return {
            "media_id": media_id,
            "type": (finish_ack.get("body") or {}).get("type") or media_type,
        }

    def _reject_all_pending(self) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(RuntimeError("wecom bot websocket disconnected"))
        self._pending.clear()


async def verify_bot_credentials(bot_id: str, secret: str) -> bool:
    """One-shot auth check: connect, subscribe, and require errcode 0."""
    try:
        async with websockets.connect(
            _DEFAULT_WS_URL,
            open_timeout=_OPEN_TIMEOUT,
            proxy=None,
            ping_interval=None,
        ) as ws:
            req_id = _gen_req_id("aibot_subscribe")
            await ws.send(json.dumps({
                "cmd": "aibot_subscribe",
                "headers": {"req_id": req_id},
                "body": {
                    "bot_id": bot_id,
                    "secret": secret,
                    "scene": _SCENE,
                    "plug_version": _PLUG_VERSION,
                },
            }))
            raw = await asyncio.wait_for(ws.recv(), timeout=_AUTH_TIMEOUT)
            ack = json.loads(raw)
            return ack.get("errcode") == 0
    except Exception:
        logger.warning("wecom_bot_credential_check_failed", exc_info=True)
        return False
