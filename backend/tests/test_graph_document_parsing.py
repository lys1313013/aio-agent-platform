"""Unit tests for graph knowledge document parsing & MinerU client."""

import io
import zipfile

import pytest

from aio_agent_platform.graph_knowledge import mineru
from aio_agent_platform.graph_knowledge.parsing import (
    DocumentParseError,
    ScannedPDFError,
    extract_text,
)


# ---- parsing.extract_text ----


def test_extract_markdown():
    assert extract_text("a.md", "# 标题\n\n正文".encode()) == "# 标题\n\n正文"


def test_extract_text_gbk_fallback():
    assert extract_text("a.txt", "中文".encode("gbk")) == "中文"


def test_extract_html_to_markdown():
    text = extract_text("a.html", b"<h1>Hi</h1><p>para</p>")
    assert "Hi" in text and "para" in text


def test_extract_docx():
    import docx

    doc = docx.Document()
    doc.add_paragraph("第一段")
    buf = io.BytesIO()
    doc.save(buf)
    assert "第一段" in extract_text("a.docx", buf.getvalue())


def test_extract_scanned_pdf_raises_scanned_error():
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    with pytest.raises(ScannedPDFError):
        extract_text("a.pdf", buf.getvalue())


def test_extract_unsupported_extension():
    with pytest.raises(DocumentParseError, match="不支持的文件格式"):
        extract_text("a.exe", b"x")


def test_extract_doc_unsupported():
    with pytest.raises(DocumentParseError, match="不支持的文件格式"):
        extract_text("a.doc", b"x")


# ---- mineru._extract_markdown ----


def _make_zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


def test_mineru_extract_markdown_prefers_full_md():
    zip_bytes = _make_zip({"full.md": "# 全文", "images/x.jpg.md": "碎片"})
    assert mineru._extract_markdown(zip_bytes) == "# 全文"


def test_mineru_extract_markdown_any_md():
    zip_bytes = _make_zip({"doc/result.md": "内容"})
    assert mineru._extract_markdown(zip_bytes) == "内容"


def test_mineru_extract_markdown_no_md():
    with pytest.raises(mineru.MinerUParseError, match="未找到 Markdown"):
        mineru._extract_markdown(_make_zip({"layout.json": "{}"}))


def test_mineru_extract_markdown_bad_zip():
    with pytest.raises(mineru.MinerUParseError, match="损坏"):
        mineru._extract_markdown(b"not a zip")


# ---- mineru.parse_document (mocked HTTP) ----


class _FakeResponse:
    def __init__(self, status_code: int = 200, json_body: dict | None = None, content: bytes = b""):
        self.status_code = status_code
        self._json = json_body or {}
        self.content = content

    def json(self):
        return self._json


class _FakeClient:
    """Records requests and serves scripted responses."""

    def __init__(self, responses: dict[tuple[str, str], list]):
        self.responses = {k: list(v) for k, v in responses.items()}
        self.requests: list[tuple[str, str]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def _next(self, method: str, url: str):
        self.requests.append((method, url))
        key = next(
            (k for k in self.responses if k[0] == method and url.startswith(k[1])), None
        )
        assert key is not None, f"unexpected request: {method} {url}"
        return self.responses[key].pop(0)

    async def post(self, url, **kwargs):
        return self._next("POST", url)

    async def put(self, url, **kwargs):
        return self._next("PUT", url)

    async def get(self, url, **kwargs):
        return self._next("GET", url)


def _patch_settings(monkeypatch, tmp_path):
    monkeypatch.setattr(mineru.settings.mineru, "api_token", "test-token")
    monkeypatch.setattr(mineru.settings.mineru, "base_url", "https://mineru.test")
    monkeypatch.setattr(mineru.settings.mineru, "poll_interval_seconds", 0.01)
    monkeypatch.setattr(mineru.settings.mineru, "timeout_seconds", 30)


async def test_mineru_parse_document_happy_path(monkeypatch, tmp_path):
    _patch_settings(monkeypatch, tmp_path)
    zip_bytes = _make_zip({"full.md": "# OCR 结果"})
    fake = _FakeClient(
        {
            ("POST", "https://mineru.test/api/v4/file-urls/batch"): [
                _FakeResponse(
                    json_body={
                        "code": 0,
                        "data": {
                            "batch_id": "b1",
                            "file_urls": ["https://oss.test/upload/b1"],
                        },
                    }
                )
            ],
            ("PUT", "https://oss.test/upload/b1"): [_FakeResponse()],
            ("GET", "https://mineru.test/api/v4/extract-results/batch/b1"): [
                _FakeResponse(
                    json_body={"code": 0, "data": {"extract_result": [{"state": "pending"}]}}
                ),
                _FakeResponse(
                    json_body={
                        "code": 0,
                        "data": {
                            "extract_result": [
                                {"state": "done", "full_zip_url": "https://oss.test/result.zip"}
                            ]
                        },
                    }
                ),
            ],
            ("GET", "https://oss.test/result.zip"): [_FakeResponse(content=zip_bytes)],
        }
    )
    monkeypatch.setattr(mineru.httpx, "AsyncClient", lambda **kw: fake)
    assert await mineru.parse_document("scan.pdf", b"%PDF...") == "# OCR 结果"


async def test_mineru_parse_document_failed_state(monkeypatch, tmp_path):
    _patch_settings(monkeypatch, tmp_path)
    fake = _FakeClient(
        {
            ("POST", "https://mineru.test/api/v4/file-urls/batch"): [
                _FakeResponse(
                    json_body={
                        "code": 0,
                        "data": {"batch_id": "b1", "file_urls": ["https://oss.test/u"]},
                    }
                )
            ],
            ("PUT", "https://oss.test/u"): [_FakeResponse()],
            ("GET", "https://mineru.test/api/v4/extract-results/batch/b1"): [
                _FakeResponse(
                    json_body={
                        "code": 0,
                        "data": {
                            "extract_result": [{"state": "failed", "err_msg": "文件损坏"}]
                        },
                    }
                )
            ],
        }
    )
    monkeypatch.setattr(mineru.httpx, "AsyncClient", lambda **kw: fake)
    with pytest.raises(mineru.MinerUParseError, match="文件损坏"):
        await mineru.parse_document("scan.pdf", b"%PDF...")


async def test_mineru_parse_document_auth_error(monkeypatch, tmp_path):
    _patch_settings(monkeypatch, tmp_path)
    fake = _FakeClient(
        {
            ("POST", "https://mineru.test/api/v4/file-urls/batch"): [
                _FakeResponse(json_body={"code": 401, "msg": "invalid token"})
            ],
        }
    )
    monkeypatch.setattr(mineru.httpx, "AsyncClient", lambda **kw: fake)
    with pytest.raises(mineru.MinerUParseError, match="invalid token"):
        await mineru.parse_document("scan.pdf", b"%PDF...")
