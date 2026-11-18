"""Simulated Vehicle Control Unit that sends the BMS its heartbeat/command."""

from __future__ import annotations

import threading
import time

import can

from canbench import protocol
from canbench.faults import FrameFaultInjector
from canbench.protocol import RequestedState

VCU_CYCLE_S = 0.05


class VcuSimulator:
    def __init__(self, bus: can.BusABC, cycle_s: float = VCU_CYCLE_S) -> None:
        self.bus = bus
        self.cycle_s = cycle_s
        self.requested_state = RequestedState.STANDBY
        self.clear_faults = False
        self.faults = FrameFaultInjector()
        self.sent = 0
        self._counter = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="vcu-sim", daemon=True)

    def start(self) -> VcuSimulator:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)

    def _run(self) -> None:
        deadline = time.monotonic()
        while not self._stop.is_set():
            msg = protocol.encode(
                "VCU_Command",
                {
                    "RequestedState": int(self.requested_state),
                    "ClearFaults": int(self.clear_faults),
                },
                self._counter,
            )
            self._counter = (self._counter + 1) % protocol.COUNTER_MODULO
            out = self.faults.apply(msg)
            if out is not None:
                self.bus.send(out)
                self.sent += 1
            deadline += self.cycle_s
            self._stop.wait(max(0.0, deadline - time.monotonic()))
