"""Library for Fronius inverter Modbus TCP (SunSpec) interfaces.

Consumes a ``modbus_connection.ModbusUnit`` - connection lifecycle stays with
the caller.
"""

from .common import DeviceIdentity
from .controls import PowerLimit
from .inverter import GEN24_UNIT_ID, FroniusModbusInverter, datamanager_unit_id
from .inverter_model import InverterData, OperatingState
from .mppt import ModuleRole, MpptData, MpptModule
from .storage import StorageData, StorageState
from .sunspec import SunSpecError, SunSpecModel

__all__ = [
    "GEN24_UNIT_ID",
    "DeviceIdentity",
    "FroniusModbusInverter",
    "InverterData",
    "ModuleRole",
    "MpptData",
    "MpptModule",
    "OperatingState",
    "PowerLimit",
    "StorageData",
    "StorageState",
    "SunSpecError",
    "SunSpecModel",
    "datamanager_unit_id",
]
