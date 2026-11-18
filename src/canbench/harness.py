"""High-level test bench that wires the BMS target, VCU, plant control and recorder."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

import can

from canbench.bms import BmsNode
from canbench.monitor import BusRecorder
from canbench.plant import PlantControl
from canbench.protocol import BmsState, DecodedFrame, RequestedState
from canbench.vcu import VcuSimulator


@dataclass
class BenchConfig:
    interface: str = "virtual"
    channel: str | None = None
    """Defaults to a unique virtual channel so parallel benches never interfere."""
    start_simulator: bool = True
    """False when the BMS is an external process or hardware already on the bus."""


class Testbench:
    __test__ = False  # not a pytest test class

    def __init__(self, config: BenchConfig | None = None) -> None:
        self.config = config or BenchConfig()
        channel = self.config.channel or f"bench-{uuid.uuid4().hex[:8]}"
        self.channel = channel
        self._buses: list[can.BusABC] = []
        self.sim: BmsNode | None = None

        if self.config.start_simulator:
            self.sim = BmsNode(self._open_bus())
        self.recorder = BusRecorder(self._open_bus())
        harness_bus = self._open_bus()
        self.vcu = VcuSimulator(harness_bus)
        self.plant = PlantControl(harness_bus)

    def _open_bus(self) -> can.BusABC:
        bus = can.Bus(interface=self.config.interface, channel=self.channel)
        self._buses.append(bus)
        return bus

    def start(self) -> Testbench:
        since = time.time()
        if self.sim is not None:
            self.sim.start()
        else:
            self.plant.reset()
        self.vcu.start()
        self.wait_for_state(BmsState.STANDBY, timeout=3.0, since=since)
        return self

    def stop(self) -> None:
        self.vcu.stop()
        if self.sim is not None:
            self.sim.stop()
        self.recorder.close()
        for bus in self._buses:
            bus.shutdown()

    def __enter__(self) -> Testbench:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()

    # ------------------------------------------------------------ helpers

    def status(self) -> DecodedFrame:
        frame = self.recorder.latest("BMS_Status")
        if frame is None:
            frame = self.recorder.wait_for("BMS_Status")
        return frame

    def faults(self) -> dict[str, bool]:
        frame = self.recorder.latest("BMS_Faults") or self.recorder.wait_for("BMS_Faults")
        return {k: bool(v) for k, v in frame.signals.items() if k not in ("Counter", "Checksum")}

    def wait_for_state(
        self, state: BmsState, timeout: float = 2.0, since: float | None = None
    ) -> DecodedFrame:
        return self.recorder.wait_for(
            "BMS_Status", lambda f: int(f["BMS_State"]) == state, timeout=timeout, since=since
        )

    def wait_for_fault(self, fault: str, timeout: float = 2.0, since: float | None = None) -> DecodedFrame:
        return self.recorder.wait_for("BMS_Faults", lambda f: bool(f[fault]), timeout=timeout, since=since)

    def request(self, state: RequestedState, timeout: float = 2.0) -> DecodedFrame:
        """Ask the BMS for a state via the VCU heartbeat and wait for it."""
        since = time.time()
        self.vcu.requested_state = state
        return self.wait_for_state(BmsState[state.name], timeout=timeout, since=since)

    def clear_faults(self, timeout: float = 2.0) -> DecodedFrame:
        since = time.time()
        self.vcu.requested_state = RequestedState.STANDBY
        self.vcu.clear_faults = True
        try:
            return self.wait_for_state(BmsState.STANDBY, timeout=timeout, since=since)
        finally:
            self.vcu.clear_faults = False
