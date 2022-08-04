import os

from signal import SIGINT, SIGTERM, Signals
from signal import signal as signal_func
from typing import List, Optional

from sanic.log import logger
from sanic.worker.process import ProcessState, Worker


class WorkerManager:
    def __init__(
        self,
        number: int,
        serve,
        server_settings,
        context,
        restart_pubsub,
        worker_state,
    ):
        self.context = context
        self.transient: List[Worker] = []
        self.durable: List[Worker] = []
        self.restart_publisher, self.restart_subscriber = restart_pubsub
        self.worker_state = worker_state
        self.worker_state["Sanic-Main"] = {"pid": self.pid}

        for i in range(number):
            self.manage(f"Worker-{i}", serve, server_settings, transient=True)

        signal_func(SIGINT, self.kill)
        signal_func(SIGTERM, self.kill)

    def manage(self, ident, func, kwargs, transient=False):
        container = self.transient if transient else self.durable
        container.append(
            Worker(ident, func, kwargs, self.context, self.worker_state)
        )

    def run(self):
        self.start()
        self.monitor()
        self.join()
        self.terminate()

    def start(self):
        for process in self.processes:
            process.start()

    def join(self):
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
        for process in self.processes:
            process.terminate()

    def restart(self, process_names: Optional[List[str]] = None, **kwargs):
        for process in self.transient_processes:
            if not process_names or process.name in process_names:
                process.restart(**kwargs)

    def monitor(self):
        while True:
            if self.restart_subscriber.poll(0.1):
                message = self.restart_subscriber.recv()
                if not message:
                    break
                processes, *reloaded_files = message.split(":", 1)
                process_names = [name.strip() for name in processes.split(",")]
                if "__ALL_PROCESSES__" in process_names:
                    process_names = None
                self.restart(
                    process_names=process_names, reloaded_files=reloaded_files
                )

    @property
    def workers(self):
        return self.transient + self.durable

    @property
    def processes(self):
        for worker in self.workers:
            for process in worker.processes:
                yield process

    @property
    def transient_processes(self):
        for worker in self.transient:
            for process in worker.processes:
                yield process

    def kill(self, signal, frame):
        self.restart_publisher.send(None)
        logger.info("Received signal %s. Shutting down.", Signals(signal).name)
        for process in self.processes:
            if process.is_alive():
                os.kill(process.pid, SIGTERM)

    @property
    def pid(self):
        return os.getpid()