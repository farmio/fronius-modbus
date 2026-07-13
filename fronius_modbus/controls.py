"""SunSpec Immediate Controls Model (123) - inverter output power limiting.

Writes require "inverter control via Modbus" to be enabled on the device web
interface, and may be overridden by higher-priority control sources (IO
control, dynamic power reduction).

The framework doesn't write dynamically-scaled fields, so setters read the
scale factor from the same component update and write raw values through
separate unscaled fields at the same addresses. Register addresses relative
to the model start, per the SunSpec model 123 definition.
"""

from modbus_connection import ModbusExceptionError
from modbus_connection.model import sunspec as sunspec_fields
from modbus_connection.model.fields import NumberField

from .sunspec import SunSpecComponent, SunSpecError


class Controls(SunSpecComponent):
    """The Immediate Controls model: output power limit state and setters."""

    # harmless read-write register used to probe write access
    connect_window = sunspec_fields.uint16(2, writable=True)
    power_limit = sunspec_fields.uint16(5, scale_register=23, unit="%")
    power_limit_raw = sunspec_fields.uint16(5, writable=True)
    revert_seconds = sunspec_fields.uint16(7, writable=True)
    enabled: NumberField[bool] = NumberField(
        9, signed=False, nan=0xFFFF, convert=bool, writable=True
    )
    power_limit_sf = sunspec_fields.sunssf(23)

    async def set_power_limit(self, percent: float, revert_seconds: int) -> None:
        """Limit output power to ``percent`` of the nominal power and enable.

        ``revert_seconds`` > 0 auto-reverts the limit if it isn't refreshed -
        a safety net for controllers that may die; 0 keeps it until disabled.
        """
        if not 0 <= percent <= 100:
            raise ValueError(f"power limit out of range 0-100: {percent}")
        if not 0 <= revert_seconds <= 0xFFFF:
            raise ValueError(f"revert_seconds out of range: {revert_seconds}")
        await self.async_update()
        if (sf := self.power_limit_sf) is None:
            raise SunSpecError("WMaxLimPct scale factor not implemented")
        await self.write("revert_seconds", revert_seconds)
        await self.write("power_limit_raw", round(percent / 10.0**sf))
        await self.write("enabled", 1)

    async def clear_power_limit(self) -> None:
        """Disable the output power limit."""
        await self.async_update()
        await self.write("enabled", 0)

    async def probe_write_access(self) -> bool:
        """Check whether the device accepts Modbus writes.

        There is no register exposing the "inverter control via Modbus" web
        interface setting, so this writes a harmless register's current value
        back to itself and reports whether the device rejected it.
        """
        await self.async_update()
        current = self.connect_window
        try:
            await self.write(
                "connect_window", int(current) if current is not None else 0
            )
        except ModbusExceptionError:
            return False
        return True
