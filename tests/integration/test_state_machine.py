import time

import pytest

from canbench import BmsState, RequestedState

pytestmark = pytest.mark.integration


@pytest.mark.req("BMS-003")
def test_starts_in_standby_with_contactors_open(bench):
    status = bench.status()
    assert status["BMS_State"] == BmsState.STANDBY
    assert status["ContactorsClosed"] == 0
    assert not any(bench.faults().values())


@pytest.mark.req("BMS-004")
@pytest.mark.parametrize("target", [RequestedState.DRIVE, RequestedState.CHARGE])
def test_vcu_request_closes_contactors(bench, target):
    status = bench.request(target, timeout=0.5)
    assert status["ContactorsClosed"] == 1


@pytest.mark.req("BMS-004")
def test_return_to_standby_opens_contactors(bench):
    bench.request(RequestedState.DRIVE)
    status = bench.request(RequestedState.STANDBY, timeout=0.5)
    assert status["ContactorsClosed"] == 0


@pytest.mark.req("BMS-004")
def test_direct_drive_to_charge_is_rejected(bench):
    bench.request(RequestedState.DRIVE)
    since = time.time()
    bench.vcu.requested_state = RequestedState.CHARGE
    frames = bench.recorder.collect("BMS_Status", duration=0.6)
    assert frames, "no status frames"
    assert all(f["BMS_State"] == BmsState.DRIVE for f in frames if f.timestamp >= since)


@pytest.mark.req("BMS-004")
def test_contactors_closed_only_in_drive_or_charge(bench):
    """Invariant check over a full drive cycle."""
    bench.request(RequestedState.DRIVE)
    bench.plant.set_load_current(150)
    time.sleep(0.3)
    bench.request(RequestedState.STANDBY)
    bench.request(RequestedState.CHARGE)
    bench.plant.set_load_current(-80)
    time.sleep(0.3)
    bench.request(RequestedState.STANDBY)

    for f in bench.recorder.frames("BMS_Status"):
        closed = bool(f["ContactorsClosed"])
        assert closed == (f["BMS_State"] in (BmsState.DRIVE, BmsState.CHARGE)), f.signals
