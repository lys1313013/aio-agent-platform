"""Feishu WebSocket transport — asyncio implementation of Feishu's ws v2 protocol.

Protocol (mirrors the official lark-oapi SDK's ws.Client):
1. POST /callback/ws/endpoint with {"AppID", "AppSecret"} → wss URL + ClientConfig
2. Frames are protobuf ``pbbp2.Frame`` (vendored in ``lark_oapi.ws.pb``)
3. CONTROL frames carry ping/pong; DATA frames carry event payloads and must
   be ACKed by echoing the frame with payload ``{"code": 200}``
4. The client sends a ping frame every ``PingInterval`` seconds
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from urllib.parse import parse_qs, urlparse

import httpx
import structlog
import websockets
from lark_oapi.ws.pb.pbbp2_pb2 import Frame

from aio_agent_platform.channels.adapter import Transport, TransportState
from aio_agent_platform.channels.feishu.events import normalize_event
from aio_agent_platform.channels.pipeline import ChannelInboundPipeline

logger = structlog.get_logger()

_FEISHU_DOMAIN = "https://open.feishu.cn"

_FRAME_CONTROL = 0
_FRAME_DATA = 1

_HEADER_TYPE = "type"
_HEADER_MESSAGE_ID = "message_id"
_HEADER_SUM = "sum"
_HEADER_SEQ = "seq"
_HEADER_BIZ_RT = "biz_rt"

_FRAGMENT_TTL = 60.0


class FeishuWebSocketTransport(Transport):
    """WebSocket transport for Feishu long-connection mode."""

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        pipeline: ChannelInboundPipeline,
    ):
        self.app_id = app_id
        self.app_secret = app_secret
        self.pipeline = pipeline
        self.state = TransportState.DISCONNECTED
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        # Server-tunable timings, replaced by ClientConfig on every handshake.
        self._ping_interval = 120
        self._reconnect_interval = 5
        self._service_id = 0
        self._fragments: dict[str, dict] = {}

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop())
        logger.info("feishu_ws_transport_started", app_id=self.app_id)

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self.state = TransportState.DISCONNECTED
        logger.info("feishu_ws_transport_stopped", app_id=self.app_id)

    async def _run_loop(self) -> None:
        """Connect, receive frames, reconnect on failure."""
        first = True
        while not self._stop_event.is_set():
            if first:
                first = False
            else:
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=self._reconnect_interval
                    )
                    break  # stop requested
                except TimeoutError:
                    pass
            try:
                self.state = TransportState.CONNECTING
                ws_url = await self._get_ws_endpoint()
                query = parse_qs(urlparse(ws_url).query)
                self._service_id = int(query["service_id"][0])

                # proxy=None: websockets discovers macOS system proxies by
                # default and hard-fails on SOCKS without python-socks.
                async with websockets.connect(ws_url, proxy=None) as ws:
                    self.state = TransportState.CONNECTED
                    logger.info("feishu_ws_connected", app_id=self.app_id)
                    ping_task = asyncio.create_task(self._ping_loop(ws))
                    try:
                        async for raw in ws:
                            if self._stop_event.is_set():
                                break
                            await self._handle_frame(raw, ws)
                    finally:
                        ping_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await ping_task
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.state = TransportState.ERROR
                logger.warning(
                    "feishu_ws_connection_error",
                    app_id=self.app_id,
                    error=str(e),
                    reconnect_interval=self._reconnect_interval,
                )

        self.state = TransportState.DISCONNECTED

    async def _get_ws_endpoint(self) -> str:
        """Request a WebSocket endpoint URL from Feishu."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{_FEISHU_DOMAIN}/callback/ws/endpoint",
                json={"AppID": self.app_id, "AppSecret": self.app_secret},
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") != 0:
                raise RuntimeError(f"Failed to get WS endpoint: {data}")
            config = (data.get("data") or {}).get("ClientConfig")
            if config:
                self._apply_client_config(config)
            return data["data"]["URL"]

    def _apply_client_config(self, config: dict) -> None:
        if config.get("PingInterval"):
            self._ping_interval = config["PingInterval"]
        if config.get("ReconnectInterval"):
            self._reconnect_interval = config["ReconnectInterval"]

    async def _ping_loop(self, ws) -> None:
        while True:
            await asyncio.sleep(self._ping_interval)
            frame = Frame()
            header = frame.headers.add()
            header.key = _HEADER_TYPE
            header.value = "ping"
            frame.service = self._service_id
            frame.method = _FRAME_CONTROL
            frame.SeqID = 0
            frame.LogID = 0
            await ws.send(frame.SerializeToString())

    def _combine_fragment(self, msg_id: str, total: int, seq: int, payload: bytes) -> bytes | None:
        """Reassemble multi-part payloads. Returns None until all parts arrive."""
        now = time.monotonic()
        stale = [k for k, v in self._fragments.items() if now - v["ts"] > _FRAGMENT_TTL]
        for k in stale:
            del self._fragments[k]

        entry = self._fragments.setdefault(msg_id, {"ts": now, "parts": {}})
        entry["ts"] = now
        entry["parts"][seq] = payload
        if len(entry["parts"]) < total:
            return None
        del self._fragments[msg_id]
        return b"".join(entry["parts"][i] for i in range(total))

    async def _handle_frame(self, raw: bytes, ws) -> None:
        try:
            frame = Frame()
            frame.ParseFromString(raw)
        except Exception:
            logger.warning("feishu_ws_invalid_frame", raw_preview=repr(raw[:120]))
            return

        headers = {h.key: h.value for h in frame.headers}

        if frame.method == _FRAME_CONTROL:
            if headers.get(_HEADER_TYPE) == "pong" and frame.payload:
                try:
                    self._apply_client_config(json.loads(frame.payload))
                except (json.JSONDecodeError, TypeError):
                    pass
            return

        if frame.method != _FRAME_DATA or headers.get(_HEADER_TYPE) != "event":
            return

        payload = frame.payload
        total = int(headers.get(_HEADER_SUM) or 1)
        if total > 1:
            payload = self._combine_fragment(
                headers.get(_HEADER_MESSAGE_ID, ""),
                total,
                int(headers.get(_HEADER_SEQ) or 0),
                payload,
            )
            if payload is None:
                return

        code = 200
        start = time.monotonic()
        try:
            event = json.loads(payload)
            header = event.get("header", {})
            if header.get("event_type") == "im.message.receive_v1":
                inbound = normalize_event(
                    channel_id=self.pipeline.channel.id,
                    event_id=header.get("event_id", ""),
                    event=event,
                    bot_app_id=self.app_id,
                )
                if inbound is not None:
                    self.pipeline.submit(inbound)
        except Exception:
            logger.exception("feishu_ws_event_handle_failed")
            code = 500

        # ACK: echo the frame back with biz_rt and a status payload.
        biz_rt = frame.headers.add()
        biz_rt.key = _HEADER_BIZ_RT
        biz_rt.value = str(int((time.monotonic() - start) * 1000))
        frame.payload = json.dumps({"code": code}).encode()
        try:
            await ws.send(frame.SerializeToString())
        except Exception:
            logger.warning("feishu_ws_ack_failed")
