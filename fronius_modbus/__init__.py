"""Library for Fronius inverter Modbus TCP (SunSpec) interfaces.

Consumes a ``modbus_connection.ModbusUnit`` - connection lifecycle stays with
the caller.
"""

from .inverter import GEN24_UNIT_ID, FroniusModbusInverter, datamanager_unit_id
from .mppt import ModuleRole, MpptData, MpptModule
from .sunspec import SunSpecError, SunSpecModel

__all__ = [
    "GEN24_UNIT_ID",
    "FroniusModbusInverter",
    "ModuleRole",
    "MpptData",
    "MpptModule",
    "SunSpecError",
    "SunSpecModel",
    "datamanager_unit_id",
]
