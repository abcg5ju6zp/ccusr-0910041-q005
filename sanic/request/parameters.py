from __future__ import annotations

from typing import Any


class RequestParameters(dict):
    """项目内部接口说明。"""

    def get(self, name: str, default: Any | None = None) -> Any | None:
        """项目内部接口说明。"""
        return super().get(name, [default])[0]

    def getlist(
        self, name: str, default: list[Any] | None = None
    ) -> list[Any]:
        """项目内部接口说明。"""
        return super().get(name, default) or []
