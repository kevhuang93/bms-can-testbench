"""Safety reactions to battery fault conditions, injected through the plant model."""

import time

import pytest

from canbench import BmsState, RequestedState

pytestmark = pytest.mark.integration

FAULT_REACTION_S = 0.5


def assert_safe_state(bench, since):
    status = bench.wait_for_state(BmsState.FAULT, timeout=FAULT_REACTION_S, since=since)
    assert status["ContactorsClosed"] == 0, "contactors must open on fault"


@pytest.mark.req("BMS-005")
def test_overtemp_while_driving_opens_contactors(bench):
    bench.request(RequestedState.DRIVE)
    since = time.time()
    bench.plant.set_cell_temp(42, 68.0)

    fault = bench.wait_for_fault("OverTemp", timeout=FAULT_REACTION_S, since=since)
    assert (fault.timestamp - since) * 1000 <= FAULT_REACTION_S * 1000
    assert_safe_state(bench, since)
    assert bench.recorder.latest("BMS_CellStats")["MaxCellTemp"] == 68


@pytest.mark.req("BMS-007")
@pytest.mark.parametrize(
    "cell, voltage, fault",
    [(0, 2.5, "UnderVoltage"), (95, 4.4, "OverVoltage")],
    ids=["undervoltage", "overvoltage"],
)
def test_cell_voltage_limits(bench, cell, voltage, fault):
    bench.request(RequestedState.CHARGE)
    since = time.time()
    bench.plant.set_cell_voltage(cell, voltage)
    bench.wait_for_fault(fault, timeout=FAULT_REACTION_S, since=since)
    assert_safe_state(bench, since)


@pytest.mark.req("BMS-008")
def test_overcurrent_while_driving(bench):
    bench.request(RequestedState.DRIVE)
    since = time.time()
    bench.plant.set_load_current(650)
    bench.wait_for_fault("OverCurrent", timeout=FAULT_REACTION_S, since=since)
    assert_safe_state(bench, since)


@pytest.mark.req("BMS-008")
def test_high_but_legal_current_does_not_trip(bench):
    bench.request(RequestedState.DRIVE)
    bench.plant.set_load_current(450)
    frames = bench.recorder.collect("BMS_Faults", duration=0.6)
    assert not any(f["OverCurrent"] for f in frames)
    assert bench.status()["BMS_State"] == BmsState.DRIVE


@pytest.mark.req("BMS-009")
def test_open_thermistor_is_sensor_fault_not_overtemp(bench):
    since = time.time()
    bench.plant.open_temp_sensor(17)
    bench.wait_for_fault("SensorFault", timeout=FAULT_REACTION_S, since=since)
    frames = bench.recorder.frames("BMS_Faults", since=since)
    assert not any(f["OverTemp"] for f in frames), "broken sensor must not be reported as overtemp"


@pytest.mark.req("BMS-005")
def test_overtemp_is_reported_when_another_sensor_is_open(bench):
    """A broken sensor must not mask a real thermal event on a healthy cell."""
    since = time.time()
    bench.plant.open_temp_sensor(3)
    bench.plant.set_cell_temp(4, 72.0)
    bench.wait_for_fault("OverTemp", timeout=FAULT_REACTION_S, since=since)
    assert bench.faults()["SensorFault"]
