"""Helpers for file download responses."""

from urllib.parse import quote


def attachment_disposition(filename: str) -> str:
    """Build a Content-Disposition header value that survives latin-1 encoding.

    HTTP headers must be latin-1 encodable, so non-ASCII filenames need an
    ASCII fallback plus an RFC 5987 ``filename*`` parameter.
    """
    ascii_name = filename.encode("ascii", "replace").decode("ascii")
    encoded_name = quote(filename)
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded_name}"
