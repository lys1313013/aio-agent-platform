"""MinerU cloud API client (远程文档解析).

Flow (MinerU Open API v4):
1. POST /api/v4/file-urls/batch  -> batch_id + presigned OSS upload URLs
2. PUT  <presigned_url>          -> upload file bytes (no extra headers!)
3. GET  /api/v4/extract-results/batch/{batch_id}  -> poll until done/failed
4. GET  <full_zip_url>           -> result ZIP containing full.md
"""

from __future__ import annotations

import asyncio
import io
import zipfile

import httpx

from aio_agent_platform.core.config import settings


class MinerUParseError(Exception):
    """Raised when MinerU parsing fails (用户可读的错误信息)."""


def is_configured() -> bool:
    return bool(settings.mineru.api_token)


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.mineru.api_token}",
        "Content-Type": "application/json",
    }


async def _request_batch_upload(client: httpx.AsyncClient, filename: str) -> tuple[str, str]:
    """Request a batch upload slot. Returns (batch_id, presigned_upload_url)."""
    resp = await client.post(
        f"{settings.mineru.base_url}/api/v4/file-urls/batch",
        headers=_headers(),
        json={
            "enable_formula": True,
            "enable_table": True,
            "language": "ch",
            "files": [{"name": filename, "is_ocr": True}],
        },
    )
    if resp.status_code != 200:
        raise MinerUParseError(f"MinerU 创建解析任务失败(HTTP {resp.status_code})")
    body = resp.json()
    if body.get("code") != 0:
        raise MinerUParseError(f"MinerU 创建解析任务失败:{body.get('msg', '未知错误')}")
    data = body["data"]
    return data["batch_id"], data["file_urls"][0]


async def _upload_file(client: httpx.AsyncClient, upload_url: str, data: bytes) -> None:
    # Presigned OSS URL — must NOT send Authorization/Content-Type headers,
    # otherwise the signature check fails (SignatureDoesNotMatch).
    resp = await client.put(upload_url, content=data, headers={})
    if resp.status_code != 200:
        raise MinerUParseError(f"MinerU 文件上传失败(HTTP {resp.status_code})")


async def _poll_result(client: httpx.AsyncClient, batch_id: str) -> str:
    """Poll until parsing completes. Returns the full_zip_url."""
    url = f"{settings.mineru.base_url}/api/v4/extract-results/batch/{batch_id}"
    deadline = settings.mineru.timeout_seconds
    elapsed = 0.0
    while elapsed < deadline:
        resp = await client.get(url, headers=_headers())
        if resp.status_code != 200:
            raise MinerUParseError(f"MinerU 查询解析结果失败(HTTP {resp.status_code})")
        body = resp.json()
        if body.get("code") != 0:
            raise MinerUParseError(f"MinerU 查询解析结果失败:{body.get('msg', '未知错误')}")
        results = body["data"].get("extract_result") or []
        if results:
            result = results[0]
            state = result.get("state")
            if state == "done":
                zip_url = result.get("full_zip_url")
                if not zip_url:
                    raise MinerUParseError("MinerU 未返回解析结果文件")
                return zip_url
            if state == "failed":
                raise MinerUParseError(f"MinerU 解析失败:{result.get('err_msg') or '未知错误'}")
        await asyncio.sleep(settings.mineru.poll_interval_seconds)
        elapsed += settings.mineru.poll_interval_seconds
    raise MinerUParseError(f"MinerU 解析超时(超过 {settings.mineru.timeout_seconds} 秒)")


def _extract_markdown(zip_bytes: bytes) -> str:
    try:
        archive = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as e:
        raise MinerUParseError("MinerU 返回的结果文件损坏") from e
    md_names = [n for n in archive.namelist() if n.endswith(".md")]
    if not md_names:
        raise MinerUParseError("MinerU 结果中未找到 Markdown 内容")
    # Prefer the full document markdown (e.g. "full.md")
    name = next((n for n in md_names if n.endswith("full.md")), md_names[0])
    return archive.read(name).decode("utf-8", errors="replace")


async def parse_document(filename: str, data: bytes) -> str:
    """Parse a document via MinerU cloud and return Markdown text."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=10.0)) as client:
        batch_id, upload_url = await _request_batch_upload(client, filename)
        await _upload_file(client, upload_url, data)
        zip_url = await _poll_result(client, batch_id)
        zip_resp = await client.get(zip_url)
        if zip_resp.status_code != 200:
            raise MinerUParseError(f"MinerU 结果下载失败(HTTP {zip_resp.status_code})")
        text = _extract_markdown(zip_resp.content)
    if not text.strip():
        raise MinerUParseError("MinerU 解析结果为空")
    return text
