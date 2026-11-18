"""Simulated Battery Management System ECU.

``BmsLogic`` is the pure, clock-driven application logic: feed it received
frames and call ``tick`` once per cycle. ``BmsNode`` wraps it in a real-time
thread attached to a python-can bus, so it behaves like a separate ECU.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

import can

from canbench import protocol
from canbench.pack import BatteryPack
from canbench.protocol import FAULT_SIGNALS, BmsState, PlantCommand, RequestedState

log = logging.getLogger(__name__)

TX_MESSAGES = ("BMS_Status", "BMS_CellStats", "BMS_Faults")


@dataclass
class BmsConfig:
    cycle_ms: int = 100
    over_temp_c: float = 60.0
    under_voltage_v: float = 2.8
    over_voltage_v: float = 4.25
    over_current_a: float = 500.0
    plausible_temp_c: tuple[float, float] = (-40.0, 120.0)
    plausible_voltage_v: tuple[float, float] = (0.5, 5.0)
    debounce_cycles: int = 3
    comm_timeout_ms: int = 500


@dataclass
class BmsLogic:
    pack: BatteryPack = field(default_factory=BatteryPack)
    config: BmsConfig = field(default_factory=BmsConfig)

    def __post_init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.pack.reset()
        self.state = BmsState.INIT
        self.faults = {name: False for name in FAULT_SIGNALS}
        self.conditions = {name: False for name in FAULT_SIGNALS}
        self._debounce = {name: 0 for name in FAULT_SIGNALS}
        self._tx_counters = {name: 0 for name in TX_MESSAGES}
        self.requested_state = RequestedState.NONE
        self.clear_faults_requested = False
        self.requested_load_a = 0.0
        self.last_vcu_ms: float | None = None
        self._last_vcu_counter: int | None = None
        self.rejected_frames = 0

    @property
    def contactors_closed(self) -> bool:
        return self.state in (BmsState.DRIVE, BmsState.CHARGE)

    def vcu_comm_ok(self, now_ms: float) -> bool:
        return self.last_vcu_ms is not None and now_ms - self.last_vcu_ms <= self.config.comm_timeout_ms

    # ------------------------------------------------------------------ rx

    def handle_frame(self, raw: can.Message, now_ms: float) -> None:
        frame = protocol.decode(raw)
        if frame is None:
            return
        if frame.name == "VCU_Command":
            self._handle_vcu(frame, now_ms)
        elif frame.name == "SIM_PlantControl":
            self._handle_plant(frame)

    def _handle_vcu(self, frame: protocol.DecodedFrame, now_ms: float) -> None:
        # A repeated counter means the sender is stuck; treat it like a lost frame.
        if not frame.checksum_ok or frame.counter == self._last_vcu_counter:
            self.rejected_frames += 1
            return
        self._last_vcu_counter = frame.counter
        self.last_vcu_ms = now_ms
        try:
            self.requested_state = RequestedState(int(frame["RequestedState"]))
        except ValueError:
            self.requested_state = RequestedState.NONE
        self.clear_faults_requested = bool(frame["ClearFaults"])

    def _handle_plant(self, frame: protocol.DecodedFrame) -> None:
        if not frame.checksum_ok:
            self.rejected_frames += 1
            return
        command = int(frame["Command"])
        index = int(frame["CellIndex"])
        value = float(frame["Value"])
        try:
            if command == PlantCommand.SET_LOAD_CURRENT:
                self.requested_load_a = value
            elif command == PlantCommand.SET_CELL_TEMP:
                self.pack.inject_cell_temp(index, value)
            elif command == PlantCommand.SET_CELL_VOLTAGE:
                self.pack.inject_cell_voltage(index, value)
            elif command == PlantCommand.OPEN_TEMP_SENSOR:
                self.pack.open_temp_sensor(index)
            elif command == PlantCommand.CLEAR_INJECTIONS:
                self.pack.clear_injections()
                self.requested_load_a = 0.0
            elif command == PlantCommand.RESET:
                self.reset()
        except IndexError:
            log.warning("ignoring plant command %s for invalid cell %d", command, index)

    # ---------------------------------------------------------------- tick

    def tick(self, now_ms: float) -> list[can.Message]:
        self.pack.load_current_a = self.requested_load_a if self.contactors_closed else 0.0
        self.pack.step(self.config.cycle_ms / 1000.0)
        self._evaluate_faults(now_ms)
        self._update_state(now_ms)
        return self._compose()

    def _evaluate_faults(self, now_ms: float) -> None:
        cfg = self.config
        temps = self.pack.measured_cell_temps()
        volts = self.pack.measured_cell_voltages()
        t_lo, t_hi = cfg.plausible_temp_c
        v_lo, v_hi = cfg.plausible_voltage_v
        valid_temps = [t for t in temps if t_lo <= t <= t_hi]
        valid_volts = [v for v in volts if v_lo <= v <= v_hi]

        # Implausible readings raise SensorFault and are excluded from the
        # threshold checks so a broken wire is not reported as a thermal event.
        self.conditions = {
            "OverTemp": any(t > cfg.over_temp_c for t in valid_temps),
            "UnderVoltage": any(v < cfg.under_voltage_v for v in valid_volts),
            "OverVoltage": any(v > cfg.over_voltage_v for v in valid_volts),
            "OverCurrent": abs(self.pack.load_current_a) > cfg.over_current_a,
            "CommLoss": self.last_vcu_ms is not None and not self.vcu_comm_ok(now_ms),
            "SensorFault": len(valid_temps) < len(temps) or len(valid_volts) < len(volts),
        }

        for name, active in self.conditions.items():
            self._debounce[name] = self._debounce[name] + 1 if active else 0
            threshold = 1 if name == "CommLoss" else cfg.debounce_cycles
            if self._debounce[name] >= threshold and not self.faults[name]:
                log.info("fault latched: %s", name)
                self.faults[name] = True

    def _update_state(self, now_ms: float) -> None:
        if self.state == BmsState.FAULT and self.clear_faults_requested and not any(self.conditions.values()):
            log.info("faults cleared by VCU")
            self.faults = {name: False for name in FAULT_SIGNALS}
            self._debounce = {name: 0 for name in FAULT_SIGNALS}
            self.state = BmsState.STANDBY
            return

        if any(self.faults.values()):
            self.state = BmsState.FAULT
            return
        if self.state == BmsState.INIT:
            self.state = BmsState.STANDBY
            return
        if not self.vcu_comm_ok(now_ms):
            return

        transitions = {
            RequestedState.STANDBY: BmsState.STANDBY,
            RequestedState.DRIVE: BmsState.DRIVE,
            RequestedState.CHARGE: BmsState.CHARGE,
        }
        target = transitions.get(self.requested_state)
        if target is None or target == self.state:
            return
        # Switching directly between DRIVE and CHARGE with contactors closed is
        # not allowed; the vehicle must pass through STANDBY.
        if self.contactors_closed and target != BmsState.STANDBY:
            return
        log.info("state %s -> %s", self.state.name, target.name)
        self.state = target

    def _next_counter(self, name: str) -> int:
        value = self._tx_counters[name]
        self._tx_counters[name] = (value + 1) % protocol.COUNTER_MODULO
        return value

    def _compose(self) -> list[can.Message]:
        volts = self.pack.measured_cell_voltages()
        temps = self.pack.measured_cell_temps()
        pack_voltage = sum(self.pack.true_cell_voltage(i) for i in range(len(volts)))
        return [
            protocol.encode(
                "BMS_Status",
                {
                    "PackVoltage": pack_voltage,
                    "PackCurrent": self.pack.load_current_a,
                    "SOC": self.pack.soc * 100.0,
                    "BMS_State": int(self.state),
                    "ContactorsClosed": int(self.contactors_closed),
                },
                self._next_counter("BMS_Status"),
            ),
            protocol.encode(
                "BMS_CellStats",
                {
                    "MaxCellVoltage": max(volts),
                    "MinCellVoltage": min(volts),
                    "MaxCellTemp": max(temps),
                    "MinCellTemp": min(temps),
                },
                self._next_counter("BMS_CellStats"),
            ),
            protocol.encode(
                "BMS_Faults",
                {name: int(v) for name, v in self.faults.items()},
                self._next_counter("BMS_Faults"),
            ),
        ]


class BmsNode:
    """Runs ``BmsLogic`` in real time on a background thread."""

    def __init__(self, bus: can.BusABC, logic: BmsLogic | None = None) -> None:
        self.bus = bus
        self.logic = logic or BmsLogic()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="bms-node", daemon=True)
        self._t0 = 0.0

    def _now_ms(self) -> float:
        return (time.monotonic() - self._t0) * 1000.0

    def start(self) -> BmsNode:
        self._t0 = time.monotonic()
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)

    def __enter__(self) -> BmsNode:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()

    def _run(self) -> None:
        cycle_s = self.logic.config.cycle_ms / 1000.0
        # Absolute deadlines keep the cycle time from drifting.
        deadline = time.monotonic()
        while not self._stop.is_set():
            now = time.monotonic()
            if now >= deadline:
                for msg in self.logic.tick(self._now_ms()):
                    try:
                        self.bus.send(msg)
                    except can.CanError as exc:
                        log.warning("tx failed: %s", exc)
                deadline += cycle_s
                if deadline < now:
                    deadline = now + cycle_s
                continue
            frame = self.bus.recv(timeout=min(deadline - now, 0.05))
            if frame is not None:
                self.logic.handle_frame(frame, self._now_ms())
