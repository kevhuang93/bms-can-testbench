import pytest

from canbench.pack import OPEN_SENSOR_READING_C, BatteryPack, PackConfig


@pytest.fixture
def pack():
    return BatteryPack()


def test_model_is_deterministic_for_a_seed():
    a, b = BatteryPack(PackConfig(seed=7)), BatteryPack(PackConfig(seed=7))
    assert a.measured_cell_voltages() == b.measured_cell_voltages()
    assert a.measured_cell_temps() == b.measured_cell_temps()


def test_discharge_reduces_soc_by_coulomb_count(pack):
    start = pack.soc
    pack.load_current_a = 100.0
    for _ in range(36):
        pack.step(1.0)  # 100 A for 36 s = 1 Ah = 1 % of 100 Ah
    assert start - pack.soc == pytest.approx(0.01, rel=1e-6)


def test_charging_increases_soc(pack):
    start = pack.soc
    pack.load_current_a = -50.0
    pack.step(60.0)
    assert pack.soc > start


def test_soc_is_bounded(pack):
    pack.load_current_a = 1000.0
    pack.step(10_000.0)
    assert pack.soc == 0.0


def test_voltage_sags_under_load(pack):
    rest = pack.true_cell_voltage(0)
    pack.load_current_a = 300.0
    assert pack.true_cell_voltage(0) < rest


def test_cells_heat_under_load_and_cool_at_rest(pack):
    t0 = pack.cells[0].temperature_c
    pack.load_current_a = 300.0
    for _ in range(50):
        pack.step(0.1)
    hot = pack.cells[0].temperature_c
    assert hot > t0
    pack.load_current_a = 0.0
    for _ in range(50):
        pack.step(0.1)
    assert pack.cells[0].temperature_c < hot


def test_injected_readings_override_and_clear(pack):
    pack.inject_cell_temp(3, 75.0)
    pack.inject_cell_voltage(4, 2.5)
    assert pack.measured_cell_temp(3) == 75.0
    assert pack.measured_cell_voltage(4) == 2.5
    assert pack.cells[3].temperature_c != 75.0  # true state unaffected
    pack.clear_injections()
    assert pack.measured_cell_temp(3) == pack.cells[3].temperature_c


def test_open_sensor_reads_full_scale(pack):
    pack.open_temp_sensor(0)
    assert pack.measured_cell_temp(0) == OPEN_SENSOR_READING_C


@pytest.mark.parametrize("index", [-1, 96, 255])
def test_invalid_cell_index_rejected(pack, index):
    with pytest.raises(IndexError):
        pack.inject_cell_temp(index, 30.0)
