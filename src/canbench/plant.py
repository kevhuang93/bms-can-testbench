"""Client for the SIM_PlantControl message used to drive the battery model.

Sending plant commands over CAN (instead of calling the model directly) keeps
the tests black-box, so the same suite can run against a simulator in another
process or a hardware-in-the-loop rig.
"""

from __future__ import annotations

import can

from canbench import protocol
from canbench.protocol import PlantCommand


class PlantControl:
    def __init__(self, bus: can.BusABC) -> None:
        self.bus = bus
        self._counter = 0

    def _send(self, command: PlantCommand, cell: int = 0, value: float = 0.0) -> None:
        msg = protocol.encode(
            "SIM_PlantControl",
            {"Command": int(command), "CellIndex": cell, "Value": value},
            self._counter,
        )
        self._counter = (self._counter + 1) % protocol.COUNTER_MODULO
        self.bus.send(msg)

    def set_load_current(self, amps: float) -> None:
        """Positive = discharge, negative = charge. Flows only with contactors closed."""
        self._send(PlantCommand.SET_LOAD_CURRENT, value=amps)

    def set_cell_temp(self, cell: int, temperature_c: float) -> None:
        self._send(PlantCommand.SET_CELL_TEMP, cell, temperature_c)

    def set_cell_voltage(self, cell: int, voltage_v: float) -> None:
        self._send(PlantCommand.SET_CELL_VOLTAGE, cell, voltage_v)

    def open_temp_sensor(self, cell: int) -> None:
        self._send(PlantCommand.OPEN_TEMP_SENSOR, cell)

    def clear_injections(self) -> None:
        self._send(PlantCommand.CLEAR_INJECTIONS)

    def reset(self) -> None:
        self._send(PlantCommand.RESET)
