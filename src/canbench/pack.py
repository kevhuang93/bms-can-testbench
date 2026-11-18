"""A simple electro-thermal model of a lithium-ion battery pack.

The model is intentionally small: linear open-circuit voltage, a fixed internal
resistance per cell and first-order heating/cooling. It is deterministic for a
given seed so that tests are reproducible.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

OPEN_SENSOR_READING_C = 215.0


@dataclass
class PackConfig:
    cells_in_series: int = 96
    capacity_ah: float = 100.0
    cell_resistance_ohm: float = 0.0012
    ocv_empty_v: float = 3.0
    ocv_full_v: float = 4.2
    ambient_c: float = 25.0
    heat_coefficient: float = 0.05
    cooling_coefficient: float = 0.01
    initial_soc: float = 0.8
    seed: int = 1234


@dataclass
class Cell:
    soc: float
    temperature_c: float
    resistance_ohm: float


@dataclass
class BatteryPack:
    config: PackConfig = field(default_factory=PackConfig)
    load_current_a: float = 0.0
    """Positive = discharge, negative = charge."""

    def __post_init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        rng = random.Random(self.config.seed)
        self.load_current_a = 0.0
        self.cells = [
            Cell(
                soc=self.config.initial_soc + rng.uniform(-0.01, 0.01),
                temperature_c=self.config.ambient_c + rng.uniform(-0.5, 0.5),
                resistance_ohm=self.config.cell_resistance_ohm * rng.uniform(0.95, 1.05),
            )
            for _ in range(self.config.cells_in_series)
        ]
        self.clear_injections()

    def clear_injections(self) -> None:
        self._temp_overrides: dict[int, float] = {}
        self._voltage_overrides: dict[int, float] = {}

    def step(self, dt_s: float) -> None:
        cfg = self.config
        delta_soc = self.load_current_a * dt_s / 3600.0 / cfg.capacity_ah
        for cell in self.cells:
            cell.soc = min(1.0, max(0.0, cell.soc - delta_soc))
            heating = self.load_current_a**2 * cell.resistance_ohm * cfg.heat_coefficient
            cooling = (cell.temperature_c - cfg.ambient_c) * cfg.cooling_coefficient
            cell.temperature_c += (heating - cooling) * dt_s

    def cell_ocv(self, index: int) -> float:
        cfg = self.config
        return cfg.ocv_empty_v + (cfg.ocv_full_v - cfg.ocv_empty_v) * self.cells[index].soc

    def true_cell_voltage(self, index: int) -> float:
        return self.cell_ocv(index) - self.load_current_a * self.cells[index].resistance_ohm

    # --- sensor readings (what the BMS sees, including injected faults) ---

    def measured_cell_voltage(self, index: int) -> float:
        return self._voltage_overrides.get(index, self.true_cell_voltage(index))

    def measured_cell_temp(self, index: int) -> float:
        return self._temp_overrides.get(index, self.cells[index].temperature_c)

    def measured_cell_voltages(self) -> list[float]:
        return [self.measured_cell_voltage(i) for i in range(len(self.cells))]

    def measured_cell_temps(self) -> list[float]:
        return [self.measured_cell_temp(i) for i in range(len(self.cells))]

    @property
    def soc(self) -> float:
        return sum(c.soc for c in self.cells) / len(self.cells)

    # --- fault injection ---

    def _check_index(self, index: int) -> None:
        if not 0 <= index < len(self.cells):
            raise IndexError(f"cell index {index} out of range 0..{len(self.cells) - 1}")

    def inject_cell_temp(self, index: int, temperature_c: float) -> None:
        self._check_index(index)
        self._temp_overrides[index] = temperature_c

    def inject_cell_voltage(self, index: int, voltage_v: float) -> None:
        self._check_index(index)
        self._voltage_overrides[index] = voltage_v

    def open_temp_sensor(self, index: int) -> None:
        """Simulate a disconnected thermistor, which reads as full scale."""
        self.inject_cell_temp(index, OPEN_SENSOR_READING_C)
