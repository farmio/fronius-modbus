"""SunSpec Common Model (1) - device identification."""

from modbus_connection.model import sunspec as sunspec_fields

from .sunspec import SunSpecComponent


class CommonModel(SunSpecComponent):
    """Manufacturer, model, version and serial number of the device."""

    model_name = "Common"

    manufacturer = sunspec_fields.string(2, 16)
    model = sunspec_fields.string(18, 16)
    options = sunspec_fields.string(34, 8)
    software_version = sunspec_fields.string(42, 8)
    serial_number = sunspec_fields.string(50, 16)
