"""SunSpec Basic Storage Control Model (124).

Read-only battery state plus charge/discharge control setpoints. Writes
require "inverter control via Modbus" to be enabled on the device web
interface. Setpoint semantics per the Fronius documentation: ``InWRte`` /
``OutWRte`` limit charge/discharge rates in percent of ``WChaMax``; negative
values force charging/discharging (Solar.web shows "Forced Recharge").

Setters refresh the model first, so the header check catches a shifted
register map before anything is written. Register addresses relative to the
model start, per the SunSpec model 124 definition.
"""

from enum import IntEnum

from modbus_connection.model import bit
from modbus_connection.model import sunspec as sunspec_fields

from .sunspec import SunSpecComponent


class StorageState(IntEnum):
    """SunSpec storage charge status (ChaSt)."""

    OFF = 1
    EMPTY = 2
    DISCHARGING = 3
    CHARGING = 4
    FULL = 5
    HOLDING = 6
    TESTING = 7


class Storage(SunSpecComponent):
    """The storage model: battery state and charge/discharge setpoints."""

    charge_reference_power = sunspec_fields.uint16(2, scale_register=18, unit="W")
    state_of_charge = sunspec_fields.uint16(8, scale_register=22, unit="%")
    state = sunspec_fields.enum16(11, StorageState)
    minimum_reserve = sunspec_fields.uint16(
        7, scale_register=21, unit="%", writable=True
    )
    charge_limit = sunspec_fields.int16(13, scale_register=25, unit="%", writable=True)
    discharge_limit = sunspec_fields.int16(
        12, scale_register=25, unit="%", writable=True
    )
    revert_seconds = sunspec_fields.uint16(15, writable=True)
    grid_charging = sunspec_fields.boolean(17, writable=True)
    # StorCtl_Mod packs both limit switches into one register
    charge_limit_enabled = bit(5, 0, writable=True)
    discharge_limit_enabled = bit(5, 1, writable=True)

    async def set_limits(
        self,
        *,
        charge: float | None = None,
        discharge: float | None = None,
        revert_seconds: int = 0,
    ) -> None:
        """Limit charge / discharge rates in percent of WChaMax.

        ``None`` deactivates the respective limit. Negative values force
        charging / discharging. ``revert_seconds`` > 0 auto-reverts the
        limits unless one is written again within the period - reading the
        model does not restart it, whatever the Fronius manual says.
        """
        for limit in (charge, discharge):
            if limit is not None and not -100 <= limit <= 100:
                raise ValueError(f"limit out of range -100..100: {limit}")
        if charge is not None and discharge is not None and charge + discharge < 0:
            raise ValueError(
                f"limits leave an empty window: charge {charge}, discharge {discharge}"
            )
        await self.async_update()
        if revert_seconds:
            await self.write("revert_seconds", revert_seconds)
        # With both limits enabled the device refuses an empty window - a
        # charge rate below the negated discharge rate, both negative for
        # one. It checks when a limit is enabled, so a limit going off is
        # disabled first and a limit going on is enabled last. With both
        # staying on, the rates go in the order that keeps the window open:
        # new charge + old discharge and old charge + new discharge sum up
        # to the old window plus the new one, so one of them is not negative.
        enabled = {
            "charge_limit_enabled": charge is not None,
            "discharge_limit_enabled": discharge is not None,
        }
        rates = [("charge_limit", charge), ("discharge_limit", discharge)]
        if charge is not None and charge + (self.discharge_limit or 0) < 0:
            rates.reverse()
        for enable_field, on in enabled.items():
            if not on:
                await self.write(enable_field, False)
        for field, limit in rates:
            if limit is not None:
                await self.write(field, limit)
        for enable_field, on in enabled.items():
            if on:
                await self.write(enable_field, True)

    async def refresh_limits(self, *, revert_seconds: int) -> bool:
        """Send the active charge / discharge limits again to restart the period.

        The storage counterpart of ``Controls.refresh_power_limit``: decided
        on a fresh read, returns whether a limit was in force, and writes the
        period along with the limits.
        """
        await self.async_update()
        charge_on = self.charge_limit_enabled is True
        discharge_on = self.discharge_limit_enabled is True
        if not charge_on and not discharge_on:
            return False
        await self.write("revert_seconds", revert_seconds)
        if charge_on and self.charge_limit is not None:
            await self.write("charge_limit", self.charge_limit)
        if discharge_on and self.discharge_limit is not None:
            await self.write("discharge_limit", self.discharge_limit)
        # both switches share one register, so one merging write re-sends both
        await self.write(
            "charge_limit_enabled" if charge_on else "discharge_limit_enabled", True
        )
        return True

    async def set_minimum_reserve(self, percent: float) -> None:
        """Set the minimum state of charge reserve in percent."""
        if not 0 <= percent <= 100:
            raise ValueError(f"minimum reserve out of range 0-100: {percent}")
        await self.async_update()
        await self.write("minimum_reserve", percent)

    async def set_grid_charging(self, enabled: bool) -> None:
        """Allow or prevent charging the storage from the grid.

        AND-linked with the "battery charging from grid" web interface
        setting - enabling it here only takes effect if allowed there too.
        """
        await self.async_update()
        await self.write("grid_charging", enabled)
