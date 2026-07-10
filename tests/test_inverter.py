"""Tests for the FroniusModbusInverter device class."""

import pytest
from modbus_connection.mock import MockModbusUnit

from fronius_modbus import FroniusModbusInverter, SunSpecError, datamanager_unit_id
from fronius_modbus.testing import MpptModuleSpec, build_sunspec_map

MODULES = [
    MpptModuleSpec(
        id_str="String 1", current=82, voltage=4021, power=3300, energy=1_000_000
    ),
]


@pytest.mark.parametrize(
    ("float_mode", "expected_float_mode"),
    [pytest.param(True, True, id="float"), pytest.param(False, False, id="int_sf")],
)
async def test_data_type_detection(
    mock_modbus_unit: MockModbusUnit, float_mode: bool, expected_float_mode: bool
) -> None:
    """Test float vs int+SF detection from the discovered inverter model."""
    mock_modbus_unit.holding.update(build_sunspec_map(MODULES, float_mode=float_mode))
    inverter = FroniusModbusInverter(mock_modbus_unit, has_storage=False)
    await inverter.discover()
    assert inverter.float_mode is expected_float_mode
    assert inverter.has_mppt is True

    data = await inverter.read_mppt()
    assert data.modules[0].power == 3300


@pytest.mark.parametrize(
    ("storage_wcha_max", "expected_has_storage"),
    [
        pytest.param(None, False, id="no_storage_model"),
        pytest.param(0, False, id="storage_capable_without_battery"),
        pytest.param(0xFFFF, False, id="wcha_max_not_implemented"),
        pytest.param(5000, True, id="storage_connected"),
    ],
)
async def test_storage_auto_detection(
    mock_modbus_unit: MockModbusUnit,
    storage_wcha_max: int | None,
    expected_has_storage: bool,
) -> None:
    """Test storage detection from the Basic Storage Control Model (124)."""
    hybrid_modules = [
        MpptModuleSpec(id_str="String 1", energy=1_000_000),
        MpptModuleSpec(id_str="String 2", energy=500_000),
        MpptModuleSpec(id_str="unnamed 3", energy=200_000),
        MpptModuleSpec(id_str="unnamed 4", energy=150_000),
    ]
    mock_modbus_unit.holding.update(
        build_sunspec_map(hybrid_modules, storage_wcha_max=storage_wcha_max)
    )
    inverter = FroniusModbusInverter(mock_modbus_unit)
    await inverter.discover()
    assert inverter.has_storage is expected_has_storage

    data = await inverter.read_mppt()
    if expected_has_storage:
        assert data.storage_charge_energy_total == 200_000
        assert data.storage_discharge_energy_total == 150_000
    else:
        assert data.storage_charge_energy_total is None
        assert data.pv_energy_total == 1_850_000


async def test_storage_detection_override(mock_modbus_unit: MockModbusUnit) -> None:
    """Test an explicit has_storage overrides the auto-detection."""
    mock_modbus_unit.holding.update(build_sunspec_map(MODULES))
    inverter = FroniusModbusInverter(mock_modbus_unit, has_storage=True)
    await inverter.discover()
    assert inverter.has_storage is True


async def test_no_mppt_model(mock_modbus_unit: MockModbusUnit) -> None:
    """Test a device without model 160 in its chain."""
    mock_modbus_unit.holding.update(build_sunspec_map([], include_mppt_model=False))
    inverter = FroniusModbusInverter(mock_modbus_unit, has_storage=False)
    await inverter.discover()
    assert inverter.has_mppt is False
    with pytest.raises(SunSpecError, match="not available"):
        await inverter.read_mppt()


async def test_rediscovery_on_register_map_shift(
    mock_modbus_unit: MockModbusUnit,
) -> None:
    """Test a data type change at runtime triggers re-discovery."""
    mock_modbus_unit.holding.update(build_sunspec_map(MODULES, float_mode=True))
    inverter = FroniusModbusInverter(mock_modbus_unit, has_storage=False)
    await inverter.discover()
    assert (await inverter.read_mppt()).modules[0].power == 3300

    # switching to int+SF shifts the model 160 address
    mock_modbus_unit.holding.clear()
    mock_modbus_unit.holding.update(build_sunspec_map(MODULES, float_mode=False))
    data = await inverter.read_mppt()
    assert data.modules[0].power == 3300
    assert inverter.float_mode is False


async def test_broken_register_map(mock_modbus_unit: MockModbusUnit) -> None:
    """Test a broken register map raises after failed re-discovery."""
    mock_modbus_unit.holding.update(build_sunspec_map(MODULES))
    inverter = FroniusModbusInverter(mock_modbus_unit, has_storage=False)
    await inverter.discover()

    mock_modbus_unit.holding.clear()
    with pytest.raises(SunSpecError):
        await inverter.read_mppt()


def test_datamanager_unit_id() -> None:
    """Test the Datamanager SolarNet number to Modbus unit ID mapping."""
    assert datamanager_unit_id("1") == 1
    assert datamanager_unit_id("99") == 99
    # inverter number 00 maps to unit ID 100
    assert datamanager_unit_id("0") == 100
    assert datamanager_unit_id("not a number") is None
