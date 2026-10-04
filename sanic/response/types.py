from __future__ import annotations

from collections.abc import Coroutine, Iterator
from datetime import datetime
from typing import TYPE_CHECKING, Any, AnyStr, Callable, TypeVar

from sanic.compat import Header
from sanic.cookies import CookieJar
from sanic.cookies.response import Cookie, SameSite
from sanic.exceptions import SanicException, ServerError
from sanic.helpers import Default, _default, has_message_body, json_dumps
from sanic.http import Http


if TYPE_CHECKING:
    from sanic.asgi import ASGIApp
    from sanic.http.http3 import HTTPReceiver
    from sanic.request import Request
else:
    Request = TypeVar("Request")


HEADER_TRANSLATION_TABLE = str.maketrans("", "", "\r\n\x00")


class BaseHTTPResponse:
    """项目内部接口说明。"""

    __slots__ = (
        "asgi",
        "body",
        "content_type",
        "stream",
        "status",
        "headers",
        "_cookies",
    )

    _dumps = json_dumps

    def __init__(self):
        self.asgi: bool = False
        self.body: bytes | None = None
        self.content_type: str | None = None
        self.stream: Http | ASGIApp | HTTPReceiver | None = None
        self.status: int = None
        self.headers = Header({})
        self._cookies: CookieJar | None = None

    def __repr__(self):
        class_name = self.__class__.__name__
        return f"<{class_name}: {self.status} {self.content_type}>"

    def _encode_body(self, data: str | bytes | None):
        if data is None:
            return b""
        return data.encode() if hasattr(data, "encode") else data  # type: ignore

    @property
    def cookies(self) -> CookieJar:
        """项目内部接口说明。"""
        if self._cookies is None:
            self._cookies = CookieJar(self.headers)
        return self._cookies

    @property
    def processed_headers(self) -> Iterator[tuple[bytes, bytes]]:
        """项目内部接口说明。"""
        if has_message_body(self.status):
            self.headers.setdefault("content-type", self.content_type)
        # Encode headers into bytes
        return (
            (
                self._sanitize_header_value(name).encode("ascii"),
                self._sanitize_header_value(f"{value}").encode(
                    errors="surrogateescape"
                ),
            )
            for name, value in self.headers.items()
        )

    async def send(
        self,
        data: AnyStr | None = None,
        end_stream: bool | None = None,
    ) -> None:
        """项目内部接口说明。"""
        if data is None and end_stream is None:
            end_stream = True
        if self.stream is None:
            raise SanicException(
                "No stream is connected to the response object instance."
            )
        if self.stream.send is None:
            if end_stream and not data:
                return
            raise ServerError(
                "Response stream was ended, no more response data is "
                "allowed to be sent."
            )
        data = data.encode() if hasattr(data, "encode") else data or b""  # type: ignore
        await self.stream.send(
            data,  # type: ignore
            end_stream=end_stream or False,
        )

    def add_cookie(
        self,
        key: str,
        value: str,
        *,
        path: str = "/",
        domain: str | None = None,
        secure: bool = True,
        max_age: int | None = None,
        expires: datetime | None = None,
        httponly: bool = False,
        samesite: SameSite | None = "Lax",
        partitioned: bool = False,
        comment: str | None = None,
        host_prefix: bool = False,
        secure_prefix: bool = False,
    ) -> Cookie:
        """项目内部接口说明。"""
        return self.cookies.add_cookie(
            key=key,
            value=value,
            path=path,
            domain=domain,
            secure=secure,
            max_age=max_age,
            expires=expires,
            httponly=httponly,
            samesite=samesite,
            partitioned=partitioned,
            comment=comment,
            host_prefix=host_prefix,
            secure_prefix=secure_prefix,
        )

    def delete_cookie(
        self,
        key: str,
        *,
        path: str = "/",
        domain: str | None = None,
        host_prefix: bool = False,
        secure_prefix: bool = False,
    ) -> None:
        """项目内部接口说明。"""
        self.cookies.delete_cookie(
            key=key,
            path=path,
            domain=domain,
            host_prefix=host_prefix,
            secure_prefix=secure_prefix,
        )

    @staticmethod
    def _sanitize_header_value(value: str) -> str:
        return value.translate(HEADER_TRANSLATION_TABLE)


class HTTPResponse(BaseHTTPResponse):
    """项目内部接口说明。"""

    __slots__ = ()

    def __init__(
        self,
        body: Any = None,
        status: int = 200,
        headers: Header | dict[str, str] | None = None,
        content_type: str | None = None,
    ):
        super().__init__()

        self.content_type: str | None = content_type
        self.body = self._encode_body(body)
        self.status = status
        self.headers = Header(headers or {})
        self._cookies = None

    async def eof(self):
        """项目内部接口说明。"""
        await self.send("", True)

    async def __aenter__(self):
        return self.send

    async def __aexit__(self, *_):
        await self.eof()


class JSONResponse(HTTPResponse):
    """项目内部接口说明。"""

    __slots__ = (
        "_body",
        "_body_manually_set",
        "_initialized",
        "_raw_body",
        "_use_dumps",
        "_use_dumps_kwargs",
    )

    def __init__(
        self,
        body: Any = None,
        status: int = 200,
        headers: Header | dict[str, str] | None = None,
        content_type: str = "application/json",
        dumps: Callable[..., AnyStr] | None = None,
        **kwargs: Any,
    ):
        self._initialized = False
        self._body_manually_set = False

        self._use_dumps: Callable[..., str | bytes] = (
            dumps or BaseHTTPResponse._dumps
        )
        self._use_dumps_kwargs = kwargs

        self._raw_body = body

        super().__init__(
            self._encode_body(self._use_dumps(body, **self._use_dumps_kwargs)),
            headers=headers,
            status=status,
            content_type=content_type,
        )

        self._initialized = True

    def _check_body_not_manually_set(self):
        if self._body_manually_set:
            raise SanicException(
                "Cannot use raw_body after body has been manually set."
            )

    @property
    def raw_body(self) -> Any:
        """项目内部接口说明。"""
        self._check_body_not_manually_set()
        return self._raw_body

    @raw_body.setter
    def raw_body(self, value: Any):
        self._body_manually_set = False
        self._body = self._encode_body(
            self._use_dumps(value, **self._use_dumps_kwargs)
        )
        self._raw_body = value

    @property  # type: ignore
    def body(self) -> bytes | None:  # type: ignore
        """项目内部接口说明。"""
        return self._body

    @body.setter
    def body(self, value: bytes | None):
        self._body = value
        if not self._initialized:
            return
        self._body_manually_set = True

    def set_body(
        self,
        body: Any,
        dumps: Callable[..., AnyStr] | None = None,
        **dumps_kwargs: Any,
    ) -> None:
        """项目内部接口说明。"""
        self._body_manually_set = False
        self._raw_body = body

        use_dumps = dumps or self._use_dumps
        use_dumps_kwargs = dumps_kwargs if dumps else self._use_dumps_kwargs

        self._body = self._encode_body(use_dumps(body, **use_dumps_kwargs))

    def append(self, value: Any) -> None:
        """项目内部接口说明。"""

        self._check_body_not_manually_set()

        if not isinstance(self._raw_body, list):
            raise SanicException("Cannot append to a non-list object.")

        self._raw_body.append(value)
        self.raw_body = self._raw_body

    def extend(self, value: Any) -> None:
        """项目内部接口说明。"""

        self._check_body_not_manually_set()

        if not isinstance(self._raw_body, list):
            raise SanicException("Cannot extend a non-list object.")

        self._raw_body.extend(value)
        self.raw_body = self._raw_body

    def update(self, *args, **kwargs) -> None:
        """项目内部接口说明。"""

        self._check_body_not_manually_set()

        if not isinstance(self._raw_body, dict):
            raise SanicException("Cannot update a non-dict object.")

        self._raw_body.update(*args, **kwargs)
        self.raw_body = self._raw_body

    def pop(self, key: Any, default: Any = _default) -> Any:
        """项目内部接口说明。"""

        self._check_body_not_manually_set()

        if not isinstance(self._raw_body, (list, dict)):
            raise SanicException(
                "Cannot pop from a non-list and non-dict object."
            )

        if isinstance(default, Default):
            value = self._raw_body.pop(key)
        elif isinstance(self._raw_body, list):
            raise TypeError("pop doesn't accept a default argument for lists")
        else:
            value = self._raw_body.pop(key, default)

        self.raw_body = self._raw_body

        return value


class ResponseStream:
    """项目内部接口说明。"""

    __slots__ = (
        "_cookies",
        "content_type",
        "headers",
        "request",
        "response",
        "status",
        "streaming_fn",
    )

    def __init__(
        self,
        streaming_fn: Callable[
            [BaseHTTPResponse | ResponseStream],
            Coroutine[Any, Any, None],
        ],
        status: int = 200,
        headers: Header | dict[str, str] | None = None,
        content_type: str | None = None,
    ):
        if headers is None:
            headers = Header()
        elif not isinstance(headers, Header):
            headers = Header(headers)
        self.streaming_fn = streaming_fn
        self.status = status
        self.headers = headers or Header()
        self.content_type = content_type
        self.request: Request | None = None
        self._cookies: CookieJar | None = None

    async def write(self, message: str):
        await self.response.send(message)

    async def stream(self) -> HTTPResponse:
        if not self.request:
            raise ServerError("Attempted response to unknown request")
        self.response = await self.request.respond(
            headers=self.headers,
            status=self.status,
            content_type=self.content_type,
        )
        await self.streaming_fn(self)
        return self.response

    async def eof(self) -> None:
        await self.response.eof()

    @property
    def cookies(self) -> CookieJar:
        if self._cookies is None:
            self._cookies = CookieJar(self.headers)
        return self._cookies

    @property
    def processed_headers(self):
        return self.response.processed_headers

    @property
    def body(self):
        return self.response.body

    def __call__(self, request: Request) -> ResponseStream:
        self.request = request
        return self

    def __await__(self):
        return self.stream().__await__()
