"""Simulated EV battery management system and black-box CAN test framework."""

from canbench.harness import BenchConfig, Testbench
from canbench.protocol import BmsState, RequestedState

__all__ = ["BenchConfig", "BmsState", "RequestedState", "Testbench"]
__version__ = "0.1.0"
