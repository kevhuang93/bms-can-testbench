"""CAN message definitions and end-to-end (E2E) protection helpers.

Every frame on this bus carries a 4-bit rolling counter and an 8-bit checksum
in the last two bytes, a simplified version of the AUTOSAR E2E pattern used in
production vehicles to detect lost, repeated and corrupted frames.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import IntEnum
from functools import lru_cache
from importlib import resources

import can
import cantools
from cantools.database.can import Database, Message

COUNTER_MODULO = 16
CHECKSUM_BYTE = 7


class BmsState(IntEnum):
    INIT = 0
    STANDBY = 1
    DRIVE = 2
    CHARGE = 3
    FAULT = 4


class RequestedState(IntEnum):
    NONE = 0
    STANDBY = 1
    DRIVE = 2
    CHARGE = 3


class PlantCommand(IntEnum):
    NOP = 0
    SET_LOAD_CURRENT = 1
    SET_CELL_TEMP = 2
    SET_CELL_VOLTAGE = 3
    OPEN_TEMP_SENSOR = 4
    CLEAR_INJECTIONS = 5
    RESET = 6


FAULT_SIGNALS = (
    "OverTemp",
    "UnderVoltage",
    "OverVoltage",
    "OverCurrent",
    "CommLoss",
    "SensorFault",
)


@lru_cache(maxsize=1)
def database() -> Database:
    dbc = resources.files("canbench").joinpath("dbc/bms.dbc")
    with resources.as_file(dbc) as path:
        return cantools.database.load_file(str(path))


def message(name: str) -> Message:
    return database().get_message_by_name(name)


def compute_checksum(arbitration_id: int, data: bytes) -> int:
    """Checksum over the frame ID and payload bytes 0..6, inverted."""
    total = (arbitration_id & 0xFF) + ((arbitration_id >> 8) & 0xFF) + sum(data[:CHECKSUM_BYTE])
    return (total & 0xFF) ^ 0xFF


def _clamp(msg: Message, signals: Mapping[str, float]) -> dict[str, float]:
    clamped = {}
    for sig in msg.signals:
        value = signals.get(sig.name, 0)
        if sig.minimum is not None:
            value = max(sig.minimum, value)
        if sig.maximum is not None:
            value = min(sig.maximum, value)
        clamped[sig.name] = value
    return clamped


def encode(name: str, signals: Mapping[str, float], counter: int) -> can.Message:
    """Encode a frame, filling in the rolling counter and checksum.

    Physical values outside the DBC range are saturated, as an ECU would do.
    """
    msg = message(name)
    values = _clamp(msg, {**signals, "Counter": counter % COUNTER_MODULO, "Checksum": 0})
    data = bytearray(msg.encode(values, strict=False))
    data[CHECKSUM_BYTE] = compute_checksum(msg.frame_id, bytes(data))
    return can.Message(arbitration_id=msg.frame_id, data=bytes(data), is_extended_id=False)


@dataclass(frozen=True)
class DecodedFrame:
    name: str
    arbitration_id: int
    timestamp: float
    data: bytes
    signals: dict[str, float]

    @property
    def counter(self) -> int:
        return int(self.signals["Counter"])

    @property
    def checksum_ok(self) -> bool:
        return self.data[CHECKSUM_BYTE] == compute_checksum(self.arbitration_id, self.data)

    def __getitem__(self, signal: str) -> float:
        return self.signals[signal]


def decode(frame: can.Message) -> DecodedFrame | None:
    """Decode a raw frame. Returns ``None`` for IDs not in the DBC."""
    try:
        msg = database().get_message_by_frame_id(frame.arbitration_id)
    except KeyError:
        return None
    data = bytes(frame.data)
    signals = msg.decode(data, decode_choices=False, scaling=True)
    return DecodedFrame(
        name=msg.name,
        arbitration_id=frame.arbitration_id,
        timestamp=frame.timestamp,
        data=data,
        signals=dict(signals),
    )


def counter_step_ok(previous: int, current: int) -> bool:
    return current == (previous + 1) % COUNTER_MODULO
