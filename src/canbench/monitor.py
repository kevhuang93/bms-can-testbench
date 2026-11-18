"""Bus recording, waiting helpers and end-to-end protection checks."""

from __future__ import annotations

import statistics
import threading
import time
from collections.abc import Callable, Iterable

import can

from canbench import protocol
from canbench.protocol import DecodedFrame

Predicate = Callable[[DecodedFrame], bool]


class BusRecorder(can.Listener):
    """Records every decodable frame seen on a bus, with its timestamp."""

    def __init__(self, bus: can.BusABC) -> None:
        self.bus = bus
        self._frames: list[DecodedFrame] = []
        self._cond = threading.Condition()
        self._notifier = can.Notifier(bus, [self], timeout=0.05)

    def on_message_received(self, msg: can.Message) -> None:
        frame = protocol.decode(msg)
        if frame is None:
            return
        with self._cond:
            self._frames.append(frame)
            self._cond.notify_all()

    def close(self) -> None:
        self._notifier.stop()

    def clear(self) -> None:
        with self._cond:
            self._frames.clear()

    def frames(self, name: str | None = None, since: float | None = None) -> list[DecodedFrame]:
        with self._cond:
            return [
                f
                for f in self._frames
                if (name is None or f.name == name) and (since is None or f.timestamp >= since)
            ]

    def latest(self, name: str) -> DecodedFrame | None:
        with self._cond:
            return self._latest_unlocked(name)

    def wait_for(
        self,
        name: str,
        predicate: Predicate = lambda f: True,
        timeout: float = 2.0,
        since: float | None = None,
    ) -> DecodedFrame:
        """Block until a ``name`` frame satisfying ``predicate`` arrives.

        Frames already recorded at or after ``since`` count, so calling this
        right after triggering a stimulus cannot miss a fast response.
        """
        end = time.monotonic() + timeout
        checked = 0
        with self._cond:
            while True:
                candidates = [
                    f
                    for f in self._frames[checked:]
                    if f.name == name and (since is None or f.timestamp >= since)
                ]
                checked = len(self._frames)
                for frame in candidates:
                    if predicate(frame):
                        return frame
                remaining = end - time.monotonic()
                if remaining <= 0:
                    last = self._latest_unlocked(name)
                    raise TimeoutError(
                        f"no {name} frame matched within {timeout:.2f}s; "
                        f"last seen: {last.signals if last else 'nothing'}"
                    )
                self._cond.wait(remaining)

    def _latest_unlocked(self, name: str) -> DecodedFrame | None:
        for frame in reversed(self._frames):
            if frame.name == name:
                return frame
        return None

    def collect(self, name: str, duration: float) -> list[DecodedFrame]:
        """Record ``name`` frames for ``duration`` seconds and return them."""
        start = time.time()
        time.sleep(duration)
        return self.frames(name, since=start)


def periods_ms(frames: Iterable[DecodedFrame]) -> list[float]:
    stamps = [f.timestamp for f in frames]
    return [(b - a) * 1000.0 for a, b in zip(stamps, stamps[1:], strict=False)]


def period_stats(frames: Iterable[DecodedFrame]) -> dict[str, float]:
    p = periods_ms(frames)
    if not p:
        raise ValueError("need at least two frames to compute a period")
    return {
        "count": len(p),
        "mean": statistics.fmean(p),
        "median": statistics.median(p),
        "min": min(p),
        "max": max(p),
        "stdev": statistics.pstdev(p),
    }


def e2e_violations(frames: Iterable[DecodedFrame]) -> list[str]:
    """Return human-readable E2E protection errors for a stream of one message."""
    problems = []
    previous: DecodedFrame | None = None
    for frame in frames:
        if not frame.checksum_ok:
            problems.append(f"{frame.name} @ {frame.timestamp:.3f}: bad checksum")
        if previous is not None and not protocol.counter_step_ok(previous.counter, frame.counter):
            problems.append(
                f"{frame.name} @ {frame.timestamp:.3f}: counter {previous.counter} -> {frame.counter}"
            )
        previous = frame
    return problems
