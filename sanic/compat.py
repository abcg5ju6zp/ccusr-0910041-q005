import asyncio
import os
import platform
import signal
import sys

from collections.abc import Awaitable
from contextlib import contextmanager
from enum import Enum
from typing import Literal

from multidict import CIMultiDict  # type: ignore

from sanic.helpers import Default
from sanic.log import error_logger


StartMethod = (
    Default | Literal["fork"] | Literal["forkserver"] | Literal["spawn"]
)

OS_IS_WINDOWS = os.name == "nt"
PYPY_IMPLEMENTATION = platform.python_implementation() == "PyPy"
UVLOOP_INSTALLED = False
PYTHON_314_OR_LATER = sys.version_info >= (3, 14)

try:
    import uvloop  # type: ignore # noqa

    UVLOOP_INSTALLED = True
except ImportError:
    pass

# Python 3.11 changed the way Enum formatting works for mixed-in types.
if sys.version_info < (3, 11, 0):

    class StrEnum(str, Enum):
        pass

else:
    from enum import StrEnum  # type: ignore # noqa


class UpperStrEnum(StrEnum):
    """项目内部接口说明。"""

    def _generate_next_value_(name, start, count, last_values):
        return name.upper()

    def __eq__(self, value: object) -> bool:
        value = str(value).upper()
        return super().__eq__(value)

    def __hash__(self) -> int:
        return hash(self.value)

    def __str__(self) -> str:
        return self.value


@contextmanager
def use_context(method: StartMethod):
    from sanic import Sanic

    orig = Sanic.start_method
    Sanic.start_method = method
    yield
    Sanic.start_method = orig


def enable_windows_color_support():
    import ctypes

    kernel = ctypes.windll.kernel32
    kernel.SetConsoleMode(kernel.GetStdHandle(-11), 7)


def pypy_os_module_patch() -> None:
    """项目内部接口说明。"""
    if hasattr(os, "readlink"):
        error_logger.debug(
            "PyPy: Skipping patching of the os module as it appears the "
            "'readlink' function has been added."
        )
        return

    module = sys.modules["os"]
    module.readlink = os.path.realpath  # type: ignore


def pypy_windows_set_console_cp_patch() -> None:
    """项目内部接口说明。"""
    from ctypes import windll  # type: ignore

    code: int = windll.kernel32.GetConsoleOutputCP()
    if code != 65001:
        windll.kernel32.SetConsoleCP(65001)
        windll.kernel32.SetConsoleOutputCP(65001)


class Header(CIMultiDict):
    """项目内部接口说明。"""

    def __getattr__(self, key: str) -> str:
        if key.startswith("_"):
            return self.__getattribute__(key)
        key = key.rstrip("_").replace("_", "-")
        return ",".join(self.getall(key, []))

    def get_all(self, key: str):
        """项目内部接口说明。"""
        return self.getall(key, [])


use_trio = sys.argv[0].endswith("hypercorn") and "trio" in sys.argv

if use_trio:  # pragma: no cover
    import trio  # type: ignore

    def stat_async(path) -> Awaitable[os.stat_result]:
        return trio.Path(path).stat()

    open_async = trio.open_file
    CancelledErrors = tuple([asyncio.CancelledError, trio.Cancelled])
else:
    if PYPY_IMPLEMENTATION:
        pypy_os_module_patch()

        if OS_IS_WINDOWS:
            pypy_windows_set_console_cp_patch()

    from aiofiles import open as aio_open  # type: ignore
    from aiofiles.os import stat as stat_async  # type: ignore  # noqa: F401

    async def open_async(file, mode="r", **kwargs):
        return aio_open(file, mode, **kwargs)

    CancelledErrors = tuple([asyncio.CancelledError])


def ctrlc_workaround_for_windows(app):
    async def stay_active(app):
        """项目内部接口说明。"""
        while not die:
            # If someone else stopped the app, just exit
            if app.state.is_stopping:
                return
            # Windows Python blocks signal handlers while the event loop is
            # waiting for I/O. Frequent wakeups keep interrupts flowing.
            await asyncio.sleep(0.1)
        # Can't be called from signal handler, so call it from here
        app.stop()

    def ctrlc_handler(sig, frame):
        nonlocal die
        if die:
            raise KeyboardInterrupt("Non-graceful Ctrl+C")
        die = True

    die = False
    signal.signal(signal.SIGINT, ctrlc_handler)
    app.add_task(stay_active)


def clear_function_annotate(*funcs):
    """项目内部接口说明。"""
    if PYTHON_314_OR_LATER:
        for func in funcs:
            if hasattr(func, "__annotate__") and func.__annotate__ is not None:
                func.__annotate__ = None
