"""SunSpec Measurements_Status Model (122).

Only the lifetime AC-side energy and the output power limit's active flag are
read. The energy is exposed at full acc64 resolution without a scale factor,
so it doesn't quantise like the float inverter model's `energy_total` at high
readings. Register addresses relative to the model start, per the SunSpec
model 122 definition.
"""

from modbus_connection.model import bit
from modbus_connection.model import sunspec as sunspec_fields

from .sunspec import SunSpecComponent


class MeasurementsStatus(SunSpecComponent):
    """The Measurements_Status model: lifetime AC energy and control status."""

    ac_energy_total = sunspec_fields.acc64(5, unit="Wh")
    # StActCtl low word (big-endian bitfield32, 35-36); bit 0 = FixedW
    power_limit_active = bit(36, 0)
