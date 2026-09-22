"""One event loop per UI session; every MAF client lives and closes on it."""

from __future__ import annotations

import asyncio
from concurrent.futures import Future
from threading import Event, Thread


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
        self.active: Future | None = None

    def submit(self, coroutine) -> Future:
        if self.loop.is_closed():
            coroutine.close()
            raise RuntimeError("Runtime is closed. Resume the saved run with a new runtime.")
        if self.active and not self.active.done():
            coroutine.close()
            raise RuntimeError("An operation is already running.")
        self.active = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        return self.active

    def close(self):
        if not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=15)
