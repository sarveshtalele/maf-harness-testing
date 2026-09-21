"""Daily token ledger: the harness's only spending limit.

All runs on this machine (CLI and UI, sequential or concurrent) share one counter per local calendar day, stored as a
small JSON file and updated under an exclusive file lock. A model call *reserves* its worst-case tokens before it is
made (so parallel runs cannot overshoot) and is settled to the provider-reported usage afterwards. Counting starts
when this ledger is first written; earlier spend is not reconstructed.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from filelock import FileLock

from .settings import DAILY_TOKEN_LIMIT


class DailyLimitError(ValueError):
    """The daily token cap would be exceeded by the next model call."""


class DailyUsageLedger:
    def __init__(
        self,
        directory: Path | None = None,
        limit: int = DAILY_TOKEN_LIMIT,
        today: Callable[[], date] = date.today,
    ):
        self.directory = Path(
            directory or os.getenv("HARNESS_USAGE_DIR") or Path.home() / ".maf-harness" / "usage"
        )
        self.limit, self._today = limit, today

    @contextmanager
    def _locked(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        with FileLock(str(self.directory / ".lock")):
            yield

    def _path(self, day: str) -> Path:
        return self.directory / f"{day}.json"

    def _read(self, day: str) -> dict:
        path = self._path(day)
        return (
            json.loads(path.read_text(encoding="utf-8"))
            if path.is_file()
            else {"date": day, "tokens": 0, "calls": 0}
        )

    def _write(self, data: dict) -> None:
        path = self._path(data["date"])
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(data), encoding="utf-8")
        temp.replace(path)

    def snapshot(self) -> dict:
        day = self._today().isoformat()
        with self._locked():
            used = self._read(day)["tokens"]
        return {"date": day, "used": used, "limit": self.limit, "remaining": max(0, self.limit - used)}

    def reserve(self, tokens: int) -> dict:
        """Claim ``tokens`` for one call; raises DailyLimitError when the day's cap would be exceeded."""
        day = self._today().isoformat()
        with self._locked():
            data = self._read(day)
            if data["tokens"] + tokens > self.limit:
                raise DailyLimitError(
                    f"Daily token cap of {self.limit:,} reached ({data['tokens']:,} used on {day}; this call needs "
                    f"up to {tokens:,}). The cap resets at local midnight."
                )
            data["tokens"] += tokens
            data["calls"] += 1
            self._write(data)
        return {"day": day, "tokens": tokens}

    def settle(self, reservation: dict, actual: int) -> None:
        """Replace the reserved amount with what the provider actually reported (never below zero)."""
        with self._locked():
            data = self._read(reservation["day"])
            data["tokens"] = max(0, data["tokens"] + int(actual) - reservation["tokens"])
            self._write(data)
