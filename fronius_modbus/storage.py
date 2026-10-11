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

from enum import IntEnum, StrEnum

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


class ForcedMode(StrEnum):
    """Which way a negative rate limit forces the battery."""

    OFF = "off"
    CHARGE = "charge"
    DISCHARGE = "discharge"


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
        limits if they aren't refreshed; support varies by device generation.
        """
        for limit in (charge, discharge):
            if limit is not None and not -100 <= limit <= 100:
                raise ValueError(f"limit out of range -100..100: {limit}")
        await self.async_update()
        if revert_seconds:
            await self.write("revert_seconds", revert_seconds)
        # the device refuses both rates negative, which reversing a forced
        # direction passes through unless the negative rate goes last
        rates = {"charge_limit": charge, "discharge_limit": discharge}
        for field, limit in sorted(rates.items(), key=lambda rate: (rate[1] or 0) < 0):
            if limit is not None:
                await self.write(field, limit)
        await self.write("charge_limit_enabled", charge is not None)
        await self.write("discharge_limit_enabled", discharge is not None)

    @property
    def forced_mode(self) -> ForcedMode | None:
        """Return which way an active negative rate forces the battery.

        A negative discharge rate forces charging and a negative charge rate
        forces discharging - whoever wrote it, so forcing by another
        controller reads back too.
        """
        if self.discharge_limit_enabled and _negative(self.discharge_limit):
            return ForcedMode.CHARGE
        if self.charge_limit_enabled and _negative(self.charge_limit):
            return ForcedMode.DISCHARGE
        if None in (
            self.charge_limit,
            self.discharge_limit,
            self.charge_limit_enabled,
            self.discharge_limit_enabled,
        ):
            return None
        return ForcedMode.OFF

    async def set_forced_mode(self, mode: ForcedMode) -> None:
        """Force the battery to charge or discharge at full power, or stop.

        Forcing takes over the rate of the opposite direction - a negative
        discharge rate is what forces charging - and charging also enables
        grid charging. The rate of the forced direction is left alone, so an
        active limit there caps the forced power: the device refuses a forced
        rate beyond it, so the forced rate is capped to match.

        Stopping releases a forcing rate at 100% and disabled, and grid
        charging with forced charging. A rate is released first when the
        other direction takes over, as the device refuses both negative.
        """
        await self.async_update()
        # writes don't update the model, so decide everything from this read
        charge, discharge = self.charge_limit, self.discharge_limit
        charge_cap = _user_limit(charge, self.charge_limit_enabled)
        discharge_cap = _user_limit(discharge, self.discharge_limit_enabled)
        if mode is not ForcedMode.CHARGE and _negative(discharge):
            await self._release("discharge_limit", "discharge_limit_enabled")
            await self.write("grid_charging", False)
        if mode is not ForcedMode.DISCHARGE and _negative(charge):
            await self._release("charge_limit", "charge_limit_enabled")
        if mode is ForcedMode.CHARGE:
            await self.write("grid_charging", True)
            await self._force("discharge_limit", "discharge_limit_enabled", charge_cap)
        elif mode is ForcedMode.DISCHARGE:
            await self._force("charge_limit", "charge_limit_enabled", discharge_cap)

    async def _release(self, field: str, enable_field: str) -> None:
        """Stop a rate from forcing, leaving no negative value behind."""
        await self.write(enable_field, False)
        await self.write(field, 100)

    async def _force(self, field: str, enable_field: str, cap: float | None) -> None:
        """Force with the opposite rate, capped by the user's limit."""
        await self.write(field, -(100 if cap is None else min(100, cap)))
        await self.write(enable_field, True)

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


def _negative(limit: float | None) -> bool:
    return limit is not None and limit < 0


def _user_limit(limit: float | None, enabled: bool | None) -> float | None:
    """Return an active limit the user set - a negative one is forcing."""
    if not enabled or limit is None or limit < 0:
        return None
    return limit
