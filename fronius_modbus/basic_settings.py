"""SunSpec Basic Settings Model (121).

Only the maximum power output is read. It is the reference the Immediate
Controls model's power limit is a percentage of, so it turns that limit into
watts. The model's other writable registers are grid code settings - voltage
limits and the offset to the point of common coupling - which this library
has no use for. Register addresses relative to the model start, per the
SunSpec model 121 definition.
"""

from modbus_connection.model import sunspec as sunspec_fields

from .sunspec import SunSpecComponent


class BasicSettings(SunSpecComponent):
    """The Basic Settings model: the inverter's nominal power output."""

    max_power = sunspec_fields.uint16(2, scale_register=22, unit="W")
