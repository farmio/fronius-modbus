"""Library for Fronius inverter Modbus TCP (SunSpec) interfaces.

Consumes a ``modbus_connection.ModbusUnit`` - connection lifecycle stays with
the caller.
"""

from .basic_settings import BasicSettings
from .common import Common
from .controls import Controls
from .inverter import GEN24_UNIT_ID, FroniusModbusInverter, datamanager_unit_id
from .inverter_model import (
    Inverter,
    InverterEvent,
    InverterFloat,
    InverterInteger,
    OperatingState,
)
from .measurements_status import MeasurementsStatus
from .mppt import ModuleRole, Mppt, MpptModule
from .storage import Storage, StorageState
from .sunspec import SunSpecError, SunSpecMapShiftError, SunSpecModel

__all__ = [
    "GEN24_UNIT_ID",
    "BasicSettings",
    "Common",
    "Controls",
    "FroniusModbusInverter",
    "Inverter",
    "InverterEvent",
    "InverterFloat",
    "InverterInteger",
    "MeasurementsStatus",
    "ModuleRole",
    "Mppt",
    "MpptModule",
    "OperatingState",
    "Storage",
    "StorageState",
    "SunSpecError",
    "SunSpecMapShiftError",
    "SunSpecModel",
    "datamanager_unit_id",
]
