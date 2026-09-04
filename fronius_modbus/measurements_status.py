"""SunSpec Measurements Status Model (122).

Only the lifetime AC-side energy and the output power limit's active flag are
read. The energy is exposed at full acc64 resolution without a scale factor,
so it doesn't quantise like the float inverter model's `energy_total` at high
readings. Register addresses relative to the model start, per the SunSpec
model 122 definition.

`power_limit_active` is True while the inverter is actually applying a fixed
output power limit (WMaxLimPct). It reflects the applied state, so it lags
`Controls.enabled` by a few seconds in both directions, after enabling and
again after clearing. It does not report dynamic export limiting
(Einspeisebegrenzung / Soft Limit), which curtails without setting this bit.

The model is always encoded as integers / accumulators regardless of the
float / int+SF setting of the inverter and meter models - only its address
shifts.
"""

from modbus_connection.model import bit
from modbus_connection.model import sunspec as sunspec_fields

from .sunspec import SunSpecComponent


class MeasurementsStatus(SunSpecComponent):
    """The Measurements Status model: lifetime AC energy and control status."""

    ac_energy_total = sunspec_fields.acc64(5, unit="Wh")
    # FixedW is bit 0 of StActCtl's low word (big-endian bitfield32 at 35-36)
    # bit() has no not-implemented handling: an all-ones bitfield32 decodes
    # to True, harmless here since StActCtl is implemented, but this would
    # misread StSetLimMsk two registers below on devices that leave it
    # unimplemented.
    power_limit_active = bit(36, 0)
