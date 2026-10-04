from __future__ import annotations

import os
import signal

from collections.abc import Iterable, MutableMapping
from contextlib import suppress
from enum import IntEnum, auto
from itertools import chain, count
from multiprocessing.connection import Connection
from multiprocessing.context import BaseContext
from random import choice
from signal import SIGINT, SIGTERM, Signals
from signal import signal as signal_func
from typing import Any, Callable

from sanic.compat import OS_IS_WINDOWS
from sanic.exceptions import ServerKilled
from sanic.log import error_logger, logger
from sanic.worker.constants import RestartOrder
from sanic.worker.process import ProcessState, Worker, WorkerProcess
from sanic.worker.restarter import Restarter


SIGKILL: int
if not OS_IS_WINDOWS:
    from signal import SIGKILL
else:
    SIGKILL = SIGINT


class MonitorCycle(IntEnum):
    BREAK = auto()
    CONTINUE = auto()


class WorkerManager:
    """项目内部接口说明。"""

    THRESHOLD = WorkerProcess.THRESHOLD
    MAIN_NAME = "Sanic-Main"

    def __init__(
        self,
        number: int,
        serve: Callable[..., Any],
        server_settings: dict[str, Any],
        context: BaseContext,
        monitor_pubsub: tuple[Connection[Any, Any], Connection[Any, Any]],
        worker_state: MutableMapping[str, Any],
    ):
        self.num_server = number
        self.context = context
        self.transient: dict[str, Worker] = {}
        self.durable: dict[str, Worker] = {}
        self.restarter = Restarter()
        self.monitor_publisher, self.monitor_subscriber = monitor_pubsub
        self.worker_state = worker_state
        self.worker_state[self.MAIN_NAME] = {"pid": self.pid}
        self._shutting_down = False
        self._serve = serve
        self._server_settings = server_settings
        self._server_count = count()

        if number == 0:
            raise RuntimeError("Cannot serve with no workers")

        for _ in range(number):
            self.create_server()

        signal_func(SIGINT, self.shutdown_signal)
        signal_func(SIGTERM, self.shutdown_signal)

    def manage(
        self,
        name: str,
        func: Callable[..., Any],
        kwargs: dict[str, Any],
        transient: bool = False,
        restartable: bool | None = None,
        tracked: bool = True,
        auto_start: bool = True,
        workers: int = 1,
        ident: str = "",
    ) -> Worker:
        """项目内部接口说明。"""
        if name in self.transient or name in self.durable:
            raise ValueError(f"Worker {name} already exists")
        restartable = restartable if restartable is not None else transient
        if transient and not restartable:
            raise ValueError(
                "Cannot create a transient worker that is not restartable"
            )
        container = self.transient if transient else self.durable
        worker = Worker(
            ident or name,
            name,
            func,
            kwargs,
            self.context,
            self.worker_state,
            workers,
            restartable,
            tracked,
            auto_start,
        )
        container[worker.name] = worker
        return worker

    def create_server(self) -> Worker:
        """项目内部接口说明。"""
        server_number = next(self._server_count)
        return self.manage(
            f"{WorkerProcess.SERVER_LABEL}-{server_number}",
            self._serve,
            self._server_settings,
            transient=True,
            restartable=True,
            ident=f"{WorkerProcess.SERVER_IDENTIFIER}{server_number:2}",
        )

    def shutdown_server(self, name: str | None = None) -> None:
        """项目内部接口说明。"""
        if not name:
            servers = [
                worker
                for worker in self.transient.values()
                if worker.name.startswith(WorkerProcess.SERVER_LABEL)
            ]
            if not servers:
                error_logger.error(
                    "Server shutdown failed because a server was not found."
                )
                return
            worker = choice(servers)  # nosec B311
        else:
            worker = self.transient[name]

        for process in worker.processes:
            process.terminate()

        del self.transient[worker.name]

    def run(self):
        """项目内部接口说明。"""
        self.start()
        self.monitor()
        self.join()
        self.terminate()
        self.cleanup()

    def start(self):
        """项目内部接口说明。"""
        for worker in self.workers:
            for process in worker.processes:
                if not worker.auto_start:
                    process.set_state(ProcessState.NONE, True)
                    continue
                process.start()

    def join(self):
        """项目内部接口说明。"""
        logger.debug("Joining processes", extra={"verbosity": 1})
        joined = set()
        for process in self.processes:
            logger.debug(
                f"Found {process.pid} - {process.state.name}",
                extra={"verbosity": 1},
            )
            if process.state < ProcessState.JOINED:
                logger.debug(f"Joining {process.pid}", extra={"verbosity": 1})
                joined.add(process.pid)
                process.join()
        if joined:
            self.join()

    def terminate(self):
        """项目内部接口说明。"""
        if not self._shutting_down:
            for process in self.processes:
                process.terminate()

    def cleanup(self):
        """项目内部接口说明。"""
        for process in self.processes:
            process.exit()

    def restart(
        self,
        process_names: list[str] | None = None,
        restart_order=RestartOrder.SHUTDOWN_FIRST,
        **kwargs,
    ):
        """项目内部接口说明。"""
        self.restarter.restart(
            transient_processes=list(self.transient_processes),
            durable_processes=list(self.durable_processes),
            process_names=process_names,
            restart_order=restart_order,
            **kwargs,
        )

    def scale(self, num_worker: int):
        if num_worker <= 0:
            raise ValueError("Cannot scale to 0 workers.")

        change = num_worker - self.num_server
        if change == 0:
            logger.info(
                f"No change needed. There are already {num_worker} workers."
            )
            return

        logger.info(f"Scaling from {self.num_server} to {num_worker} workers")
        if change > 0:
            for _ in range(change):
                worker = self.create_server()
                for process in worker.processes:
                    process.start()
        else:
            for _ in range(abs(change)):
                self.shutdown_server()
        self.num_server = num_worker

    def monitor(self):
        """项目内部接口说明。"""
        self.wait_for_ack()
        while True:
            try:
                cycle = self._poll_monitor()
                if cycle is MonitorCycle.BREAK:
                    break
                elif cycle is MonitorCycle.CONTINUE:
                    continue
                self._sync_states()
                self._cleanup_non_tracked_workers()
            except InterruptedError:
                if not OS_IS_WINDOWS:
                    raise
                break

    def wait_for_ack(self):  # no cov
        """项目内部接口说明。"""
        misses = 0
        message = (
            "It seems that one or more of your workers failed to come "
            "online in the allowed time. Sanic is shutting down to avoid a "
            f"deadlock. The current threshold is {self.THRESHOLD / 10}s. "
            "If this problem persists, please check out the documentation "
            "https://sanic.dev/en/guide/deployment/manager.html#worker-ack."
        )
        while not self._all_workers_ack():
            if self.monitor_subscriber.poll(0.1):
                monitor_msg = self.monitor_subscriber.recv()
                if monitor_msg != "__TERMINATE_EARLY__":
                    self.monitor_publisher.send(monitor_msg)
                    continue
                misses = self.THRESHOLD
                message = (
                    "One of your worker processes terminated before startup "
                    "was completed. Please solve any errors experienced "
                    "during startup. If you do not see an exception traceback "
                    "in your error logs, try running Sanic in a single "
                    "process using --single-process or single_process=True. "
                    "Once you are confident that the server is able to start "
                    "without errors you can switch back to multiprocess mode."
                )
            misses += 1
            if misses > self.THRESHOLD:
                error_logger.error(
                    "Not all workers acknowledged a successful startup. "
                    "Shutting down.\n\n" + message
                )
                self.kill()

    @property
    def workers(self) -> list[Worker]:
        """项目内部接口说明。"""
        return list(self.transient.values()) + list(self.durable.values())

    @property
    def all_workers(self) -> Iterable[tuple[str, Worker]]:
        return chain(self.transient.items(), self.durable.items())

    @property
    def processes(self):
        """项目内部接口说明。"""
        for worker in self.workers:
            for process in worker.processes:
                if not process.pid:
                    continue
                yield process

    @property
    def transient_processes(self):
        """项目内部接口说明。"""
        for worker in self.transient.values():
            yield from worker.processes

    @property
    def durable_processes(self):
        for worker in self.durable.values():
            yield from worker.processes

    def kill(self):
        """项目内部接口说明。"""
        for process in self.processes:
            logger.info("Killing %s [%s]", process.name, process.pid)
            with suppress(ProcessLookupError):
                if os.name == "nt":
                    # Windows has no os.killpg and SIGKILL doesn't seem to work
                    os.kill(process.pid, signal.CTRL_C_EVENT)
                else:
                    try:
                        os.killpg(os.getpgid(process.pid), SIGKILL)
                    except OSError:
                        os.kill(process.pid, SIGKILL)
        raise ServerKilled

    def shutdown_signal(self, signal, frame):
        """项目内部接口说明。"""
        if self._shutting_down:
            logger.info("Shutdown interrupted. Killing.")
            with suppress(ServerKilled):
                self.kill()
            return

        logger.info("Received signal %s. Shutting down.", Signals(signal).name)
        self.monitor_publisher.send(None)
        self.shutdown()

    def shutdown(self):
        """项目内部接口说明。"""
        for process in self.processes:
            if process.is_alive():
                process.terminate()
        self._shutting_down = True

    def remove_worker(self, worker: Worker) -> None:
        if worker.tracked:
            error_logger.error(
                f"Worker {worker.name} is tracked and cannot be removed."
            )
            return
        if worker.has_alive_processes():
            error_logger.error(
                f"Worker {worker.name} has alive processes and cannot be "
                "removed."
            )
            return
        self.transient.pop(worker.name, None)
        self.durable.pop(worker.name, None)
        for process in worker.processes:
            self.worker_state.pop(process.name, None)
        logger.info("Removed worker %s", worker.name)
        del worker

    @property
    def pid(self):
        """项目内部接口说明。"""
        return os.getpid()

    def _all_workers_ack(self):
        acked = [
            worker_state.get("state") == ProcessState.ACKED.name
            for worker_state in self.worker_state.values()
            if worker_state.get("server")
        ]
        return all(acked) and len(acked) == self.num_server

    def _sync_states(self):
        for process in self.processes:
            try:
                state = self.worker_state[process.name].get("state")
            except KeyError:
                process.set_state(ProcessState.TERMINATED, True)
                continue
            # Skip state sync if process is restarting to avoid race condition
            if process.state == ProcessState.RESTARTING:
                continue
            if not process.is_alive():
                state = "FAILED" if process.exitcode else "COMPLETED"
            if state and process.state.name != state:
                process.set_state(ProcessState[state], True)

    def _cleanup_non_tracked_workers(self) -> None:
        to_remove = [
            worker
            for worker in self.workers
            if not worker.tracked and not worker.has_alive_processes()
        ]

        for worker in to_remove:
            self.remove_worker(worker)

    def _poll_monitor(self) -> MonitorCycle | None:
        if self.monitor_subscriber.poll(0.1):
            message = self.monitor_subscriber.recv()
            logger.debug(f"Monitor message: {message}", extra={"verbosity": 2})
            if not message:
                return MonitorCycle.BREAK
            elif message == "__TERMINATE__":
                self._handle_terminate()
                return MonitorCycle.BREAK
            elif isinstance(message, tuple) and (
                len(message) == 7 or len(message) == 8
            ):
                self._handle_manage(*message)  # type: ignore
                return MonitorCycle.CONTINUE
            elif not isinstance(message, str):
                error_logger.error(
                    "Monitor received an invalid message: %s", message
                )
                return MonitorCycle.CONTINUE
            return self._handle_message(message)
        return None

    def _handle_terminate(self) -> None:
        self.shutdown()

    def _handle_message(self, message: str) -> MonitorCycle | None:
        logger.debug(
            "Incoming monitor message: %s",
            message,
            extra={"verbosity": 1},
        )
        split_message = message.split(":", 2)
        if message.startswith("__SCALE__"):
            self.scale(int(split_message[-1]))
            return MonitorCycle.CONTINUE

        processes = split_message[0]
        reloaded_files = split_message[1] if len(split_message) > 1 else None
        process_names: list[str] | None = [
            name.strip() for name in processes.split(",")
        ]
        if process_names and "__ALL_PROCESSES__" in process_names:
            process_names = None
        order = (
            RestartOrder.STARTUP_FIRST
            if "STARTUP_FIRST" in split_message
            else RestartOrder.SHUTDOWN_FIRST
        )
        self.restart(
            process_names=process_names,
            reloaded_files=reloaded_files,
            restart_order=order,
        )

        return None

    def _handle_manage(
        self,
        name: str,
        func: Callable[..., Any],
        kwargs: dict[str, Any],
        transient: bool,
        restartable: bool | None,
        tracked: bool,
        auto_start: bool,
        workers: int,
    ) -> None:
        try:
            worker = self.manage(
                name,
                func,
                kwargs,
                transient=transient,
                restartable=restartable,
                tracked=tracked,
                auto_start=auto_start,
                workers=workers,
            )
        except Exception:
            error_logger.exception("Failed to manage worker %s", name)
        else:
            if not auto_start:
                return
            for process in worker.processes:
                process.start()
