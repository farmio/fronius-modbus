"""Tests for the Measurements Status model (122)."""

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


@pytest.mark.parametrize("ac_energy_total", [1, 1234, 50_000_000, 5_000_000_000])
async def test_ac_energy_total(
    mock_modbus_unit: MockModbusUnit, ac_energy_total: int
) -> None:
    """Test the lifetime AC energy is read at full acc64 resolution."""
    inverter = await _discovered_inverter(
        mock_modbus_unit,
        build_sunspec_map([], measurements_status_ac_energy_total=ac_energy_total),
    )
    assert inverter.measurements_status is not None
    assert inverter.measurements_status.ac_energy_total == ac_energy_total


async def test_ac_energy_total_not_accumulated(
    mock_modbus_unit: MockModbusUnit,
) -> None:
    """Test a zero ActWh, the acc64 "not accumulated" sentinel, decodes to None."""
    inverter = await _discovered_inverter(
        mock_modbus_unit, build_sunspec_map([], measurements_status_ac_energy_total=0)
    )
    assert inverter.measurements_status is not None
    assert inverter.measurements_status.ac_energy_total is None


@pytest.mark.parametrize("power_limit_active", [True, False])
async def test_power_limit_active(
    mock_modbus_unit: MockModbusUnit, power_limit_active: bool
) -> None:
    """Test the output power limit active flag decodes StActCtl's FixedW bit."""
    inverter = await _discovered_inverter(
        mock_modbus_unit,
        build_sunspec_map(
            [], measurements_status_power_limit_active=power_limit_active
        ),
    )
    assert inverter.measurements_status is not None
    assert inverter.measurements_status.power_limit_active is power_limit_active
