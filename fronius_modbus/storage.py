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
from typing import Final

from modbus_connection.model import sunspec as sunspec_fields
from modbus_connection.model.fields import NumberField

from .sunspec import SunSpecComponent

# StorCtl_Mod bits activating the charge / discharge limits
_MODE_CHARGE_LIMIT: Final = 0b01
_MODE_DISCHARGE_LIMIT: Final = 0b10


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
    control_mode = sunspec_fields.bitfield16(5, writable=True)
    revert_seconds = sunspec_fields.uint16(15, writable=True)
    grid_charging: NumberField[bool] = NumberField(
        17, signed=False, nan=0xFFFF, convert=bool, writable=True
    )

    @property
    def charge_limit_enabled(self) -> bool | None:
        """Whether the charge limit (InWRte) is active."""
        return self._mode_bit(_MODE_CHARGE_LIMIT)

    @property
    def discharge_limit_enabled(self) -> bool | None:
        """Whether the discharge limit (OutWRte) is active."""
        return self._mode_bit(_MODE_DISCHARGE_LIMIT)

    def _mode_bit(self, bit: int) -> bool | None:
        if (mode := self.control_mode) is None:
            return None
        return bool(int(mode) & bit)

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
        if charge is not None:
            await self.write("charge_limit", charge)
        if discharge is not None:
            await self.write("discharge_limit", discharge)
        mode = (_MODE_CHARGE_LIMIT if charge is not None else 0) | (
            _MODE_DISCHARGE_LIMIT if discharge is not None else 0
        )
        await self.write("control_mode", mode)

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
