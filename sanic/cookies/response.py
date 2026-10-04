from __future__ import annotations

import re
import string

from datetime import datetime
from typing import TYPE_CHECKING, Literal, cast

from sanic.exceptions import ServerError


if TYPE_CHECKING:
    from sanic.compat import Header


SameSite = (
    Literal["Strict"]
    | Literal["Lax"]
    | Literal["None"]
    | Literal["strict"]
    | Literal["lax"]
    | Literal["none"]
)

DEFAULT_MAX_AGE = 0
SAMESITE_VALUES = ("strict", "lax", "none")

LEGAL_CHARS = string.ascii_letters + string.digits + "!#$%&'*+-.^_`|~:"
UNESCAPED_CHARS = LEGAL_CHARS + " ()/<=>?@[]{}"
TRANSLATOR = {ch: f"\\{ch:03o}" for ch in bytes(range(32)) + b'";\\\x7f'}


def _quote(str):  # no cov
    """项目内部接口说明。"""
    if str is None or _is_legal_key(str):
        return str
    else:
        return f'"{str.translate(TRANSLATOR)}"'


_is_legal_key = re.compile("[%s]+" % re.escape(LEGAL_CHARS)).fullmatch


class CookieJar:
    """项目内部接口说明。"""

    HEADER_KEY = "Set-Cookie"

    def __init__(self, headers: Header):
        self.headers = headers

    def __len__(self):  # no cov
        return len(self.cookies)

    @property
    def cookies(self) -> list[Cookie]:
        """项目内部接口说明。"""
        return self.headers.getall(self.HEADER_KEY, [])

    def get_cookie(
        self,
        key: str,
        path: str = "/",
        domain: str | None = None,
        host_prefix: bool = False,
        secure_prefix: bool = False,
    ) -> Cookie | None:
        """项目内部接口说明。"""
        for cookie in self.cookies:
            if (
                cookie.key == Cookie.make_key(key, host_prefix, secure_prefix)
                and cookie.path == path
                and cookie.domain == domain
            ):
                return cookie
        return None

    def has_cookie(
        self,
        key: str,
        path: str = "/",
        domain: str | None = None,
        host_prefix: bool = False,
        secure_prefix: bool = False,
    ) -> bool:
        """项目内部接口说明。"""
        for cookie in self.cookies:
            if (
                cookie.key == Cookie.make_key(key, host_prefix, secure_prefix)
                and cookie.path == path
                and cookie.domain == domain
            ):
                return True
        return False

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
        cookie = Cookie(
            key,
            value,
            path=path,
            expires=expires,
            comment=comment,
            domain=domain,
            max_age=max_age,
            secure=secure,
            httponly=httponly,
            samesite=samesite,
            partitioned=partitioned,
            host_prefix=host_prefix,
            secure_prefix=secure_prefix,
        )
        self.headers.add(self.HEADER_KEY, cookie)

        return cookie

    def delete_cookie(
        self,
        key: str,
        *,
        path: str = "/",
        domain: str | None = None,
        secure: bool = True,
        host_prefix: bool = False,
        secure_prefix: bool = False,
    ) -> None:
        """项目内部接口说明。"""
        if host_prefix and not (secure and path == "/" and domain is None):
            raise ServerError(
                "Cannot set host_prefix on a cookie without "
                "path='/', domain=None, and secure=True"
            )
        if secure_prefix and not secure:
            raise ServerError(
                "Cannot set secure_prefix on a cookie without secure=True"
            )

        cookies: list[Cookie] = self.headers.popall(self.HEADER_KEY, [])
        existing_cookie = None
        for cookie in cookies:
            if (
                cookie.key != Cookie.make_key(key, host_prefix, secure_prefix)
                or cookie.path != path
                or cookie.domain != domain
            ):
                self.headers.add(self.HEADER_KEY, cookie)
            elif existing_cookie is None:
                existing_cookie = cookie

        if existing_cookie is not None:
            # Use all the same values as the cookie to be deleted
            # except value="" and max_age=0
            self.add_cookie(
                key=key,
                value="",
                path=existing_cookie.path,
                domain=existing_cookie.domain,
                secure=existing_cookie.secure,
                max_age=0,
                httponly=existing_cookie.httponly,
                partitioned=existing_cookie.partitioned,
                samesite=existing_cookie.samesite,
                host_prefix=host_prefix,
                secure_prefix=secure_prefix,
            )
        else:
            self.add_cookie(
                key=key,
                value="",
                path=path,
                domain=domain,
                secure=secure,
                max_age=0,
                samesite=None,
                host_prefix=host_prefix,
                secure_prefix=secure_prefix,
            )


class Cookie:
    """项目内部接口说明。"""

    HOST_PREFIX = "__Host-"
    SECURE_PREFIX = "__Secure-"

    __slots__ = (
        "key",
        "value",
        "_path",
        "_comment",
        "_domain",
        "_secure",
        "_httponly",
        "_partitioned",
        "_expires",
        "_max_age",
        "_samesite",
    )

    _keys = {
        "path": "Path",
        "comment": "Comment",
        "domain": "Domain",
        "max-age": "Max-Age",
        "expires": "expires",
        "samesite": "SameSite",
        # "version": "Version",
        "secure": "Secure",
        "httponly": "HttpOnly",
        "partitioned": "Partitioned",
    }
    _flags = {"secure", "httponly", "partitioned"}

    def __init__(
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
    ):
        if key in self._keys:
            raise KeyError("Cookie name is a reserved word")
        if not _is_legal_key(key):
            raise KeyError("Cookie key contains illegal characters")
        if host_prefix:
            if not secure:
                raise ServerError(
                    "Cannot set host_prefix on a cookie without secure=True"
                )
            if path != "/":
                raise ServerError(
                    "Cannot set host_prefix on a cookie unless path='/'"
                )
            if domain:
                raise ServerError(
                    "Cannot set host_prefix on a cookie with a defined domain"
                )
        elif secure_prefix and not secure:
            raise ServerError(
                "Cannot set secure_prefix on a cookie without secure=True"
            )
        if partitioned and not host_prefix:
            # This is technically possible, but it is not advisable so we will
            # take a stand and say "don't shoot yourself in the foot"
            raise ServerError(
                "Cannot create a partitioned cookie without "
                "also setting host_prefix=True"
            )

        self.key = self.make_key(key, host_prefix, secure_prefix)
        self.value = value

        self._path = path
        self._comment = comment
        self._domain = domain
        self._secure = secure
        self._httponly = httponly
        self._partitioned = partitioned
        self._expires: datetime | None = None
        self._max_age: int | None = None
        self._samesite: SameSite | None = None

        if expires is not None:
            self.expires = expires
        if max_age is not None:
            self.max_age = max_age
        if samesite is not None:
            self.samesite = samesite

    def __str__(self):
        """项目内部接口说明。"""
        output = ["{}={}".format(self.key, _quote(self.value))]
        ordered_keys = list(self._keys.keys())
        for key in sorted(
            self._keys.keys(), key=lambda k: ordered_keys.index(k)
        ):
            value = getattr(self, key.replace("-", "_"))
            if value is not None and value is not False:
                if key == "max-age":
                    try:
                        output.append("%s=%d" % (self._keys[key], value))
                    except TypeError:
                        output.append("{}={}".format(self._keys[key], value))
                elif key == "expires":
                    output.append(
                        "%s=%s"
                        % (
                            self._keys[key],
                            value.strftime("%a, %d-%b-%Y %T GMT"),
                        )
                    )
                elif key in self._flags:
                    output.append(self._keys[key])
                else:
                    output.append("{}={}".format(self._keys[key], value))

        return "; ".join(output)

    @property
    def path(self) -> str:  # no cov
        """项目内部接口说明。"""
        return self._path

    @path.setter
    def path(self, value: str) -> None:  # no cov
        self._path = value

    @property
    def expires(self) -> datetime | None:  # no cov
        """项目内部接口说明。"""
        return self._expires

    @expires.setter
    def expires(self, value: datetime) -> None:  # no cov
        if not isinstance(value, datetime):
            raise TypeError("Cookie 'expires' property must be a datetime")
        self._expires = value

    @property
    def comment(self) -> str | None:  # no cov
        """项目内部接口说明。"""
        return self._comment

    @comment.setter
    def comment(self, value: str) -> None:  # no cov
        self._comment = value

    @property
    def domain(self) -> str | None:  # no cov
        """项目内部接口说明。"""
        return self._domain

    @domain.setter
    def domain(self, value: str) -> None:  # no cov
        self._domain = value

    @property
    def max_age(self) -> int | None:  # no cov
        """项目内部接口说明。"""
        return self._max_age

    @max_age.setter
    def max_age(self, value: int) -> None:  # no cov
        if not str(value).isdigit():
            raise ValueError("Cookie max-age must be an integer")
        self._max_age = value

    @property
    def secure(self) -> bool:  # no cov
        """项目内部接口说明。"""
        return self._secure

    @secure.setter
    def secure(self, value: bool) -> None:  # no cov
        self._secure = value

    @property
    def httponly(self) -> bool:  # no cov
        """项目内部接口说明。"""
        return self._httponly

    @httponly.setter
    def httponly(self, value: bool) -> None:  # no cov
        self._httponly = value

    @property
    def samesite(self) -> SameSite | None:  # no cov
        """项目内部接口说明。"""
        return self._samesite

    @samesite.setter
    def samesite(self, value: SameSite) -> None:  # no cov
        if value.lower() not in SAMESITE_VALUES:
            raise TypeError(
                "Cookie 'samesite' property must "
                f"be one of: {','.join(SAMESITE_VALUES)}"
            )
        self._samesite = cast(SameSite, value.title())

    @property
    def partitioned(self) -> bool:  # no cov
        """项目内部接口说明。"""
        return self._partitioned

    @partitioned.setter
    def partitioned(self, value: bool) -> None:  # no cov
        self._partitioned = value

    @classmethod
    def make_key(
        cls, key: str, host_prefix: bool = False, secure_prefix: bool = False
    ) -> str:
        """项目内部接口说明。"""
        if host_prefix and secure_prefix:
            raise ServerError(
                "Both host_prefix and secure_prefix were requested. "
                "A cookie should have only one prefix."
            )
        elif host_prefix:
            key = cls.HOST_PREFIX + key
        elif secure_prefix:
            key = cls.SECURE_PREFIX + key
        return key
