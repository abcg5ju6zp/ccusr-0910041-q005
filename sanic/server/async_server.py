from __future__ import annotations

from typing import TYPE_CHECKING

from sanic.exceptions import SanicException


if TYPE_CHECKING:
    from sanic import Sanic


class AsyncioServer:
    """项目内部接口说明。"""

    __slots__ = ("app", "connections", "loop", "serve_coro", "server")

    def __init__(
        self,
        app: Sanic,
        loop,
        serve_coro,
        connections,
    ):
        # Note, Sanic already called "before_server_start" events
        # before this helper was even created. So we don't need it here.
        self.app = app
        self.connections = connections
        self.loop = loop
        self.serve_coro = serve_coro
        self.server = None

    def startup(self):
        """项目内部接口说明。"""
        return self.app._startup()

    def before_start(self):
        """项目内部接口说明。"""
        return self._server_event("init", "before")

    def after_start(self):
        """项目内部接口说明。"""
        return self._server_event("init", "after")

    def before_stop(self):
        """项目内部接口说明。"""
        return self._server_event("shutdown", "before")

    def after_stop(self):
        """项目内部接口说明。"""
        return self._server_event("shutdown", "after")

    def is_serving(self) -> bool:
        """项目内部接口说明。"""
        if self.server:
            return self.server.is_serving()
        return False

    def wait_closed(self):
        """项目内部接口说明。"""
        if self.server:
            return self.server.wait_closed()

    def close(self):
        """项目内部接口说明。"""
        if self.server:
            self.server.close()
            coro = self.wait_closed()
            task = self.loop.create_task(coro)
            return task

    def start_serving(self):
        """项目内部接口说明。"""
        return self._serve(self.server.start_serving)

    def serve_forever(self):
        """项目内部接口说明。"""
        return self._serve(self.server.serve_forever)

    def _serve(self, serve_func):
        if self.server:
            if not self.app.state.is_started:
                raise SanicException(
                    "Cannot run Sanic server without first running "
                    "await server.startup()"
                )

            try:
                return serve_func()
            except AttributeError:
                name = serve_func.__name__
                raise NotImplementedError(
                    f"server.{name} not available in this version "
                    "of asyncio or uvloop."
                )

    def _server_event(self, concern: str, action: str):
        if not self.app.state.is_started:
            raise SanicException(
                "Cannot dispatch server event without "
                "first running await server.startup()"
            )
        return self.app._server_event(concern, action, loop=self.loop)

    def __await__(self):
        """项目内部接口说明。"""
        task = self.loop.create_task(self.serve_coro)
        while not task.done():
            yield
        self.server = task.result()
        return self
