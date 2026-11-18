"""Deterministic tests of the BMS application logic using a simulated clock.

These run in microseconds and pin down exact cycle-level behaviour (debounce
counts, timeouts) that would be timing-sensitive over a real bus.
"""

import pytest

from canbench import protocol
from canbench.bms import BmsConfig, BmsLogic
from canbench.protocol import BmsState, PlantCommand, RequestedState


class SimClock:
    """Drives BmsLogic: VCU frame every 50 ms, BMS tick every 100 ms."""

    def __init__(self, logic: BmsLogic):
        self.logic = logic
        self.now_ms = 0
        self.vcu_counter = 0
        self.vcu_enabled = True
        self.requested = RequestedState.STANDBY
        self.clear_faults = False

    def vcu(self, counter=None, corrupt=False):
        msg = protocol.encode(
            "VCU_Command",
            {"RequestedState": int(self.requested), "ClearFaults": int(self.clear_faults)},
            self.vcu_counter if counter is None else counter,
        )
        if counter is None:
            self.vcu_counter += 1
        if corrupt:
            data = bytearray(msg.data)
            data[protocol.CHECKSUM_BYTE] ^= 0xFF
            msg.data = bytes(data)
        self.logic.handle_frame(msg, self.now_ms)

    def plant(self, command, cell=0, value=0.0):
        msg = protocol.encode(
            "SIM_PlantControl", {"Command": int(command), "CellIndex": cell, "Value": value}, 0
        )
        self.logic.handle_frame(msg, self.now_ms)

    def cycles(self, n):
        """Advance n BMS cycles and return the decoded frames from the last one."""
        out = []
        for _ in range(n):
            for _ in range(2):
                if self.vcu_enabled:
                    self.vcu()
                self.now_ms += 50
            out = [protocol.decode(m) for m in self.logic.tick(self.now_ms)]
        return {f.name: f for f in out}


@pytest.fixture
def clock():
    c = SimClock(BmsLogic())
    c.cycles(1)
    return c


@pytest.mark.req("BMS-003")
def test_startup_goes_to_standby_after_one_cycle():
    logic = BmsLogic()
    frames = {protocol.decode(m).name: protocol.decode(m) for m in logic.tick(100)}
    assert frames["BMS_Status"]["BMS_State"] == BmsState.STANDBY
    assert frames["BMS_Status"]["ContactorsClosed"] == 0


@pytest.mark.req("BMS-006")
@pytest.mark.parametrize("cycles_over", [1, 2])
def test_short_overtemp_excursion_does_not_latch(clock, cycles_over):
    clock.plant(PlantCommand.SET_CELL_TEMP, 10, 75.0)
    clock.cycles(cycles_over)
    clock.plant(PlantCommand.CLEAR_INJECTIONS)
    out = clock.cycles(3)
    assert out["BMS_Faults"]["OverTemp"] == 0
    assert out["BMS_Status"]["BMS_State"] == BmsState.STANDBY


@pytest.mark.req("BMS-005")
@pytest.mark.req("BMS-006")
def test_overtemp_latches_on_third_consecutive_cycle(clock):
    clock.plant(PlantCommand.SET_CELL_TEMP, 10, 75.0)
    assert clock.cycles(2)["BMS_Faults"]["OverTemp"] == 0
    out = clock.cycles(1)
    assert out["BMS_Faults"]["OverTemp"] == 1
    assert out["BMS_Status"]["BMS_State"] == BmsState.FAULT


@pytest.mark.req("BMS-005")
@pytest.mark.parametrize("temp, trips", [(60.0, False), (60.5, True)])
def test_overtemp_threshold_boundary(clock, temp, trips):
    clock.plant(PlantCommand.SET_CELL_TEMP, 0, temp)
    assert clock.cycles(4)["BMS_Faults"]["OverTemp"] == int(trips)


@pytest.mark.req("BMS-007")
@pytest.mark.parametrize(
    "voltage, fault",
    [(2.79, "UnderVoltage"), (4.26, "OverVoltage")],
)
def test_voltage_limits(clock, voltage, fault):
    clock.plant(PlantCommand.SET_CELL_VOLTAGE, 50, voltage)
    out = clock.cycles(3)
    assert out["BMS_Faults"][fault] == 1


@pytest.mark.req("BMS-007")
@pytest.mark.parametrize("voltage", [2.81, 4.24])
def test_voltage_just_inside_limits_is_ok(clock, voltage):
    clock.plant(PlantCommand.SET_CELL_VOLTAGE, 50, voltage)
    out = clock.cycles(5)
    assert not any(out["BMS_Faults"][f] for f in protocol.FAULT_SIGNALS)


@pytest.mark.req("BMS-009")
def test_implausible_voltage_is_sensor_fault_not_undervoltage(clock):
    clock.plant(PlantCommand.SET_CELL_VOLTAGE, 5, 0.0)
    out = clock.cycles(3)
    assert out["BMS_Faults"]["SensorFault"] == 1
    assert out["BMS_Faults"]["UnderVoltage"] == 0


@pytest.mark.req("BMS-008")
def test_load_current_only_flows_with_contactors_closed(clock):
    clock.plant(PlantCommand.SET_LOAD_CURRENT, value=600.0)
    out = clock.cycles(5)
    assert out["BMS_Status"]["PackCurrent"] == 0
    assert out["BMS_Faults"]["OverCurrent"] == 0

    clock.requested = RequestedState.DRIVE
    clock.cycles(1)
    out = clock.cycles(4)
    assert out["BMS_Faults"]["OverCurrent"] == 1


@pytest.mark.req("BMS-010")
def test_comm_loss_not_armed_before_first_vcu_frame():
    clock = SimClock(BmsLogic())
    clock.vcu_enabled = False
    out = clock.cycles(20)
    assert out["BMS_Faults"]["CommLoss"] == 0
    assert out["BMS_Status"]["BMS_State"] == BmsState.STANDBY


@pytest.mark.req("BMS-010")
def test_comm_loss_after_timeout(clock):
    timeout = BmsConfig().comm_timeout_ms
    clock.vcu_enabled = False
    last_frame_ms = clock.logic.last_vcu_ms
    while clock.now_ms + 100 - last_frame_ms <= timeout:
        assert clock.cycles(1)["BMS_Faults"]["CommLoss"] == 0
    out = clock.cycles(1)
    assert clock.now_ms - last_frame_ms > timeout
    assert out["BMS_Faults"]["CommLoss"] == 1
    assert out["BMS_Status"]["BMS_State"] == BmsState.FAULT


@pytest.mark.req("BMS-011")
def test_repeated_counter_frames_are_rejected(clock):
    clock.vcu_enabled = False
    before = clock.logic.rejected_frames
    clock.vcu(counter=9)
    for _ in range(12):
        clock.now_ms += 50
        clock.vcu(counter=9)
        if clock.now_ms % 100 == 0:
            clock.logic.tick(clock.now_ms)
    assert clock.logic.rejected_frames - before == 12
    assert clock.logic.faults["CommLoss"]


@pytest.mark.req("BMS-011")
def test_bad_checksum_frame_does_not_change_state(clock):
    clock.vcu_enabled = False
    clock.requested = RequestedState.DRIVE
    clock.vcu(corrupt=True)
    out = clock.cycles(1)
    assert out["BMS_Status"]["BMS_State"] == BmsState.STANDBY


@pytest.mark.req("BMS-004")
def test_drive_to_charge_must_pass_through_standby(clock):
    clock.requested = RequestedState.DRIVE
    assert clock.cycles(1)["BMS_Status"]["BMS_State"] == BmsState.DRIVE
    clock.requested = RequestedState.CHARGE
    assert clock.cycles(3)["BMS_Status"]["BMS_State"] == BmsState.DRIVE
    clock.requested = RequestedState.STANDBY
    assert clock.cycles(1)["BMS_Status"]["BMS_State"] == BmsState.STANDBY
    clock.requested = RequestedState.CHARGE
    assert clock.cycles(1)["BMS_Status"]["BMS_State"] == BmsState.CHARGE


@pytest.mark.req("BMS-012")
def test_fault_stays_latched_until_cleared(clock):
    clock.plant(PlantCommand.SET_CELL_TEMP, 0, 80.0)
    clock.cycles(3)
    clock.plant(PlantCommand.CLEAR_INJECTIONS)
    out = clock.cycles(10)
    assert out["BMS_Faults"]["OverTemp"] == 1
    assert out["BMS_Status"]["BMS_State"] == BmsState.FAULT

    clock.clear_faults = True
    out = clock.cycles(1)
    assert out["BMS_Faults"]["OverTemp"] == 0
    assert out["BMS_Status"]["BMS_State"] == BmsState.STANDBY


@pytest.mark.req("BMS-012")
def test_clear_refused_while_condition_present(clock):
    clock.plant(PlantCommand.SET_CELL_TEMP, 0, 80.0)
    clock.cycles(3)
    clock.clear_faults = True
    out = clock.cycles(5)
    assert out["BMS_Faults"]["OverTemp"] == 1
    assert out["BMS_Status"]["BMS_State"] == BmsState.FAULT


def test_invalid_cell_index_in_plant_command_is_ignored(clock):
    clock.plant(PlantCommand.SET_CELL_TEMP, 200, 90.0)
    out = clock.cycles(5)
    assert out["BMS_Faults"]["OverTemp"] == 0
