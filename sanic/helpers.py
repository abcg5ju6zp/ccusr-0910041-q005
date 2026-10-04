"""项目内部接口说明。"""

import sys

from functools import partial
from importlib import import_module
from inspect import ismodule


try:
    from ujson import dumps as ujson_dumps

    json_dumps = partial(ujson_dumps, escape_forward_slashes=False)
except ImportError:
    # This is done in order to ensure that the JSON response is
    # kept consistent across both ujson and inbuilt json usage.
    from json import dumps

    json_dumps = partial(dumps, separators=(",", ":"))

STATUS_CODES: dict[int, bytes] = {
    100: b"Continue",
    101: b"Switching Protocols",
    102: b"Processing",
    103: b"Early Hints",
    200: b"OK",
    201: b"Created",
    202: b"Accepted",
    203: b"Non-Authoritative Information",
    204: b"No Content",
    205: b"Reset Content",
    206: b"Partial Content",
    207: b"Multi-Status",
    208: b"Already Reported",
    226: b"IM Used",
    300: b"Multiple Choices",
    301: b"Moved Permanently",
    302: b"Found",
    303: b"See Other",
    304: b"Not Modified",
    305: b"Use Proxy",
    307: b"Temporary Redirect",
    308: b"Permanent Redirect",
    400: b"Bad Request",
    401: b"Unauthorized",
    402: b"Payment Required",
    403: b"Forbidden",
    404: b"Not Found",
    405: b"Method Not Allowed",
    406: b"Not Acceptable",
    407: b"Proxy Authentication Required",
    408: b"Request Timeout",
    409: b"Conflict",
    410: b"Gone",
    411: b"Length Required",
    412: b"Precondition Failed",
    413: b"Request Entity Too Large",
    414: b"Request-URI Too Long",
    415: b"Unsupported Media Type",
    416: b"Requested Range Not Satisfiable",
    417: b"Expectation Failed",
    418: b"I'm a teapot",
    422: b"Unprocessable Entity",
    423: b"Locked",
    424: b"Failed Dependency",
    426: b"Upgrade Required",
    428: b"Precondition Required",
    429: b"Too Many Requests",
    431: b"Request Header Fields Too Large",
    451: b"Unavailable For Legal Reasons",
    500: b"Internal Server Error",
    501: b"Not Implemented",
    502: b"Bad Gateway",
    503: b"Service Unavailable",
    504: b"Gateway Timeout",
    505: b"HTTP Version Not Supported",
    506: b"Variant Also Negotiates",
    507: b"Insufficient Storage",
    508: b"Loop Detected",
    510: b"Not Extended",
    511: b"Network Authentication Required",
}

# According to https://tools.ietf.org/html/rfc2616#section-7.1
_ENTITY_HEADERS = frozenset(
    [
        "allow",
        "content-encoding",
        "content-language",
        "content-length",
        "content-location",
        "content-md5",
        "content-range",
        "content-type",
        "expires",
        "last-modified",
        "extension-header",
    ]
)

# According to https://tools.ietf.org/html/rfc2616#section-13.5.1
_HOP_BY_HOP_HEADERS = frozenset(
    [
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
    ]
)


def has_message_body(status):
    """项目内部接口说明。"""
    return status not in (204, 304) and not (100 <= status < 200)


def is_entity_header(header):
    """项目内部接口说明。"""
    return header.lower() in _ENTITY_HEADERS


def is_hop_by_hop_header(header):
    """项目内部接口说明。"""
    return header.lower() in _HOP_BY_HOP_HEADERS


def import_string(module_name, package=None):
    """项目内部接口说明。"""
    module, klass = module_name.rsplit(".", 1)
    module = import_module(module, package=package)
    obj = getattr(module, klass)
    if ismodule(obj):
        return obj
    return obj()


def is_atty() -> bool:
    return bool(sys.stdout and sys.stdout.isatty())


class Default:
    """项目内部接口说明。"""

    def __repr__(self):
        return "<Default>"

    def __str__(self) -> str:
        return self.__repr__()


_default = Default()
