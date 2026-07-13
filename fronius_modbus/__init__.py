"""Library for Fronius inverter Modbus TCP (SunSpec) interfaces.

Consumes a ``modbus_connection.ModbusUnit`` - connection lifecycle stays with
the caller.
"""

from .common import Common
from .controls import Controls
from .inverter import GEN24_UNIT_ID, FroniusModbusInverter, datamanager_unit_id
from .inverter_model import (
    Inverter,
    InverterFloat,
    InverterInteger,
    OperatingState,
)
from .mppt import ModuleRole, Mppt, MpptModule
from .storage import Storage, StorageState
from .sunspec import SunSpecError, SunSpecModel

__all__ = [
    "GEN24_UNIT_ID",
    "Common",
    "Controls",
    "FroniusModbusInverter",
    "Inverter",
    "InverterFloat",
    "InverterInteger",
    "ModuleRole",
    "Mppt",
    "MpptModule",
    "OperatingState",
    "Storage",
    "StorageState",
    "SunSpecError",
    "SunSpecModel",
    "datamanager_unit_id",
]
