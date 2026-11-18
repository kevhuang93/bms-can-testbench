import time

import pytest

from canbench import BmsState, RequestedState

pytestmark = pytest.mark.integration


@pytest.mark.req("BMS-012")
def test_fault_latched_until_vcu_clears(bench):
    bench.plant.set_cell_temp(0, 75.0)
    bench.wait_for_fault("OverTemp")
    bench.plant.clear_injections()
    time.sleep(0.4)
    assert bench.faults()["OverTemp"], "fault must stay latched after the condition goes away"

    bench.clear_faults()
    assert not any(bench.faults().values())


@pytest.mark.req("BMS-012")
def test_clear_rejected_while_condition_active(bench):
    bench.plant.set_cell_temp(0, 75.0)
    bench.wait_for_fault("OverTemp")
    with pytest.raises(TimeoutError):
        bench.clear_faults(timeout=0.5)
    assert bench.status()["BMS_State"] == BmsState.FAULT


@pytest.mark.req("BMS-013")
def test_status_signals_are_plausible_at_rest(bench):
    status = bench.status()
    cells = bench.recorder.latest("BMS_CellStats")
    assert 96 * 3.0 <= status["PackVoltage"] <= 96 * 4.2
    assert 0 <= status["SOC"] <= 100
    assert status["PackCurrent"] == 0
    assert cells["MinCellVoltage"] <= cells["MaxCellVoltage"]
    assert cells["MinCellTemp"] <= cells["MaxCellTemp"]
    # Pack voltage is the sum of 96 cells, so it must sit between 96x min and 96x max.
    assert 96 * cells["MinCellVoltage"] - 0.5 <= status["PackVoltage"] <= 96 * cells["MaxCellVoltage"] + 0.5


@pytest.mark.req("BMS-013")
def test_voltage_sags_and_current_reported_under_load(bench):
    rest = bench.request(RequestedState.DRIVE)["PackVoltage"]
    since = time.time()
    bench.plant.set_load_current(300)
    loaded = bench.recorder.wait_for(
        "BMS_Status", lambda f: f["PackCurrent"] == pytest.approx(300, abs=0.1), since=since
    )
    assert loaded["PackVoltage"] < rest - 20, "expected ~35 V sag at 300 A"


@pytest.mark.req("BMS-013")
def test_soc_decreases_while_discharging(bench):
    bench.request(RequestedState.DRIVE)
    bench.plant.set_load_current(480)
    start = bench.status()["SOC"]
    # 480 A on 100 Ah drains 0.5 % SOC (one LSB) in ~3.75 s.
    deadline = time.time() + 6
    while time.time() < deadline and bench.status()["SOC"] >= start:
        time.sleep(0.2)
    assert bench.status()["SOC"] < start
