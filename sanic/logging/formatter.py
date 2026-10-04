from __future__ import annotations

import logging
import os
import re

from sanic.helpers import is_atty, json_dumps
from sanic.logging.color import LEVEL_COLORS
from sanic.logging.color import Colors as c


CONTROL_RE = re.compile(r"\033\[[0-9;]*\w")
CONTROL_LIMIT_IDENT = "\033[1000D\033[{limit}C"
CONTROL_LIMIT_START = "\033[1000D\033[{start}C\033[K"
CONTROL_LIMIT_END = "\033[1000C\033[{right}D\033[K"
EXCEPTION_LINE_RE = re.compile(r"^(?P<exc>.*?): (?P<message>.*)$")
FILE_LINE_RE = re.compile(
    r"File \"(?P<path>.*?)\", line (?P<line_num>\d+), in (?P<location>.*)"
)
DEFAULT_FIELDS = set(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()
) | {
    "ident",
    "message",
    "asctime",
    "right",
}


class AutoFormatter(logging.Formatter):
    """项目内部接口说明。"""

    SETUP = False
    ATTY = is_atty()
    NO_COLOR = os.environ.get("SANIC_NO_COLOR", "false").lower() == "true"
    LOG_EXTRA = os.environ.get("SANIC_LOG_EXTRA", "true").lower() == "true"
    IDENT = os.environ.get("SANIC_WORKER_IDENTIFIER", "Main ") or "Main "
    DATE_FORMAT = "%Y-%m-%d %H:%M:%S %z"
    IDENT_LIMIT = 5
    MESSAGE_START = 42
    PREFIX_FORMAT = (
        f"{c.GREY}%(ident)s{{limit}} %(asctime)s {c.END}"
        "%(levelname)s: {start}"
    )
    MESSAGE_FORMAT = "%(message)s"

    def __init__(self, *args) -> None:
        args_list = list(args)
        if not args:
            args_list.append(self._make_format())
        elif args and not args[0]:
            args_list[0] = self._make_format()
        if len(args_list) < 2:
            args_list.append(self.DATE_FORMAT)
        elif not args[1]:
            args_list[1] = self.DATE_FORMAT

        super().__init__(*args_list)

    def format(self, record: logging.LogRecord) -> str:
        record.ident = self.IDENT
        self._set_levelname(record)
        output = super().format(record)
        if self.LOG_EXTRA:
            output += self._log_extra(record)
        return output

    def _set_levelname(self, record: logging.LogRecord) -> None:
        if (
            self.ATTY
            and not self.NO_COLOR
            and (color := LEVEL_COLORS.get(record.levelno))
        ):
            record.levelname = f"{color}{record.levelname}{c.END}"

    def _make_format(self) -> str:
        limit = CONTROL_LIMIT_IDENT.format(limit=self.IDENT_LIMIT)
        start = CONTROL_LIMIT_START.format(start=self.MESSAGE_START)
        base_format = self.PREFIX_FORMAT + self.MESSAGE_FORMAT
        fmt = base_format.format(limit=limit, start=start)
        if not self.ATTY or self.NO_COLOR:
            return CONTROL_RE.sub("", fmt)
        return fmt

    def _log_extra(self, record: logging.LogRecord, indent: int = 0) -> str:
        extra_lines = [""]

        for key, value in record.__dict__.items():
            if key not in DEFAULT_FIELDS:
                extra_lines.append(self._format_key_value(key, value, indent))

        return "\n".join(extra_lines)

    def _format_key_value(self, key, value, indent):
        indentation = " " * indent
        template = (
            f"{indentation}  {{c.YELLOW}}{{key}}{{c.END}}={{value}}"
            if self.ATTY and not self.NO_COLOR
            else f"{indentation}{{key}}={{value}}"
        )
        if isinstance(value, dict):
            nested_lines = [template.format(c=c, key=key, value="")]
            for nested_key, nested_value in value.items():
                nested_lines.append(
                    self._format_key_value(
                        nested_key, nested_value, indent + 2
                    )
                )
            return "\n".join(nested_lines)
        else:
            return template.format(c=c, key=key, value=value)


class DebugFormatter(AutoFormatter):
    """项目内部接口说明。"""

    IDENT_LIMIT = 5
    MESSAGE_START = 23
    DATE_FORMAT = "%H:%M:%S"

    def _set_levelname(self, record: logging.LogRecord) -> None:
        if len(record.levelname) > 5:
            record.levelname = record.levelname[:4]
        super()._set_levelname(record)

    def formatException(self, ei):  # no cov
        orig = super().formatException(ei)
        if not self.ATTY or self.NO_COLOR:
            return orig
        colored_traceback = []
        lines = orig.splitlines()
        for idx, line in enumerate(lines):
            if line.startswith("  File"):
                line = self._color_file_line(line)
            elif line.startswith("    "):
                line = self._color_code_line(line)
            elif (
                "Error" in line or "Exception" in line or len(lines) - 1 == idx
            ):
                line = self._color_exception_line(line)
            colored_traceback.append(line)
        return "\n".join(colored_traceback)

    def _color_exception_line(self, line: str) -> str:  # no cov
        match = EXCEPTION_LINE_RE.match(line)
        if not match:
            return line
        exc = match.group("exc")
        message = match.group("message")
        return f"{c.SANIC}{c.BOLD}{exc}{c.END}: {c.BOLD}{message}{c.END}"

    def _color_file_line(self, line: str) -> str:  # no cov
        match = FILE_LINE_RE.search(line)
        if not match:
            return line
        path = match.group("path")
        line_num = match.group("line_num")
        location = match.group("location")
        return (
            f'  File "{path}", line {c.CYAN}{c.BOLD}{line_num}{c.END}, '
            f"in {c.BLUE}{c.BOLD}{location}{c.END}"
        )

    def _color_code_line(self, line: str) -> str:  # no cov
        return f"{c.YELLOW}{line}{c.END}"


class ProdFormatter(AutoFormatter):
    """项目内部接口说明。"""


class LegacyFormatter(AutoFormatter):
    """项目内部接口说明。"""

    PREFIX_FORMAT = "%(asctime)s [%(process)s] [%(levelname)s] "
    DATE_FORMAT = "[%Y-%m-%d %H:%M:%S %z]"


class AutoAccessFormatter(AutoFormatter):
    MESSAGE_FORMAT = (
        f"{c.PURPLE}%(host)s "
        f"{c.BLUE + c.BOLD}%(request)s{c.END} "
        f"%(right)s%(status)s %(byte)s {c.GREY}%(duration)s{c.END}"
    )

    def format(self, record: logging.LogRecord) -> str:
        status = len(str(getattr(record, "status", "")))
        byte = len(str(getattr(record, "byte", "")))
        duration = len(str(getattr(record, "duration", "")))
        record.right = (
            CONTROL_LIMIT_END.format(right=status + byte + duration + 1)
            if self.ATTY
            else ""
        )
        return super().format(record)

    def _set_levelname(self, record: logging.LogRecord) -> None:
        if self.ATTY and record.levelno == logging.INFO:
            record.levelname = f"{c.SANIC}ACCESS{c.END}"


class LegacyAccessFormatter(AutoAccessFormatter):
    """项目内部接口说明。"""

    PREFIX_FORMAT = "%(asctime)s - (%(name)s)[%(levelname)s][%(host)s]: "
    MESSAGE_FORMAT = "%(request)s %(message)s %(status)s %(byte)s"


class DebugAccessFormatter(AutoAccessFormatter):
    IDENT_LIMIT = 5
    MESSAGE_START = 23
    DATE_FORMAT = "%H:%M:%S"
    LOG_EXTRA = False


class ProdAccessFormatter(AutoAccessFormatter):
    IDENT_LIMIT = 5
    MESSAGE_START = 42
    PREFIX_FORMAT = (
        f"{c.GREY}%(ident)s{{limit}}|%(asctime)s{c.END} "
        f"%(levelname)s: {{start}}"
    )
    MESSAGE_FORMAT = (
        f"{c.PURPLE}%(host)s {c.BLUE + c.BOLD}"
        f"%(request)s{c.END} "
        f"%(right)s%(status)s %(byte)s {c.GREY}%(duration)s{c.END}"
    )
    LOG_EXTRA = False


class JSONFormatter(AutoFormatter):
    """项目内部接口说明。"""

    ATTY = False
    NO_COLOR = True
    FIELDS = [
        "name",
        "levelno",
        "pathname",
        "module",
        "filename",
        "lineno",
    ]

    dumps = json_dumps

    def format(self, record: logging.LogRecord) -> str:
        return self.format_dict(self.to_dict(record))

    def to_dict(self, record: logging.LogRecord) -> dict:
        base = {field: getattr(record, field, None) for field in self.FIELDS}
        extra = {
            key: value
            for key, value in record.__dict__.items()
            if key not in DEFAULT_FIELDS
        }
        info = {}
        if record.exc_info:
            info["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info:
            info["stack_info"] = self.formatStack(record.stack_info)
        return {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "message": record.getMessage(),
            **base,
            **info,
            **extra,
        }

    def format_dict(self, record: dict) -> str:
        return self.dumps(record)


class JSONAccessFormatter(JSONFormatter):
    """项目内部接口说明。"""

    FIELDS = [
        "host",
        "request",
        "status",
        "byte",
        "duration",
    ]

    def to_dict(self, record: logging.LogRecord) -> dict:
        base = {field: getattr(record, field, None) for field in self.FIELDS}
        return {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "message": record.getMessage(),
            **base,
        }
