"""Library for Fronius inverter Modbus TCP (SunSpec) interfaces.

Consumes a ``modbus_connection.ModbusUnit`` - connection lifecycle stays with
the caller.
"""

from .common import CommonModel
from .controls import ControlsModel
from .inverter import GEN24_UNIT_ID, FroniusModbusInverter, datamanager_unit_id
from .inverter_model import (
    InverterFloatModel,
    InverterIntegerModel,
    InverterModel,
    OperatingState,
)
from .mppt import ModuleRole, MpptModel, MpptModule
from .storage import StorageModel, StorageState
from .sunspec import SunSpecError, SunSpecModel

__all__ = [
    "GEN24_UNIT_ID",
    "CommonModel",
    "ControlsModel",
    "FroniusModbusInverter",
    "InverterFloatModel",
    "InverterIntegerModel",
    "InverterModel",
    "ModuleRole",
    "MpptModel",
    "MpptModule",
    "OperatingState",
    "StorageModel",
    "StorageState",
    "SunSpecError",
    "SunSpecModel",
    "datamanager_unit_id",
]
