"""Tests for the write support: power limit and storage controls."""

import pytest
from modbus_connection import ModbusExceptionError
from modbus_connection.mock import MockModbusUnit

from fronius_modbus import (
    Controls,
    FroniusModbusInverter,
    Storage,
    SunSpecMapShiftError,
)
from fronius_modbus.sunspec import (
    IMMEDIATE_CONTROLS_MODEL_ID,
    STORAGE_MODEL_ID,
    SunSpecModel,
)
from fronius_modbus.testing import build_sunspec_map


async def _discovered_inverter(
    unit: MockModbusUnit, registers: dict[int, int]
) -> FroniusModbusInverter:
    unit.holding.update(registers)
    inverter = FroniusModbusInverter(unit)
    await inverter.discover()
    return inverter


def _controls(inverter: FroniusModbusInverter) -> Controls:
    assert inverter.controls is not None
    return inverter.controls


def _storage(inverter: FroniusModbusInverter) -> Storage:
    assert inverter.storage is not None
    return inverter.storage


def _model_data_address(inverter: FroniusModbusInverter, model_id: int) -> int:
    model = next(model for model in inverter.model_chain if model.model_id == model_id)
    assert isinstance(model, SunSpecModel)
    return model.address + 2


async def test_read_controls(mock_modbus_unit: MockModbusUnit) -> None:
    """Test reading the power limit state."""
    inverter = await _discovered_inverter(mock_modbus_unit, build_sunspec_map([]))
    controls = _controls(inverter)

    await controls.async_update()
    assert controls.power_limit == 100.0
    assert controls.enabled is False
    assert controls.revert_seconds == 0


async def test_set_power_limit(mock_modbus_unit: MockModbusUnit) -> None:
    """Test setting the output power limit writes scaled raw values."""
    inverter = await _discovered_inverter(mock_modbus_unit, build_sunspec_map([]))
    controls = _controls(inverter)
    data_address = _model_data_address(inverter, IMMEDIATE_CONTROLS_MODEL_ID)

    await controls.set_power_limit(80.0, revert_seconds=120)
    assert mock_modbus_unit.holding[data_address + 3] == 8000  # WMaxLimPct, SF -2
    assert mock_modbus_unit.holding[data_address + 5] == 120  # RvrtTms
    assert mock_modbus_unit.holding[data_address + 7] == 1  # WMaxLim_Ena

    await controls.async_update()
    assert controls.power_limit == 80.0
    assert controls.enabled is True
    assert controls.revert_seconds == 120

    await controls.clear_power_limit()
    assert mock_modbus_unit.holding[data_address + 7] == 0


@pytest.mark.parametrize("percent", [-1.0, 100.5])
async def test_set_power_limit_out_of_range(
    mock_modbus_unit: MockModbusUnit, percent: float
) -> None:
    """Test power limit range validation."""
    inverter = await _discovered_inverter(mock_modbus_unit, build_sunspec_map([]))
    with pytest.raises(ValueError, match="out of range"):
        await _controls(inverter).set_power_limit(percent)


async def test_probe_write_access(mock_modbus_unit: MockModbusUnit) -> None:
    """Test the write access probe against accepting and rejecting devices."""
    inverter = await _discovered_inverter(mock_modbus_unit, build_sunspec_map([]))
    controls = _controls(inverter)
    assert await controls.probe_write_access() is True

    # a device with Modbus control disabled rejects the write
    data_address = _model_data_address(inverter, IMMEDIATE_CONTROLS_MODEL_ID)
    mock_modbus_unit.fail_write(
        data_address, ModbusExceptionError(2, "illegal data address")
    )
    assert await controls.probe_write_access() is False


async def test_set_storage_limits(mock_modbus_unit: MockModbusUnit) -> None:
    """Test setting charge and discharge limits."""
    inverter = await _discovered_inverter(
        mock_modbus_unit, build_sunspec_map([], storage_wcha_max=12800)
    )
    storage = _storage(inverter)
    data_address = _model_data_address(inverter, STORAGE_MODEL_ID)

    await storage.set_limits(charge=50.0, discharge=0.0, revert_seconds=60)
    assert mock_modbus_unit.holding[data_address + 11] == 5000  # InWRte, SF -2
    assert mock_modbus_unit.holding[data_address + 10] == 0  # OutWRte
    assert mock_modbus_unit.holding[data_address + 3] == 0b11  # StorCtl_Mod
    assert mock_modbus_unit.holding[data_address + 13] == 60  # RvrtTms

    await storage.async_update()
    assert storage.charge_limit == 50.0
    assert storage.discharge_limit == 0.0
    assert storage.charge_limit_enabled is True
    assert storage.discharge_limit_enabled is True


async def test_forced_charging(mock_modbus_unit: MockModbusUnit) -> None:
    """Test a negative discharge limit (forced charging) encodes signed."""
    inverter = await _discovered_inverter(
        mock_modbus_unit, build_sunspec_map([], storage_wcha_max=12800)
    )
    storage = _storage(inverter)
    data_address = _model_data_address(inverter, STORAGE_MODEL_ID)

    await storage.set_limits(discharge=-50.0)
    assert mock_modbus_unit.holding[data_address + 10] == 0x10000 - 5000  # OutWRte
    assert mock_modbus_unit.holding[data_address + 3] == 0b10  # discharge bit only

    await storage.async_update()
    assert storage.discharge_limit == -50.0
    assert storage.charge_limit_enabled is False
    assert storage.discharge_limit_enabled is True


async def test_clear_storage_limits(mock_modbus_unit: MockModbusUnit) -> None:
    """Test deactivating all storage limits."""
    inverter = await _discovered_inverter(
        mock_modbus_unit, build_sunspec_map([], storage_wcha_max=12800)
    )
    storage = _storage(inverter)
    data_address = _model_data_address(inverter, STORAGE_MODEL_ID)
    await storage.set_limits(charge=50.0, discharge=50.0)

    await storage.set_limits()
    assert mock_modbus_unit.holding[data_address + 3] == 0


async def test_set_minimum_reserve(mock_modbus_unit: MockModbusUnit) -> None:
    """Test setting the minimum state of charge reserve."""
    inverter = await _discovered_inverter(
        mock_modbus_unit,
        build_sunspec_map([], storage_wcha_max=12800, storage_min_reserve=7.0),
    )
    storage = _storage(inverter)
    data_address = _model_data_address(inverter, STORAGE_MODEL_ID)
    await storage.async_update()
    assert storage.minimum_reserve == 7.0

    await storage.set_minimum_reserve(20.0)
    assert mock_modbus_unit.holding[data_address + 5] == 2000  # MinRsvPct, SF -2
    await storage.async_update()
    assert storage.minimum_reserve == 20.0


async def test_set_grid_charging(mock_modbus_unit: MockModbusUnit) -> None:
    """Test allowing and preventing grid charging."""
    inverter = await _discovered_inverter(
        mock_modbus_unit, build_sunspec_map([], storage_wcha_max=12800)
    )
    storage = _storage(inverter)
    data_address = _model_data_address(inverter, STORAGE_MODEL_ID)
    await storage.async_update()
    assert storage.grid_charging is False

    await storage.set_grid_charging(True)
    assert mock_modbus_unit.holding[data_address + 15] == 1  # ChaGriSet
    await storage.async_update()
    assert storage.grid_charging is True

    await storage.set_grid_charging(False)
    assert mock_modbus_unit.holding[data_address + 15] == 0


async def test_storage_writes_without_model(mock_modbus_unit: MockModbusUnit) -> None:
    """Test a device without the storage model exposes no storage component."""
    inverter = await _discovered_inverter(mock_modbus_unit, build_sunspec_map([]))
    assert inverter.storage is None


async def test_write_after_register_map_shift(
    mock_modbus_unit: MockModbusUnit,
) -> None:
    """Test a setter raises on a shifted map, and re-discovery recovers."""
    inverter = await _discovered_inverter(
        mock_modbus_unit, build_sunspec_map([], float_mode=True)
    )
    # the data type changes, shifting all model addresses
    mock_modbus_unit.holding.clear()
    mock_modbus_unit.holding.update(build_sunspec_map([], float_mode=False))

    with pytest.raises(SunSpecMapShiftError):
        await _controls(inverter).set_power_limit(80.0)

    await inverter.discover()
    await _controls(inverter).set_power_limit(80.0)
    data_address = _model_data_address(inverter, IMMEDIATE_CONTROLS_MODEL_ID)
    assert mock_modbus_unit.holding[data_address + 3] == 8000
    assert mock_modbus_unit.holding[data_address + 7] == 1
