import can
import pytest

from canbench import protocol
from canbench.monitor import e2e_violations


def test_encode_sets_valid_checksum_and_counter():
    msg = protocol.encode("VCU_Command", {"RequestedState": 2}, counter=7)
    frame = protocol.decode(msg)
    assert frame.name == "VCU_Command"
    assert frame.counter == 7
    assert frame.checksum_ok
    assert frame["RequestedState"] == 2


def test_counter_wraps_modulo_16():
    frame = protocol.decode(protocol.encode("VCU_Command", {}, counter=17))
    assert frame.counter == 1


@pytest.mark.parametrize("byte", range(8))
def test_any_single_byte_corruption_is_detected(byte):
    msg = protocol.encode("BMS_Status", {"PackVoltage": 380.0, "SOC": 80}, counter=3)
    data = bytearray(msg.data)
    data[byte] ^= 0x01
    corrupted = can.Message(arbitration_id=msg.arbitration_id, data=bytes(data), is_extended_id=False)
    assert not protocol.decode(corrupted).checksum_ok


def test_checksum_covers_frame_id():
    msg = protocol.encode("BMS_Faults", {}, counter=0)
    moved = can.Message(arbitration_id=0x101, data=msg.data, is_extended_id=False)
    assert not protocol.decode(moved).checksum_ok


def test_out_of_range_values_saturate():
    frame = protocol.decode(protocol.encode("BMS_CellStats", {"MaxCellTemp": 500, "MinCellTemp": -100}, 0))
    assert frame["MaxCellTemp"] == 215
    assert frame["MinCellTemp"] == -40


def test_signed_current_round_trips():
    frame = protocol.decode(protocol.encode("BMS_Status", {"PackCurrent": -123.4}, 0))
    assert frame["PackCurrent"] == pytest.approx(-123.4)


def test_unknown_id_decodes_to_none():
    assert protocol.decode(can.Message(arbitration_id=0x7FF, data=bytes(8))) is None


def test_e2e_violations_reports_counter_gap_and_bad_checksum():
    frames = [protocol.decode(protocol.encode("BMS_Faults", {}, c)) for c in (14, 15, 0, 2)]
    bad = protocol.encode("BMS_Faults", {}, 3)
    data = bytearray(bad.data)
    data[protocol.CHECKSUM_BYTE] ^= 0xFF
    frames.append(protocol.decode(can.Message(arbitration_id=bad.arbitration_id, data=bytes(data))))

    problems = e2e_violations(frames)
    assert len(problems) == 2
    assert "counter 0 -> 2" in problems[0]
    assert "bad checksum" in problems[1]


def test_e2e_violations_clean_stream():
    frames = [protocol.decode(protocol.encode("BMS_Status", {}, c)) for c in range(40)]
    assert e2e_violations(frames) == []
