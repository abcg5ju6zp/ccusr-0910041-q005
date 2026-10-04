from __future__ import annotations

from abc import ABC, ABCMeta, abstractmethod
from collections.abc import Sequence
from inspect import getmembers, isclass, isdatadescriptor
from os import environ
from pathlib import Path
from typing import Any, Callable, Literal
from warnings import filterwarnings

from sanic.constants import LocalCertCreator
from sanic.errorpages import DEFAULT_FORMAT, check_error_format
from sanic.helpers import Default, _default
from sanic.http import Http
from sanic.log import error_logger
from sanic.utils import load_module_from_file_location, str_to_bool


FilterWarningType = (
    Literal["default"]
    | Literal["error"]
    | Literal["ignore"]
    | Literal["always"]
    | Literal["module"]
    | Literal["once"]
)

SANIC_PREFIX = "SANIC_"


DEFAULT_CONFIG = {
    "_FALLBACK_ERROR_FORMAT": _default,
    "ACCESS_LOG": False,
    "AUTO_EXTEND": True,
    "AUTO_RELOAD": False,
    "EVENT_AUTOREGISTER": False,
    "DEPRECATION_FILTER": "once",
    "FORWARDED_FOR_HEADER": "X-Forwarded-For",
    "FORWARDED_SECRET": None,  # nosec B105
    "GRACEFUL_SHUTDOWN_TIMEOUT": 15.0,
    "GRACEFUL_TCP_CLOSE_TIMEOUT": 5.0,
    "INSPECTOR": False,
    "INSPECTOR_HOST": "localhost",
    "INSPECTOR_PORT": 6457,
    "INSPECTOR_TLS_KEY": _default,
    "INSPECTOR_TLS_CERT": _default,
    "INSPECTOR_API_KEY": "",
    "KEEP_ALIVE_TIMEOUT": 120,
    "KEEP_ALIVE": True,
    "LOCAL_CERT_CREATOR": LocalCertCreator.AUTO,
    "LOCAL_TLS_KEY": _default,
    "LOCAL_TLS_CERT": _default,
    "LOCALHOST": "localhost",
    "LOG_EXTRA": _default,
    "MOTD": True,
    "MOTD_DISPLAY": {},
    "NO_COLOR": False,
    "NOISY_EXCEPTIONS": False,
    "PROXIES_COUNT": None,
    "REAL_IP_HEADER": None,
    "REQUEST_BUFFER_SIZE": 65536,
    "REQUEST_MAX_HEADER_SIZE": 8192,  # Cannot exceed 16384
    "REQUEST_ID_HEADER": "X-Request-ID",
    "REQUEST_MAX_SIZE": 100_000_000,
    "REQUEST_TIMEOUT": 60,
    "RESPONSE_TIMEOUT": 60,
    "TLS_CERT_PASSWORD": "",  # nosec B105
    "TOUCHUP": _default,
    "USE_UVLOOP": _default,
    "WEBSOCKET_MAX_SIZE": 2**20,  # 1 MiB
    "WEBSOCKET_PING_INTERVAL": 20,
    "WEBSOCKET_PING_TIMEOUT": 20,
}


class DescriptorMeta(ABCMeta):
    """项目内部接口说明。"""

    def __init__(cls, *_):
        cls.__setters__ = {name for name, _ in getmembers(cls, cls._is_setter)}

    @staticmethod
    def _is_setter(member: object):
        return isdatadescriptor(member) and hasattr(member, "setter")


class DetailedConverter(ABC):
    """项目内部接口说明。"""

    @abstractmethod
    def __call__(
        self, full_key: str, config_key: str, value: str, defaults: dict
    ) -> Any:
        """项目内部接口说明。"""


class Config(dict, metaclass=DescriptorMeta):
    """项目内部接口说明。"""

    ACCESS_LOG: bool
    AUTO_EXTEND: bool
    AUTO_RELOAD: bool
    EVENT_AUTOREGISTER: bool
    DEPRECATION_FILTER: FilterWarningType
    FORWARDED_FOR_HEADER: str
    FORWARDED_SECRET: str | None
    GRACEFUL_SHUTDOWN_TIMEOUT: float
    GRACEFUL_TCP_CLOSE_TIMEOUT: float
    INSPECTOR: bool
    INSPECTOR_HOST: str
    INSPECTOR_PORT: int
    INSPECTOR_TLS_KEY: Path | str | Default
    INSPECTOR_TLS_CERT: Path | str | Default
    INSPECTOR_API_KEY: str
    KEEP_ALIVE_TIMEOUT: int
    KEEP_ALIVE: bool
    LOCAL_CERT_CREATOR: str | LocalCertCreator
    LOCAL_TLS_KEY: Path | str | Default
    LOCAL_TLS_CERT: Path | str | Default
    LOCALHOST: str
    LOG_EXTRA: Default | bool
    MOTD: bool
    MOTD_DISPLAY: dict[str, str]
    NO_COLOR: bool
    NOISY_EXCEPTIONS: bool
    PROXIES_COUNT: int | None
    REAL_IP_HEADER: str | None
    REQUEST_BUFFER_SIZE: int
    REQUEST_MAX_HEADER_SIZE: int
    REQUEST_ID_HEADER: str
    REQUEST_MAX_SIZE: int
    REQUEST_TIMEOUT: int
    RESPONSE_TIMEOUT: int
    SERVER_NAME: str
    TLS_CERT_PASSWORD: str
    TOUCHUP: Default | bool
    USE_UVLOOP: Default | bool
    WEBSOCKET_MAX_SIZE: int
    WEBSOCKET_PING_INTERVAL: int
    WEBSOCKET_PING_TIMEOUT: int

    def __init__(
        self,
        defaults: dict[str, str | bool | int | float | None] | None = None,
        env_prefix: str | None = SANIC_PREFIX,
        keep_alive: bool | None = None,
        *,
        converters: Sequence[Callable[[str], Any]] | None = None,
    ):
        defaults = defaults or {}
        self.defaults = {**DEFAULT_CONFIG, **defaults}
        super().__init__(self.defaults)
        self._configure_warnings()

        self._converters = [str, str_to_bool, float, int]

        if converters:
            for converter in converters:
                self.register_type(converter)

        if keep_alive is not None:
            self.KEEP_ALIVE = keep_alive

        if env_prefix != SANIC_PREFIX:
            if env_prefix:
                self.load_environment_vars(env_prefix)
        else:
            self.load_environment_vars(SANIC_PREFIX)

        self._configure_header_size()
        self._check_error_format()
        self._init = True

    def __getattr__(self, attr: Any):
        try:
            return self[attr]
        except KeyError as ke:
            raise AttributeError(f"Config has no '{ke.args[0]}'")

    def __setattr__(self, attr: str, value: Any) -> None:
        self.update({attr: value})

    def __setitem__(self, attr: str, value: Any) -> None:
        self.update({attr: value})

    def update(self, *other: Any, **kwargs: Any) -> None:
        """项目内部接口说明。"""
        kwargs.update({k: v for item in other for k, v in dict(item).items()})
        setters: dict[str, Any] = {
            k: kwargs.pop(k)
            for k in {**kwargs}.keys()
            if k in self.__class__.__setters__
        }

        for key, value in setters.items():
            try:
                super().__setattr__(key, value)
            except AttributeError:
                ...

        super().update(**kwargs)
        for attr, value in {**setters, **kwargs}.items():
            self._post_set(attr, value)

    def _post_set(self, attr, value) -> None:
        if self.get("_init"):
            if attr in (
                "REQUEST_MAX_HEADER_SIZE",
                "REQUEST_BUFFER_SIZE",
                "REQUEST_MAX_SIZE",
            ):
                self._configure_header_size()

        if attr == "LOCAL_CERT_CREATOR" and not isinstance(
            self.LOCAL_CERT_CREATOR, LocalCertCreator
        ):
            self.LOCAL_CERT_CREATOR = LocalCertCreator[
                self.LOCAL_CERT_CREATOR.upper()
            ]
        elif attr == "DEPRECATION_FILTER":
            self._configure_warnings()

    @property
    def FALLBACK_ERROR_FORMAT(self) -> str:
        if isinstance(self._FALLBACK_ERROR_FORMAT, Default):
            return DEFAULT_FORMAT
        return self._FALLBACK_ERROR_FORMAT

    @FALLBACK_ERROR_FORMAT.setter
    def FALLBACK_ERROR_FORMAT(self, value):
        self._check_error_format(value)
        if (
            not isinstance(self._FALLBACK_ERROR_FORMAT, Default)
            and value != self._FALLBACK_ERROR_FORMAT
        ):
            error_logger.warning(
                "Setting config.FALLBACK_ERROR_FORMAT on an already "
                "configured value may have unintended consequences."
            )
        self._FALLBACK_ERROR_FORMAT = value

    def _configure_header_size(self):
        Http.set_header_max_size(
            self.REQUEST_MAX_HEADER_SIZE,
            self.REQUEST_BUFFER_SIZE - 4096,
            self.REQUEST_MAX_SIZE,
        )

    def _configure_warnings(self):
        filterwarnings(
            self.DEPRECATION_FILTER,
            category=DeprecationWarning,
            module=r"sanic.*",
        )

    def _check_error_format(self, format: str | None = None):
        check_error_format(format or self.FALLBACK_ERROR_FORMAT)

    def load_environment_vars(self, prefix=SANIC_PREFIX):
        """项目内部接口说明。"""
        for key, value in environ.items():
            if not key.startswith(prefix) or not key.isupper():
                continue

            _, config_key = key.split(prefix, 1)

            for converter in reversed(self._converters):
                try:
                    if isinstance(converter, DetailedConverter):
                        self[config_key] = converter(
                            key, config_key, value, self.defaults
                        )
                    else:
                        self[config_key] = converter(value)
                    break
                except ValueError:
                    pass

    def update_config(self, config: bytes | str | dict[str, Any] | Any):
        """项目内部接口说明。"""
        if isinstance(config, (bytes, str, Path)):
            config = load_module_from_file_location(location=config)

        if not isinstance(config, dict):
            cfg = {}
            if not isclass(config):
                cfg.update(
                    {
                        key: getattr(config, key)
                        for key in config.__class__.__dict__.keys()
                    }
                )

            config = dict(config.__dict__)
            config.update(cfg)

        config = dict(filter(lambda i: i[0].isupper(), config.items()))

        self.update(config)

    load = update_config

    def register_type(self, converter: Callable[[str], Any]) -> None:
        """项目内部接口说明。"""
        if converter in self._converters:
            error_logger.warning(
                f"Configuration value converter '{converter.__name__}' has "
                "already been registered"
            )
            return
        self._converters.append(converter)
