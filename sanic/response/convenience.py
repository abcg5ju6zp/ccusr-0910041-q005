from __future__ import annotations

from datetime import datetime, timezone
from email.utils import formatdate, parsedate_to_datetime
from mimetypes import guess_type
from os import path
from pathlib import PurePath
from time import time
from typing import Any, AnyStr, Callable
from urllib.parse import quote_plus

from sanic.compat import Header, open_async, stat_async
from sanic.constants import DEFAULT_HTTP_CONTENT_TYPE
from sanic.helpers import Default, _default
from sanic.log import logger
from sanic.models.protocol_types import HTMLProtocol, Range

from .types import HTTPResponse, JSONResponse, ResponseStream


def empty(
    status: int = 204, headers: dict[str, str] | None = None
) -> HTTPResponse:
    """项目内部接口说明。"""
    return HTTPResponse(body=b"", status=status, headers=headers)


def json(
    body: Any,
    status: int = 200,
    headers: dict[str, str] | None = None,
    content_type: str = "application/json",
    dumps: Callable[..., AnyStr] | None = None,
    **kwargs: Any,
) -> JSONResponse:
    """项目内部接口说明。"""
    return JSONResponse(
        body,
        status=status,
        headers=headers,
        content_type=content_type,
        dumps=dumps,
        **kwargs,
    )


def text(
    body: str,
    status: int = 200,
    headers: dict[str, str] | None = None,
    content_type: str = "text/plain; charset=utf-8",
) -> HTTPResponse:
    """项目内部接口说明。"""
    if not isinstance(body, str):
        raise TypeError(
            f"Bad body type. Expected str, got {type(body).__name__})"
        )

    return HTTPResponse(
        body, status=status, headers=headers, content_type=content_type
    )


def raw(
    body: AnyStr | None,
    status: int = 200,
    headers: dict[str, str] | None = None,
    content_type: str = DEFAULT_HTTP_CONTENT_TYPE,
) -> HTTPResponse:
    """项目内部接口说明。"""
    return HTTPResponse(
        body=body,
        status=status,
        headers=headers,
        content_type=content_type,
    )


def html(
    body: str | bytes | HTMLProtocol,
    status: int = 200,
    headers: dict[str, str] | None = None,
) -> HTTPResponse:
    """项目内部接口说明。"""
    if not isinstance(body, (str, bytes)):
        if hasattr(body, "__html__"):
            body = body.__html__()
        elif hasattr(body, "_repr_html_"):
            body = body._repr_html_()

    return HTTPResponse(
        body,
        status=status,
        headers=headers,
        content_type="text/html; charset=utf-8",
    )


async def validate_file(
    request_headers: Header, last_modified: datetime | float | int
) -> HTTPResponse | None:
    """项目内部接口说明。"""
    try:
        if_modified_since = request_headers.getone("If-Modified-Since")
    except KeyError:
        return None
    try:
        if_modified_since = parsedate_to_datetime(if_modified_since)
    except (TypeError, ValueError):
        logger.warning(
            "Ignorning invalid If-Modified-Since header received: '%s'",
            if_modified_since,
        )
        return None
    if not isinstance(last_modified, datetime):
        last_modified = datetime.fromtimestamp(
            float(last_modified), tz=timezone.utc
        ).replace(microsecond=0)

    if (
        last_modified.utcoffset() is None
        and if_modified_since.utcoffset() is not None
    ):
        logger.warning(
            "Cannot compare tz-aware and tz-naive datetimes. To avoid "
            "this conflict Sanic is converting last_modified to UTC."
        )
        last_modified.replace(tzinfo=timezone.utc)
    elif (
        last_modified.utcoffset() is not None
        and if_modified_since.utcoffset() is None
    ):
        logger.warning(
            "Cannot compare tz-aware and tz-naive datetimes. To avoid "
            "this conflict Sanic is converting if_modified_since to UTC."
        )
        if_modified_since.replace(tzinfo=timezone.utc)
    if last_modified.timestamp() <= if_modified_since.timestamp():
        return HTTPResponse(status=304)

    return None


async def file(
    location: str | PurePath,
    status: int = 200,
    request_headers: Header | None = None,
    validate_when_requested: bool = True,
    mime_type: str | None = None,
    headers: dict[str, str] | None = None,
    filename: str | None = None,
    last_modified: datetime | float | int | Default | None = _default,
    max_age: float | int | None = None,
    no_store: bool | None = None,
    _range: Range | None = None,
) -> HTTPResponse:
    """项目内部接口说明。"""

    if isinstance(last_modified, datetime):
        last_modified = last_modified.replace(microsecond=0).timestamp()
    elif isinstance(last_modified, Default):
        stat = await stat_async(location)
        last_modified = stat.st_mtime

    if (
        validate_when_requested
        and request_headers is not None
        and last_modified
    ):
        response = await validate_file(request_headers, last_modified)
        if response:
            return response

    headers = headers or {}
    if last_modified:
        headers.setdefault(
            "Last-Modified", formatdate(last_modified, usegmt=True)
        )

    if filename:
        headers.setdefault(
            "Content-Disposition", f'attachment; filename="{filename}"'
        )

    if no_store:
        cache_control = "no-store"
    elif max_age:
        cache_control = f"public, max-age={max_age}"
        headers.setdefault(
            "expires",
            formatdate(
                time() + max_age,
                usegmt=True,
            ),
        )
    else:
        cache_control = "no-cache"

    headers.setdefault("cache-control", cache_control)

    filename = filename or path.split(location)[-1]

    async with await open_async(location, mode="rb") as f:
        if _range:
            await f.seek(_range.start)
            out_stream = await f.read(_range.size)
            headers["Content-Range"] = (
                f"bytes {_range.start}-{_range.end}/{_range.total}"
            )
            status = 206
        else:
            out_stream = await f.read()

    content_type = mime_type or guess_content_type(
        filename, fallback="text/plain; charset=utf-8"
    )
    return HTTPResponse(
        body=out_stream,
        status=status,
        headers=headers,
        content_type=content_type,
    )


def redirect(
    to: str,
    headers: dict[str, str] | None = None,
    status: int = 302,
    content_type: str = "text/html; charset=utf-8",
) -> HTTPResponse:
    """项目内部接口说明。"""
    headers = headers or {}

    # URL Quote the URL before redirecting
    safe_to = quote_plus(to, safe=":/%#?&=@[]!$&'()*+,;")

    # According to RFC 7231, a relative URI is now permitted.
    headers["Location"] = safe_to

    return HTTPResponse(
        status=status, headers=headers, content_type=content_type
    )


async def file_stream(
    location: str | PurePath,
    status: int = 200,
    chunk_size: int = 4096,
    mime_type: str | None = None,
    headers: dict[str, str] | None = None,
    filename: str | None = None,
    _range: Range | None = None,
) -> ResponseStream:
    """项目内部接口说明。"""
    headers = headers or {}
    if filename:
        headers.setdefault(
            "Content-Disposition", f'attachment; filename="{filename}"'
        )
    filename = filename or path.split(location)[-1]
    mime_type = mime_type or guess_type(filename)[0] or "text/plain"
    if _range:
        start = _range.start
        end = _range.end
        total = _range.total

        headers["Content-Range"] = f"bytes {start}-{end}/{total}"
        status = 206

    async def _streaming_fn(response):
        async with await open_async(location, mode="rb") as f:
            if _range:
                await f.seek(_range.start)
                to_send = _range.size
                while to_send > 0:
                    content = await f.read(min((_range.size, chunk_size)))
                    if len(content) < 1:
                        break
                    to_send -= len(content)
                    await response.write(content)
            else:
                while True:
                    content = await f.read(chunk_size)
                    if len(content) < 1:
                        break
                    await response.write(content)

    return ResponseStream(
        streaming_fn=_streaming_fn,
        status=status,
        headers=headers,
        content_type=mime_type,
    )


def guess_content_type(
    file_path: str | PurePath,
    fallback: str = DEFAULT_HTTP_CONTENT_TYPE,
) -> str:
    """项目内部接口说明。"""
    mediatype = guess_type(file_path)[0]
    if mediatype is None:
        return fallback
    if mediatype.startswith("text/"):
        return f"{mediatype}; charset=utf-8"
    return mediatype
