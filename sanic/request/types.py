from __future__ import annotations

from asyncio import BaseProtocol
from collections import defaultdict
from contextvars import ContextVar
from inspect import isawaitable
from types import SimpleNamespace
from typing import (
    TYPE_CHECKING,
    Any,
    Generic,
    cast,
)

from sanic_routing.route import Route
from typing_extensions import TypeVar

from sanic.http.constants import HTTP  # type: ignore
from sanic.http.stream import Stream
from sanic.models.asgi import ASGIScope
from sanic.models.http_types import Credentials


if TYPE_CHECKING:
    from sanic.app import Sanic
    from sanic.config import Config
    from sanic.server import ConnInfo

import uuid

from urllib.parse import parse_qs, parse_qsl, urlunparse

from httptools import parse_url
from httptools.parser.errors import HttpParserInvalidURLError

from sanic.compat import CancelledErrors, Header
from sanic.constants import (
    CACHEABLE_HTTP_METHODS,
    DEFAULT_HTTP_CONTENT_TYPE,
    IDEMPOTENT_HTTP_METHODS,
    SAFE_HTTP_METHODS,
)
from sanic.cookies.request import CookieRequestParameters, parse_cookie
from sanic.exceptions import BadRequest, BadURL, ServerError, URITooLong
from sanic.headers import (
    AcceptList,
    Options,
    parse_accept,
    parse_content_header,
    parse_credentials,
    parse_forwarded,
    parse_host,
    parse_xforwarded,
)
from sanic.http import Stage
from sanic.log import error_logger
from sanic.models.protocol_types import TransportProtocol
from sanic.response import BaseHTTPResponse, HTTPResponse

from .form import parse_multipart_form
from .parameters import RequestParameters


try:
    from ujson import loads as json_loads  # type: ignore
except ImportError:
    from json import loads as json_loads  # type: ignore

if TYPE_CHECKING:
    # The default argument of TypeVar is proposed to be added in Python 3.13
    # by PEP 696 (https://www.python.org/dev/peps/pep-0696/).
    # Therefore, we use typing_extensions.TypeVar for compatibility.
    # For more information, see:
    # https://discuss.python.org/t/pep-696-type-defaults-for-typevarlikes
    sanic_type = TypeVar(
        "sanic_type", bound=Sanic, default=Sanic[Config, SimpleNamespace]
    )
    ctx_type = TypeVar(
        "ctx_type", bound=SimpleNamespace, default=SimpleNamespace
    )
else:
    sanic_type = TypeVar("sanic_type")
    ctx_type = TypeVar("ctx_type")


class Request(Generic[sanic_type, ctx_type]):
    """项目内部接口说明。"""

    _current: ContextVar[Request] = ContextVar("request")
    _loads = json_loads

    __slots__ = (
        "__weakref__",
        "_cookies",
        "_ctx",
        "_id",
        "_ip",
        "_parsed_url",
        "_port",
        "_protocol",
        "_remote_addr",
        "_request_middleware_started",
        "_response_middleware_started",
        "_scheme",
        "_socket",
        "_stream_id",
        "_match_info",
        "_name",
        "app",
        "body",
        "conn_info",
        "head",
        "headers",
        "method",
        "parsed_accept",
        "parsed_args",
        "parsed_cookies",
        "parsed_credentials",
        "parsed_files",
        "parsed_form",
        "parsed_forwarded",
        "parsed_json",
        "parsed_not_grouped_args",
        "parsed_token",
        "raw_url",
        "responded",
        "route",
        "stream",
        "transport",
        "version",
    )

    def __init__(
        self,
        url_bytes: bytes,
        headers: Header,
        version: str,
        method: str,
        transport: TransportProtocol,
        app: sanic_type,
        head: bytes = b"",
        stream_id: int = 0,
    ):
        self.raw_url = url_bytes
        # httptools.parse_url stores URL component offsets and lengths as
        # uint16, so a longer URL would silently wrap and truncate the path
        # or query string. Reject it instead of routing on a corrupted path.
        if len(url_bytes) > 65535:
            raise URITooLong("URL exceeds the maximum size of 65535 bytes")
        try:
            self._parsed_url = parse_url(url_bytes)
        except HttpParserInvalidURLError:
            url = url_bytes.decode(errors="backslashreplace")
            raise BadURL(f"Bad URL: {url}")
        self._id: uuid.UUID | str | int | None = None
        self._name: str | None = None
        self._stream_id = stream_id
        self.app = app

        self.headers = Header(headers)
        self.version = version
        self.method = method
        self.transport = transport
        self.head = head

        # Init but do not inhale
        self.body = b""
        self.conn_info: ConnInfo | None = None
        self._ctx: ctx_type | None = None
        self.parsed_accept: AcceptList | None = None
        self.parsed_args: defaultdict[
            tuple[bool, bool, str, str], RequestParameters
        ] = defaultdict(RequestParameters)
        self.parsed_cookies: RequestParameters | None = None
        self.parsed_credentials: Credentials | None = None
        self.parsed_files: RequestParameters | None = None
        self.parsed_form: RequestParameters | None = None
        self.parsed_forwarded: Options | None = None
        self.parsed_json = None
        self.parsed_not_grouped_args: defaultdict[
            tuple[bool, bool, str, str], list[tuple[str, str]]
        ] = defaultdict(list)
        self.parsed_token: str | None = None
        self._request_middleware_started = False
        self._response_middleware_started = False
        self.responded: bool = False
        self.route: Route | None = None
        self.stream: Stream | None = None
        self._match_info: dict[str, Any] = {}
        self._protocol: BaseProtocol | None = None

    def __repr__(self):
        class_name = self.__class__.__name__
        return f"<{class_name}: {self.method} {self.path}>"

    @staticmethod
    def make_context() -> ctx_type:
        """项目内部接口说明。"""
        return cast(ctx_type, SimpleNamespace())

    @classmethod
    def get_current(cls) -> Request:
        """项目内部接口说明。"""
        request = cls._current.get(None)
        if not request:
            raise ServerError("No current request")
        return request

    @classmethod
    def generate_id(*_) -> uuid.UUID | str | int:
        """项目内部接口说明。"""
        return uuid.uuid4()

    @property
    def ctx(self) -> ctx_type:
        """项目内部接口说明。"""
        if not self._ctx:
            self._ctx = self.make_context()
        return self._ctx

    @property
    def stream_id(self) -> int:
        """项目内部接口说明。"""
        if self.protocol.version is not HTTP.VERSION_3:
            raise ServerError(
                "Stream ID is only a property of a HTTP/3 request"
            )
        return self._stream_id

    def reset_response(self) -> None:
        """项目内部接口说明。"""
        try:
            if (
                self.stream is not None
                and self.stream.stage is not Stage.HANDLER
            ):
                raise ServerError(
                    "Cannot reset response because previous response was sent."
                )
            self.stream.response.stream = None  # type: ignore
            self.stream.response = None  # type: ignore
            self.responded = False
        except AttributeError:
            pass

    async def respond(
        self,
        response: BaseHTTPResponse | None = None,
        *,
        status: int = 200,
        headers: Header | dict[str, str] | None = None,
        content_type: str | None = None,
    ):
        """项目内部接口说明。"""
        try:
            if self.stream is not None and self.stream.response:
                raise ServerError("Second respond call is not allowed.")
        except AttributeError:
            pass
        # This logic of determining which response to use is subject to change
        if response is None:
            response = HTTPResponse(
                status=status,
                headers=headers,
                content_type=content_type,
            )

        # Connect the response
        if isinstance(response, BaseHTTPResponse) and self.stream:
            response = self.stream.respond(response)

            if isawaitable(response):
                response = await response  # type: ignore
        # Run response middleware
        try:
            middleware = (
                self.route and self.route.extra.response_middleware
            ) or self.app.response_middleware
            if middleware and not self._response_middleware_started:
                self._response_middleware_started = True
                response = await self.app._run_response_middleware(
                    self, response, middleware
                )
        except CancelledErrors:
            raise
        except Exception:
            error_logger.exception(
                "Exception occurred in one of response middleware handlers"
            )
        self.responded = True
        return response

    async def receive_body(self):
        """项目内部接口说明。"""
        if not self.body:
            self.body = b"".join([data async for data in self.stream])

    @property
    def name(self) -> str | None:
        """项目内部接口说明。"""
        if self._name:
            return self._name
        elif self.route:
            return self.route.name
        return None

    @property
    def endpoint(self) -> str | None:
        """项目内部接口说明。"""
        return self.name

    @property
    def uri_template(self) -> str | None:
        """项目内部接口说明。"""
        if self.route:
            return f"/{self.route.path}"
        return None

    @property
    def protocol(self) -> TransportProtocol:
        """项目内部接口说明。"""
        if not self._protocol:
            self._protocol = self.transport.get_protocol()
        return self._protocol  # type: ignore

    @property
    def raw_headers(self) -> bytes:
        """项目内部接口说明。"""
        _, headers = self.head.split(b"\r\n", 1)
        return bytes(headers)

    @property
    def request_line(self) -> bytes:
        """项目内部接口说明。"""
        reqline, _ = self.head.split(b"\r\n", 1)
        return bytes(reqline)

    @property
    def id(self) -> uuid.UUID | str | int | None:
        """项目内部接口说明。"""
        if not self._id:
            self._id = self.headers.getone(
                self.app.config.REQUEST_ID_HEADER,
                self.__class__.generate_id(self),  # type: ignore
            )

            # Try casting to a UUID or an integer
            if isinstance(self._id, str):
                try:
                    self._id = uuid.UUID(self._id)
                except ValueError:
                    try:
                        self._id = int(self._id)  # type: ignore
                    except ValueError:
                        ...

        return self._id  # type: ignore

    @property
    def json(self) -> Any:
        """项目内部接口说明。"""
        if self.parsed_json is None:
            self.load_json()

        return self.parsed_json

    def load_json(self, loads=None) -> Any:
        """项目内部接口说明。"""
        try:
            if not loads:
                loads = self.__class__._loads

            self.parsed_json = loads(self.body)
        except Exception:
            if not self.body:
                return None
            raise BadRequest("Failed when parsing body as json")

        return self.parsed_json

    @property
    def accept(self) -> AcceptList:
        """项目内部接口说明。"""
        if self.parsed_accept is None:
            self.parsed_accept = parse_accept(self.headers.get("accept"))
        return self.parsed_accept

    @property
    def token(self) -> str | None:
        """项目内部接口说明。"""
        if self.parsed_token is None:
            prefixes = ("Bearer", "Token")
            _, token = parse_credentials(
                self.headers.getone("authorization", None), prefixes
            )
            self.parsed_token = token
        return self.parsed_token

    @property
    def credentials(self) -> Credentials | None:
        """项目内部接口说明。"""
        if self.parsed_credentials is None:
            try:
                prefix, credentials = parse_credentials(
                    self.headers.getone("authorization", None)
                )
                if credentials:
                    self.parsed_credentials = Credentials(
                        auth_type=prefix, token=credentials
                    )
            except ValueError:
                pass
        return self.parsed_credentials

    def get_form(
        self, keep_blank_values: bool = False
    ) -> RequestParameters | None:
        """项目内部接口说明。"""
        self.parsed_form = RequestParameters()
        self.parsed_files = RequestParameters()
        content_type = self.headers.getone(
            "content-type", DEFAULT_HTTP_CONTENT_TYPE
        )
        content_type, parameters = parse_content_header(content_type)
        try:
            if content_type == "application/x-www-form-urlencoded":
                self.parsed_form = RequestParameters(
                    parse_qs(
                        self.body.decode("utf-8"),
                        keep_blank_values=keep_blank_values,
                    )
                )
            elif content_type == "multipart/form-data":
                # TODO: Stream this instead of reading to/from memory
                boundary = parameters["boundary"].encode(  # type: ignore
                    "utf-8"
                )  # type: ignore
                self.parsed_form, self.parsed_files = parse_multipart_form(
                    self.body, boundary
                )
        except Exception:
            error_logger.exception("Failed when parsing form")

        return self.parsed_form

    @property
    def form(self) -> RequestParameters | None:
        """项目内部接口说明。"""
        if self.parsed_form is None:
            self.get_form()

        return self.parsed_form

    @property
    def files(self) -> RequestParameters | None:
        """项目内部接口说明。"""
        if self.parsed_files is None:
            self.form  # compute form to get files

        return self.parsed_files

    def get_args(
        self,
        keep_blank_values: bool = False,
        strict_parsing: bool = False,
        encoding: str = "utf-8",
        errors: str = "replace",
    ) -> RequestParameters:
        """项目内部接口说明。"""
        if (
            keep_blank_values,
            strict_parsing,
            encoding,
            errors,
        ) not in self.parsed_args:
            if self.query_string:
                self.parsed_args[
                    (keep_blank_values, strict_parsing, encoding, errors)
                ] = RequestParameters(
                    parse_qs(
                        qs=self.query_string,
                        keep_blank_values=keep_blank_values,
                        strict_parsing=strict_parsing,
                        encoding=encoding,
                        errors=errors,
                    )
                )

        return self.parsed_args[
            (keep_blank_values, strict_parsing, encoding, errors)
        ]

    args = property(get_args)
    """Convenience property to access `Request.get_args` with default values.
    """

    def get_query_args(
        self,
        keep_blank_values: bool = False,
        strict_parsing: bool = False,
        encoding: str = "utf-8",
        errors: str = "replace",
    ) -> list:
        """项目内部接口说明。"""
        if (
            keep_blank_values,
            strict_parsing,
            encoding,
            errors,
        ) not in self.parsed_not_grouped_args:
            if self.query_string:
                self.parsed_not_grouped_args[
                    (keep_blank_values, strict_parsing, encoding, errors)
                ] = parse_qsl(
                    qs=self.query_string,
                    keep_blank_values=keep_blank_values,
                    strict_parsing=strict_parsing,
                    encoding=encoding,
                    errors=errors,
                )
        return self.parsed_not_grouped_args[
            (keep_blank_values, strict_parsing, encoding, errors)
        ]

    query_args = property(get_query_args)
    """Convenience property to access `Request.get_query_args` with default values.
    """  # noqa: E501

    def get_cookies(self) -> RequestParameters:
        cookie = self.headers.getone("cookie", "")
        self.parsed_cookies = CookieRequestParameters(parse_cookie(cookie))
        return self.parsed_cookies

    @property
    def cookies(self) -> RequestParameters:
        """项目内部接口说明。"""

        if self.parsed_cookies is None:
            self.get_cookies()
        return cast(CookieRequestParameters, self.parsed_cookies)

    @property
    def content_type(self) -> str:
        """项目内部接口说明。"""
        return self.headers.getone("content-type", DEFAULT_HTTP_CONTENT_TYPE)

    @property
    def match_info(self) -> dict[str, Any]:
        """项目内部接口说明。"""
        return self._match_info

    @match_info.setter
    def match_info(self, value):
        self._match_info = value

    @property
    def ip(self) -> str:
        """项目内部接口说明。"""
        return self.conn_info.client_ip if self.conn_info else ""

    @property
    def port(self) -> int:
        """项目内部接口说明。"""
        return self.conn_info.client_port if self.conn_info else 0

    @property
    def socket(self) -> tuple[str, int] | tuple[None, None]:
        """项目内部接口说明。"""
        return (
            self.conn_info.peername
            if self.conn_info and self.conn_info.peername
            else (None, None)
        )

    @property
    def path(self) -> str:
        """项目内部接口说明。"""
        return self._parsed_url.path.decode("utf-8")

    @property
    def network_paths(self) -> list[Any] | None:
        """项目内部接口说明。"""
        if self.conn_info is None:
            return None
        return self.conn_info.network_paths

    # Proxy properties (using SERVER_NAME/forwarded/request/transport info)

    @property
    def forwarded(self) -> Options:
        """项目内部接口说明。"""
        if self.parsed_forwarded is None:
            self.parsed_forwarded = (
                parse_forwarded(self.headers, self.app.config)
                or parse_xforwarded(self.headers, self.app.config)
                or {}
            )
        return self.parsed_forwarded

    @property
    def remote_addr(self) -> str:
        """项目内部接口说明。"""
        if not hasattr(self, "_remote_addr"):
            self._remote_addr = str(self.forwarded.get("for", ""))
        return self._remote_addr

    @property
    def client_ip(self) -> str:
        """项目内部接口说明。"""
        return self.remote_addr or self.ip

    @property
    def scheme(self) -> str:
        """项目内部接口说明。"""
        if not hasattr(self, "_scheme"):
            if (
                self.app.websocket_enabled
                and self.headers.upgrade.lower() == "websocket"
            ):
                scheme = "ws"
            else:
                scheme = "http"
            proto = None
            sp = self.app.config.get("SERVER_NAME", "").split("://", 1)
            if len(sp) == 2:
                proto = sp[0]
            elif "proto" in self.forwarded:
                proto = str(self.forwarded["proto"])
            if proto:
                # Give ws/wss if websocket, otherwise keep the same
                scheme = proto.replace("http", scheme)
            elif self.conn_info and self.conn_info.ssl:
                scheme += "s"
            self._scheme = scheme

        return self._scheme

    @property
    def host(self) -> str:
        """项目内部接口说明。"""
        server_name = self.app.config.get("SERVER_NAME")
        if server_name:
            return server_name.split("//", 1)[-1].split("/", 1)[0]
        return str(
            self.forwarded.get("host") or self.headers.getone("host", "")
        )

    @property
    def server_name(self) -> str:
        """项目内部接口说明。"""
        return parse_host(self.host)[0] or ""

    @property
    def server_port(self) -> int:
        """项目内部接口说明。"""
        port = self.forwarded.get("port") or parse_host(self.host)[1]
        return int(port or (80 if self.scheme in ("http", "ws") else 443))

    @property
    def server_path(self) -> str:
        """项目内部接口说明。"""
        return str(self.forwarded.get("path") or self.path)

    @property
    def query_string(self) -> str:
        """项目内部接口说明。"""
        if self._parsed_url.query:
            return self._parsed_url.query.decode("utf-8")
        else:
            return ""

    @property
    def url(self) -> str:
        """项目内部接口说明。"""
        return urlunparse(
            (self.scheme, self.host, self.path, None, self.query_string, None)
        )

    def url_for(self, view_name: str, **kwargs) -> str:
        """项目内部接口说明。"""
        # Full URL SERVER_NAME can only be handled in app.url_for
        try:
            sp = self.app.config.get("SERVER_NAME", "").split("://", 1)
            if len(sp) == 2:
                return self.app.url_for(view_name, _external=True, **kwargs)
        except AttributeError:
            pass

        scheme = self.scheme
        host = self.server_name
        port = self.server_port

        if (scheme.lower() in ("http", "ws") and port == 80) or (
            scheme.lower() in ("https", "wss") and port == 443
        ):
            netloc = host
        else:
            netloc = f"{host}:{port}"

        return self.app.url_for(
            view_name, _external=True, _scheme=scheme, _server=netloc, **kwargs
        )

    @property
    def scope(self) -> ASGIScope:
        """项目内部接口说明。"""
        if not self.app.asgi:
            raise NotImplementedError(
                "App isn't running in ASGI mode. "
                "Scope is only available for ASGI apps."
            )

        return self.transport.scope

    @property
    def is_safe(self) -> bool:
        """项目内部接口说明。"""
        return self.method in SAFE_HTTP_METHODS

    @property
    def is_idempotent(self) -> bool:
        """项目内部接口说明。"""
        return self.method in IDEMPOTENT_HTTP_METHODS

    @property
    def is_cacheable(self) -> bool:
        """项目内部接口说明。"""
        return self.method in CACHEABLE_HTTP_METHODS
