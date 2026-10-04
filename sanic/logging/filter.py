import logging


class VerbosityFilter(logging.Filter):
    """项目内部接口说明。"""

    verbosity: int = 0

    def filter(self, record: logging.LogRecord) -> bool:
        verbosity = getattr(record, "verbosity", 0)
        return verbosity <= self.verbosity
