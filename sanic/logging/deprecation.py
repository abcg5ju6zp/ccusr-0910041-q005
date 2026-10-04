from warnings import warn

from sanic.helpers import is_atty
from sanic.logging.color import Colors


def deprecation(message: str, version: float):  # no cov
    """项目内部接口说明。"""
    version_display = f" v{version}" if version else ""
    version_info = f"[DEPRECATION{version_display}] "
    if is_atty():
        version_info = f"{Colors.RED}{version_info}"
        message = f"{Colors.YELLOW}{message}{Colors.END}"
    warn(version_info + message, DeprecationWarning)
