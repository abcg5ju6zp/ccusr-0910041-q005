from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from inspect import isawaitable, ismethod
from multiprocessing.connection import Connection
from os import environ
from pathlib import Path
from typing import Any

from sanic.exceptions import NotFound, Unauthorized
from sanic.helpers import Default
from sanic.log import logger
from sanic.request import Request
from sanic.response import json


class Inspector:
    """项目内部接口说明。"""

    def __init__(
        self,
        publisher: Connection,
        app_info: dict[str, Any],
        worker_state: Mapping[str, Any],
        host: str,
        port: int,
        api_key: str,
        tls_key: Path | str | Default,
        tls_cert: Path | str | Default,
    ):
        self._publisher = publisher
        self.app_info = app_info
        self.worker_state = worker_state
        self.host = host
        self.port = port
        self.api_key = api_key
        self.tls_key = tls_key
        self.tls_cert = tls_cert

    def __call__(self, run=True, **_) -> Inspector:
        from sanic import Sanic

        self.app = Sanic("Inspector")
        self._setup()
        if run:
            self.app.run(
                host=self.host,
                port=self.port,
                single_process=True,
                ssl={"key": self.tls_key, "cert": self.tls_cert}
                if not isinstance(self.tls_key, Default)
                and not isinstance(self.tls_cert, Default)
                else None,
            )
        return self

    def _setup(self):
        self.app.get("/")(self._info)
        self.app.post("/<action:str>")(self._action)
        if self.api_key:
            self.app.on_request(self._authentication)
        environ["SANIC_IGNORE_PRODUCTION_WARNING"] = "true"

    def _authentication(self, request: Request) -> None:
        if request.token != self.api_key:
            raise Unauthorized("Bad API key")

    async def _action(self, request: Request, action: str):
        logger.info("Incoming inspector action: %s", action)
        method = getattr(self, action, None)
        # Only public, bound methods may be dispatched as actions. This blocks
        # dunder/private methods, data attributes and anything on a subclass
        # that is not an intentional action.
        if action.startswith("_") or not ismethod(method):
            raise NotFound(f"Unknown action: {action}")

        kwargs = {}
        if request.body:
            kwargs = request.json
        args = kwargs.pop("args", ())
        output = method(*args, **kwargs)
        if isawaitable(output):
            output = await output

        return await self._respond(request, output)

    async def _info(self, request: Request):
        return await self._respond(request, self._state_to_json())

    async def _respond(self, request: Request, output: Any):
        name = request.match_info.get("action", "info")
        return json({"meta": {"action": name}, "result": output})

    def _state_to_json(self) -> dict[str, Any]:
        output = {"info": self.app_info}
        output["workers"] = self._make_safe(dict(self.worker_state))
        return output

    @staticmethod
    def _make_safe(obj: dict[str, Any]) -> dict[str, Any]:
        for key, value in obj.items():
            if isinstance(value, dict):
                obj[key] = Inspector._make_safe(value)
            elif isinstance(value, datetime):
                obj[key] = value.isoformat()
        return obj

    def reload(self, zero_downtime: bool = False) -> None:
        """项目内部接口说明。"""
        message = "__ALL_PROCESSES__:"
        if zero_downtime:
            message += ":STARTUP_FIRST"
        self._publisher.send(message)

    def scale(self, replicas: str | int) -> str:
        """项目内部接口说明。"""
        num_workers = 1
        if replicas:
            num_workers = int(replicas)
        log_msg = f"Scaling to {num_workers}"
        logger.info(log_msg)
        message = f"__SCALE__:{num_workers}"
        self._publisher.send(message)
        return log_msg

    def shutdown(self) -> None:
        """项目内部接口说明。"""
        message = "__TERMINATE__"
        self._publisher.send(message)
