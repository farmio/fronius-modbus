"""Tests for SunSpec model chain discovery."""

import pytest
from modbus_connection.mock import MockModbusUnit

from fronius_modbus import SunSpecError, SunSpecModel
from fronius_modbus.sunspec import SUNSPEC_BASE_ADDRESS, scan
from fronius_modbus.testing import MpptModuleSpec, build_sunspec_map


async def test_scan(mock_modbus_unit: MockModbusUnit) -> None:
    """Test walking a valid model chain."""
    mock_modbus_unit.holding.update(
        build_sunspec_map([MpptModuleSpec(id_str="String 1")], float_mode=True)
    )
    models = await scan(mock_modbus_unit, SUNSPEC_BASE_ADDRESS)
    assert models == {
        1: [SunSpecModel(model_id=1, address=40002, length=66)],
        113: [SunSpecModel(model_id=113, address=40070, length=60)],
        123: [SunSpecModel(model_id=123, address=40132, length=24)],
        160: [SunSpecModel(model_id=160, address=40158, length=28)],
    }


async def test_scan_no_sunspec(mock_modbus_unit: MockModbusUnit) -> None:
    """Test model discovery on a device without SunSpec marker."""
    with pytest.raises(SunSpecError, match="No SunSpec marker"):
        await scan(mock_modbus_unit, SUNSPEC_BASE_ADDRESS)


async def test_scan_unterminated(mock_modbus_unit: MockModbusUnit) -> None:
    """Test model discovery on a device with an unterminated model chain."""
    mock_modbus_unit.holding.update({40000: 0x5375, 40001: 0x6E53})
    # all further registers read 0 - a chain of zero-length models without end
    with pytest.raises(SunSpecError, match="not terminated"):
        await scan(mock_modbus_unit, SUNSPEC_BASE_ADDRESS)
