import pytest

from canbench import protocol
from canbench.faults import FrameFault, FrameFaultInjector


def frames(n, start=0):
    return [protocol.encode("VCU_Command", {"RequestedState": 1}, start + i) for i in range(n)]


def test_passthrough_when_no_rules():
    inj = FrameFaultInjector()
    msg = frames(1)[0]
    assert inj.apply(msg) is msg


def test_drop_count_then_resume():
    inj = FrameFaultInjector()
    inj.drop(count=2)
    out = [inj.apply(m) for m in frames(4)]
    assert out[:2] == [None, None]
    assert all(o is not None for o in out[2:])
    assert inj.injected[FrameFault.DROP] == 2
    assert not inj.active


def test_corrupt_checksum_only_changes_checksum():
    inj = FrameFaultInjector()
    inj.corrupt_checksum()
    original = frames(1)[0]
    out = inj.apply(original)
    assert not protocol.decode(out).checksum_ok
    assert out.data[: protocol.CHECKSUM_BYTE] == original.data[: protocol.CHECKSUM_BYTE]


def test_freeze_counter_repeats_first_counter_with_valid_checksum():
    inj = FrameFaultInjector()
    inj.freeze_counter(count=3)
    out = [protocol.decode(inj.apply(m)) for m in frames(5, start=4)]
    assert [f.counter for f in out] == [4, 4, 4, 7, 8]
    assert all(f.checksum_ok for f in out)


def test_clear_removes_indefinite_rules():
    inj = FrameFaultInjector()
    inj.drop()
    assert inj.apply(frames(1)[0]) is None
    inj.clear()
    assert inj.apply(frames(1)[0]) is not None


def test_invalid_count_rejected():
    with pytest.raises(ValueError):
        FrameFaultInjector().drop(count=0)
