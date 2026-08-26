"""Tests for the Basic Settings model (121)."""

import pytest
from modbus_connection.mock import MockModbusUnit

from fronius_modbus import FroniusModbusInverter
from fronius_modbus.testing import build_sunspec_map


async def _discovered_inverter(
    unit: MockModbusUnit, registers: dict[int, int]
) -> FroniusModbusInverter:
    unit.holding.update(registers)
    inverter = FroniusModbusInverter(unit, has_storage=False)
    await inverter.discover()
    await inverter.async_update()
    return inverter


@pytest.mark.parametrize("max_power", [10000, 5000, 27000])
async def test_max_power(mock_modbus_unit: MockModbusUnit, max_power: int) -> None:
    """Test the nominal power output is read and scaled."""
    inverter = await _discovered_inverter(
        mock_modbus_unit, build_sunspec_map([], max_power=max_power)
    )
    assert inverter.basic_settings is not None
    assert inverter.basic_settings.max_power == max_power


async def test_power_limit_in_watts(mock_modbus_unit: MockModbusUnit) -> None:
    """Test the Immediate Controls limit resolves to watts against WMax.

    The limit is a percentage of WMax, so the two models together are what
    turns it into a power value.
    """
    inverter = await _discovered_inverter(
        mock_modbus_unit, build_sunspec_map([], max_power=10000)
    )
    assert inverter.controls is not None
    assert inverter.basic_settings is not None
    assert inverter.controls.power_limit == 100.0

    await inverter.controls.set_power_limit(60.0)
    await inverter.async_update()

    assert inverter.controls.power_limit == 60.0
    watts = inverter.controls.power_limit / 100 * inverter.basic_settings.max_power
    assert watts == 6000.0
