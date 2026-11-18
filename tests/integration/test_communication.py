"""Robustness against VCU communication faults, injected on the harness side."""

import time

import pytest

from canbench import BmsState, RequestedState

pytestmark = pytest.mark.integration

COMM_TIMEOUT_S = 0.5
DETECTION_BUDGET_S = COMM_TIMEOUT_S + 0.25


@pytest.mark.req("BMS-010")
def test_heartbeat_loss_detected(bench):
    bench.request(RequestedState.DRIVE)
    since = time.time()
    bench.vcu.faults.drop()
    fault = bench.wait_for_fault("CommLoss", timeout=DETECTION_BUDGET_S + 0.5, since=since)
    assert fault.timestamp - since <= DETECTION_BUDGET_S
    status = bench.wait_for_state(BmsState.FAULT, since=since)
    assert status["ContactorsClosed"] == 0


@pytest.mark.req("BMS-010")
def test_short_heartbeat_gap_is_tolerated(bench):
    bench.request(RequestedState.DRIVE)
    since = time.time()
    bench.vcu.faults.drop(count=5)  # 250 ms of missing frames
    frames = bench.recorder.collect("BMS_Faults", duration=1.0)
    assert not any(f["CommLoss"] for f in frames if f.timestamp >= since)
    assert bench.status()["BMS_State"] == BmsState.DRIVE


@pytest.mark.req("BMS-011")
def test_corrupted_heartbeats_count_as_lost(bench):
    since = time.time()
    bench.vcu.faults.corrupt_checksum()
    bench.wait_for_fault("CommLoss", timeout=DETECTION_BUDGET_S + 0.5, since=since)


@pytest.mark.req("BMS-011")
def test_frozen_counter_heartbeats_count_as_lost(bench):
    since = time.time()
    bench.vcu.faults.freeze_counter()
    bench.wait_for_fault("CommLoss", timeout=DETECTION_BUDGET_S + 0.5, since=since)


@pytest.mark.req("BMS-011")
def test_corrupted_drive_request_is_ignored(bench):
    bench.vcu.faults.corrupt_checksum(count=4)
    since = time.time()
    bench.vcu.requested_state = RequestedState.DRIVE
    time.sleep(0.15)
    bench.vcu.requested_state = RequestedState.STANDBY
    frames = bench.recorder.collect("BMS_Status", duration=0.3)
    assert all(f["BMS_State"] == BmsState.STANDBY for f in frames if f.timestamp >= since)


@pytest.mark.req("BMS-012")
def test_recovery_after_comm_restored(bench):
    bench.vcu.faults.drop()
    bench.wait_for_fault("CommLoss", timeout=DETECTION_BUDGET_S + 0.5)
    bench.vcu.faults.clear()
    time.sleep(0.2)
    assert bench.status()["BMS_State"] == BmsState.FAULT, "fault must stay latched"
    bench.clear_faults()
    assert bench.request(RequestedState.DRIVE)["ContactorsClosed"] == 1
