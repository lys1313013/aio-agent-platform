"""Regression tests for Content-Disposition header construction.

Non-ASCII filenames used to crash Starlette with UnicodeEncodeError
(HTTP headers are latin-1 only), breaking file downloads. See
``aio_agent_platform.interface.headers.attachment_disposition``.
"""

from urllib.parse import unquote

from starlette.responses import Response, StreamingResponse

from aio_agent_platform.interface.headers import attachment_disposition


def _parse_filename_star(disposition: str) -> str:
    """Extract and decode the RFC 5987 filename* parameter."""
    for part in disposition.split(";"):
        part = part.strip()
        if part.startswith("filename*=UTF-8''"):
            return unquote(part[len("filename*=UTF-8''"):])
    raise AssertionError(f"filename* parameter missing in: {disposition}")


class TestAttachmentDisposition:
    def test_chinese_filename_roundtrip(self):
        disposition = attachment_disposition("悉达多_中文.txt")
        assert _parse_filename_star(disposition) == "悉达多_中文.txt"

    def test_header_is_latin1_encodable(self):
        for filename in ("悉达多_中文.txt", "héllo wörld.zip", "データ.csv"):
            disposition = attachment_disposition(filename)
            disposition.encode("latin-1")  # raises UnicodeEncodeError on regression

    def test_ascii_filename_kept_as_fallback(self):
        disposition = attachment_disposition("report final.txt")
        assert 'filename="report final.txt"' in disposition

    def test_non_ascii_fallback_is_ascii(self):
        disposition = attachment_disposition("悉达多_中文.txt")
        fallback = disposition.split(";")[1].strip()
        assert fallback == 'filename="???_??.txt"'

    def test_starlette_response_accepts_chinese_filename(self):
        """End-to-end: building the actual Response must not raise."""
        headers = {"Content-Disposition": attachment_disposition("悉达多_中文.txt")}
        response = Response(content=b"data", media_type="application/octet-stream", headers=headers)
        raw = dict(response.raw_headers)[b"content-disposition"].decode("latin-1")
        assert _parse_filename_star(raw) == "悉达多_中文.txt"

    def test_starlette_streaming_response_accepts_chinese_filename(self):
        headers = {"Content-Disposition": attachment_disposition("技能文件.py")}
        response = StreamingResponse(
            iter([b"data"]), media_type="application/octet-stream", headers=headers
        )
        raw = dict(response.raw_headers)[b"content-disposition"].decode("latin-1")
        assert _parse_filename_star(raw) == "技能文件.py"
