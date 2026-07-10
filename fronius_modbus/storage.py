"""SunSpec Basic Storage Control Model (124) - read-only values.

Only exposed by storage-capable inverters. The control setpoints in this
model are not read here; they are candidates for future write support.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from modbus_connection import ModbusUnit
from modbus_connection.model import Component
from modbus_connection.model import sunspec as sunspec_fields

from .sunspec import SunSpecModel, check_model_header

# Data block offsets, relative to the first register after the 2-register
# model header (per the SunSpec model 124 definition).
WCHA_MAX: Final = 0
_CHA_STATE: Final = 6
_CHA_ST: Final = 9
_WCHA_MAX_SF: Final = 16
_CHA_STATE_SF: Final = 20

type StorageDataReader = Callable[[], Awaitable["StorageData"]]


class StorageState(StrEnum):
    """SunSpec storage charge status (ChaSt)."""

    OFF = "off"
    EMPTY = "empty"
    DISCHARGING = "discharging"
    CHARGING = "charging"
    FULL = "full"
    HOLDING = "holding"
    TESTING = "testing"


_STORAGE_STATES: Final = {
    1: StorageState.OFF,
    2: StorageState.EMPTY,
    3: StorageState.DISCHARGING,
    4: StorageState.CHARGING,
    5: StorageState.FULL,
    6: StorageState.HOLDING,
    7: StorageState.TESTING,
}


@dataclass(frozen=True)
class StorageData:
    """Read-only values of the Basic Storage Control Model."""

    state_of_charge: float | None  # percent
    state: StorageState | None
    charge_reference_power: float | None  # WChaMax, W


def build_storage_reader(unit: ModbusUnit, model: SunSpecModel) -> StorageDataReader:
    """Create a reader for the storage model found at its discovered address."""
    data_address = model.address + 2

    class StorageComponent(Component):
        """The storage model, including its header for shift detection."""

        model_id = sunspec_fields.uint16(model.address)
        model_length = sunspec_fields.uint16(model.address + 1)
        charge_reference_power = sunspec_fields.uint16(
            data_address + WCHA_MAX,
            scale_register=data_address + _WCHA_MAX_SF,
            unit="W",
        )
        state_of_charge = sunspec_fields.uint16(
            data_address + _CHA_STATE,
            scale_register=data_address + _CHA_STATE_SF,
            unit="%",
        )
        state = sunspec_fields.enum16(data_address + _CHA_ST)

    component = StorageComponent(unit)

    async def read() -> StorageData:
        await component.async_update()
        check_model_header(model, component.model_id, component.model_length, "Storage")
        state = component.state
        return StorageData(
            state_of_charge=component.state_of_charge,
            state=_STORAGE_STATES.get(int(state)) if state is not None else None,
            charge_reference_power=component.charge_reference_power,
        )

    return read
