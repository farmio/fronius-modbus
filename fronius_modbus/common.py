"""SunSpec Common Model (1) - device identification."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Final

from modbus_connection import ModbusUnit
from modbus_connection.model import Component
from modbus_connection.model import sunspec as sunspec_fields

from .sunspec import SunSpecModel, check_model_header

# Data block offsets, relative to the first register after the 2-register
# model header (per the SunSpec model 1 definition).
_MANUFACTURER: Final = 0  # 16 registers
_MODEL: Final = 16  # 16 registers
_OPTIONS: Final = 32  # 8 registers
_VERSION: Final = 40  # 8 registers
_SERIAL_NUMBER: Final = 48  # 16 registers

type DeviceIdentityReader = Callable[[], Awaitable["DeviceIdentity"]]


@dataclass(frozen=True)
class DeviceIdentity:
    """Device identification from the SunSpec Common Model."""

    manufacturer: str | None
    model: str | None
    options: str | None
    software_version: str | None
    serial_number: str | None


def build_device_identity_reader(
    unit: ModbusUnit, model: SunSpecModel
) -> DeviceIdentityReader:
    """Create a reader for the Common Model found at its discovered address."""
    data_address = model.address + 2

    class CommonComponent(Component):
        """The Common Model, including its header for shift detection."""

        model_id = sunspec_fields.uint16(model.address)
        model_length = sunspec_fields.uint16(model.address + 1)
        manufacturer = sunspec_fields.string(data_address + _MANUFACTURER, 16)
        device_model = sunspec_fields.string(data_address + _MODEL, 16)
        options = sunspec_fields.string(data_address + _OPTIONS, 8)
        version = sunspec_fields.string(data_address + _VERSION, 8)
        serial_number = sunspec_fields.string(data_address + _SERIAL_NUMBER, 16)

    component = CommonComponent(unit)

    async def read() -> DeviceIdentity:
        await component.async_update()
        check_model_header(model, component.model_id, component.model_length, "Common")
        return DeviceIdentity(
            manufacturer=component.manufacturer,
            model=component.device_model,
            options=component.options,
            software_version=component.version,
            serial_number=component.serial_number,
        )

    return read
