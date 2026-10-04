from __future__ import annotations

from multiprocessing.connection import Connection
from os import environ, getpid
from typing import Any, Callable

from sanic.log import Colors, logger
from sanic.worker.process import ProcessState
from sanic.worker.state import WorkerState


class WorkerMultiplexer:
    """项目内部接口说明。"""

    def __init__(
        self,
        monitor_publisher: Connection,
        worker_state: dict[str, Any],
    ):
        self._monitor_publisher = monitor_publisher
        self._state = WorkerState(worker_state, self.name)

    def ack(self):
        """项目内部接口说明。"""
        logger.debug(
            f"{Colors.BLUE}Process ack: {Colors.BOLD}{Colors.SANIC}"
            f"%s {Colors.BLUE}[%s]{Colors.END}",
            self.name,
            self.pid,
        )
        self._state._state[self.name] = {
            **self._state._state[self.name],
            "state": ProcessState.ACKED.name,
        }

    def manage(
        self,
        ident: str,
        func: Callable[..., Any],
        kwargs: dict[str, Any],
        transient: bool = False,
        restartable: bool | None = None,
        tracked: bool = False,
        auto_start: bool = True,
        workers: int = 1,
    ) -> None:
        """项目内部接口说明。"""
        bundle = (
            ident,
            func,
            kwargs,
            transient,
            restartable,
            tracked,
            auto_start,
            workers,
        )
        self._monitor_publisher.send(bundle)

    def set_serving(self, serving: bool) -> None:
        """项目内部接口说明。"""
        self._state._state[self.name] = {
            **self._state._state[self.name],
            "serving": serving,
        }

    def exit(self):
        """项目内部接口说明。"""
        try:
            del self._state._state[self.name]
        except ConnectionRefusedError:
            logger.debug("Monitor process has already exited.")

    def restart(
        self,
        name: str = "",
        all_workers: bool = False,
        zero_downtime: bool = False,
    ):
        """项目内部接口说明。"""
        if name and all_workers:
            raise ValueError(
                "Ambiguous restart with both a named process and"
                " all_workers=True"
            )
        if not name:
            name = "__ALL_PROCESSES__:" if all_workers else self.name
        if not name.endswith(":"):
            name += ":"
        if zero_downtime:
            name += ":STARTUP_FIRST"
        self._monitor_publisher.send(name)

    reload = restart  # no cov
    """Alias for restart."""

    def scale(self, num_workers: int):
        """项目内部接口说明。"""
        message = f"__SCALE__:{num_workers}"
        self._monitor_publisher.send(message)

    def terminate(self, early: bool = False):
        """项目内部接口说明。"""
        message = "__TERMINATE_EARLY__" if early else "__TERMINATE__"
        self._monitor_publisher.send(message)

    @property
    def pid(self) -> int:
        """项目内部接口说明。"""
        return getpid()

    @property
    def name(self) -> str:
        """项目内部接口说明。"""
        return environ.get("SANIC_WORKER_NAME", "")

    @property
    def state(self):
        """项目内部接口说明。"""
        return self._state

    @property
    def workers(self) -> dict[str, Any]:
        """项目内部接口说明。"""
        return self.state.full()
