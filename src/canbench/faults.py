"""Frame-level fault injection for frames the test harness transmits."""

from __future__ import annotations

import threading
from enum import Enum

import can

from canbench import protocol


class FrameFault(Enum):
    DROP = "drop"
    CORRUPT_CHECKSUM = "corrupt_checksum"
    FREEZE_COUNTER = "freeze_counter"


class FrameFaultInjector:
    """Mutates or suppresses outgoing frames according to active rules.

    Each rule applies to the next ``count`` frames, or indefinitely when
    ``count`` is ``None``, until ``clear`` is called.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rules: dict[FrameFault, int | None] = {}
        self._frozen_counter: int | None = None
        self.injected = {fault: 0 for fault in FrameFault}

    def drop(self, count: int | None = None) -> None:
        self._set(FrameFault.DROP, count)

    def corrupt_checksum(self, count: int | None = None) -> None:
        self._set(FrameFault.CORRUPT_CHECKSUM, count)

    def freeze_counter(self, count: int | None = None) -> None:
        self._set(FrameFault.FREEZE_COUNTER, count)

    def clear(self) -> None:
        with self._lock:
            self._rules.clear()
            self._frozen_counter = None

    @property
    def active(self) -> bool:
        with self._lock:
            return bool(self._rules)

    def _set(self, fault: FrameFault, count: int | None) -> None:
        if count is not None and count < 1:
            raise ValueError("count must be >= 1 or None")
        with self._lock:
            self._rules[fault] = count

    def _consume(self, fault: FrameFault) -> bool:
        if fault not in self._rules:
            return False
        remaining = self._rules[fault]
        if remaining is not None:
            if remaining <= 1:
                del self._rules[fault]
            else:
                self._rules[fault] = remaining - 1
        self.injected[fault] += 1
        return True

    def apply(self, msg: can.Message) -> can.Message | None:
        """Return the frame to send, or ``None`` if it should be dropped."""
        with self._lock:
            if self._consume(FrameFault.DROP):
                return None

            frame = protocol.decode(msg)
            if frame is None:
                return msg

            if FrameFault.FREEZE_COUNTER in self._rules:
                if self._frozen_counter is None:
                    self._frozen_counter = frame.counter
                if self._consume(FrameFault.FREEZE_COUNTER):
                    msg = protocol.encode(frame.name, frame.signals, self._frozen_counter)
                if FrameFault.FREEZE_COUNTER not in self._rules:
                    self._frozen_counter = None

            if self._consume(FrameFault.CORRUPT_CHECKSUM):
                data = bytearray(msg.data)
                data[protocol.CHECKSUM_BYTE] ^= 0x5A
                msg = can.Message(arbitration_id=msg.arbitration_id, data=bytes(data), is_extended_id=False)
            return msg
