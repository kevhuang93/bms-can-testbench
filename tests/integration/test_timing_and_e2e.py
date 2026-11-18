import pytest

from canbench.bms import TX_MESSAGES
from canbench.monitor import e2e_violations, period_stats

pytestmark = pytest.mark.integration

NOMINAL_PERIOD_MS = 100.0


@pytest.mark.req("BMS-001")
@pytest.mark.parametrize("name", TX_MESSAGES)
def test_cyclic_message_period(bench, name):
    frames = bench.recorder.collect(name, duration=2.0)
    stats = period_stats(frames)
    assert stats["count"] >= 15, f"too few {name} frames: {stats}"
    assert stats["median"] == pytest.approx(NOMINAL_PERIOD_MS, rel=0.10), stats
    assert stats["max"] < 1.5 * NOMINAL_PERIOD_MS, f"{name} gap too long: {stats}"


@pytest.mark.req("BMS-002")
@pytest.mark.parametrize("name", TX_MESSAGES)
def test_e2e_protection_on_bms_frames(bench, name):
    frames = bench.recorder.collect(name, duration=2.0)
    assert len(frames) > 16, "need a full counter wrap"
    assert e2e_violations(frames) == []
