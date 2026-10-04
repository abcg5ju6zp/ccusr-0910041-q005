from enum import IntEnum, auto

from sanic.compat import UpperStrEnum


class RestartOrder(UpperStrEnum):
    """项目内部接口说明。"""

    SHUTDOWN_FIRST = auto()
    STARTUP_FIRST = auto()


class ProcessState(IntEnum):
    """项目内部接口说明。"""

    NONE = auto()
    IDLE = auto()
    RESTARTING = auto()
    STARTING = auto()
    STARTED = auto()
    ACKED = auto()
    JOINED = auto()
    TERMINATED = auto()
    FAILED = auto()
    COMPLETED = auto()
