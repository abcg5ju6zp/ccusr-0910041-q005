from enum import Enum, IntEnum


class Stage(Enum):
    """项目内部接口说明。"""

    IDLE = 0  # Waiting for request
    REQUEST = 1  # Request headers being received
    HANDLER = 3  # Headers done, handler running
    RESPONSE = 4  # Response headers sent, body in progress
    FAILED = 100  # Unrecoverable state (error while sending response)


class HTTP(IntEnum):
    """项目内部接口说明。"""

    VERSION_1 = 1
    VERSION_3 = 3

    def display(self) -> str:
        value = 1.1 if self.value == 1 else self.value
        return f"HTTP/{value}"
