"""One event loop per UI session; every MAF client lives and closes on it."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from concurrent.futures import Future
from threading import Event, Thread

from ..envs.platform_compat import RUN_CANCEL

STOP_WAIT_SECONDS = (
    30  # docs/USER_GUIDE.md: a stop that a cleanup never finishes must not hold the run forever
)


class RuntimeService:
    def __init__(self):
        self.loop = asyncio.new_event_loop()
        ready = Event()

        def run():
            asyncio.set_event_loop(self.loop)
            ready.set()
            self.loop.run_forever()
            pending = asyncio.all_tasks(self.loop)
            for task in pending:
                task.cancel()
            if pending:
                self.loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self.loop.run_until_complete(self.loop.shutdown_asyncgens())
            self.loop.run_until_complete(self.loop.shutdown_default_executor())
            self.loop.close()

        self.thread = Thread(target=run, name="maf-runtime", daemon=True)
        self.thread.start()
        ready.wait()
        self.active: Future | None = None  # the last submitted operation, or the Stop that is waiting for it
        self.task = None  # asyncio task of the running operation (set on the loop)
        self.idle = Event()  # set once the submitted operation has really ended (cancelled or finished)
        self.idle.set()
        self.stop_requested = False  # the running operation was asked to stop
        self.cancel_event = Event()

    def submit(self, coroutine) -> Future:
        if self.loop.is_closed():
            coroutine.close()
            raise RuntimeError("Runtime is closed. Resume the saved run with a new runtime.")
        if (
            not self.idle.is_set()
        ):  # the previous task has not really ended (a Stop may still be waiting for it)
            coroutine.close()
            if self.stop_requested:
                raise RuntimeError(
                    "An operation is already running: the previous one is still finishing after Stop and has not "
                    "ended yet. Wait until the run shows as stopped before starting new work."
                )
            raise RuntimeError("An operation is already running.")
        if self.active and not self.active.done():
            coroutine.close()
            raise RuntimeError("An operation is already running.")
        self.cancel_event = Event()
        self.idle.clear()
        self.stop_requested = False
        self.task = None

        async def owned():
            self.task = asyncio.current_task()
            token = RUN_CANCEL.set(self.cancel_event)
            try:
                return await coroutine
            finally:
                RUN_CANCEL.reset(token)

        operation = asyncio.run_coroutine_threadsafe(owned(), self.loop)
        operation.add_done_callback(lambda _: self.idle.set())
        self.active = operation
        return operation

    def stop_active(
        self, on_stopped: Callable[[], None], on_still_running: Callable[[], None] | None = None
    ) -> Future:
        """Cancel the running operation. ``on_stopped`` runs only once the task has really ended. When it ignores
        cancellation for STOP_WAIT_SECONDS, ``on_still_running`` runs (on the loop) and the wait goes on: the run stays
        "stopping", ``submit`` refuses new work, and a later ``cancel_again`` still reaches the task."""
        self.cancel_event.set()
        self.stop_requested = True

        async def stop():
            task = self.task
            if task and not task.done():
                task.cancel()
                done, _ = await asyncio.wait({task}, timeout=STOP_WAIT_SECONDS)
                if not done:
                    logging.getLogger(__name__).warning(
                        "Operation ignored cancellation for %s s; it is still finishing", STOP_WAIT_SECONDS
                    )
                    if on_still_running:
                        try:
                            on_still_running()
                        except Exception:  # a status note must never let the wait end early
                            logging.getLogger(__name__).exception("Could not record the still-finishing note")
                    await asyncio.wait({task})
            if task and task.done() and not task.cancelled():
                task.exception()  # mark retrieved; a failure caused by the stop is not an error
            on_stopped()

        self.active = asyncio.run_coroutine_threadsafe(stop(), self.loop)
        return self.active

    def cancel_again(self) -> None:
        """A repeated Stop while the task is still finishing: set the cancel flag and cancel the task once more, since a
        task may have swallowed the first cancellation."""
        self.cancel_event.set()
        if self.loop.is_closed():
            return

        def again():
            task = self.task
            if task and not task.done():
                task.cancel()

        self.loop.call_soon_threadsafe(again)

    def close(self):
        if not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=15)
