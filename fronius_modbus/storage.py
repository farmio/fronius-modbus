"""SunSpec Basic Storage Control Model (124).

Read-only battery state plus charge/discharge control setpoints. Writes
require "inverter control via Modbus" to be enabled on the device web
interface. Setpoint semantics per the Fronius documentation: ``InWRte`` /
``OutWRte`` limit charge/discharge rates in percent of ``WChaMax``; negative
values force charging/discharging (Solar.web shows "Forced Recharge").

The framework doesn't write dynamically-scaled fields, so setters read the
scale factor from the same component update and write raw values through
separate unscaled fields at the same addresses. Register addresses relative
to the model start, per the SunSpec model 124 definition.
"""

from enum import StrEnum
from typing import Final

from modbus_connection.model import sunspec as sunspec_fields
from modbus_connection.model.fields import NumberField

from .sunspec import SunSpecComponent, SunSpecError

# WChaMax data offset, used by the storage detection during discovery.
WCHA_MAX: Final = 0

# StorCtl_Mod bits activating the charge / discharge limits
_MODE_CHARGE_LIMIT: Final = 0b01
_MODE_DISCHARGE_LIMIT: Final = 0b10


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


class Storage(SunSpecComponent):
    """The storage model: battery state and charge/discharge setpoints."""

    charge_reference_power = sunspec_fields.uint16(2, scale_register=18, unit="W")
    state_of_charge = sunspec_fields.uint16(8, scale_register=22, unit="%")
    state: NumberField[StorageState] = NumberField(
        11, signed=False, nan=0xFFFF, convert=_STORAGE_STATES
    )
    minimum_reserve = sunspec_fields.uint16(7, scale_register=21, unit="%")
    minimum_reserve_raw = sunspec_fields.uint16(7, writable=True)
    minimum_reserve_sf = sunspec_fields.sunssf(21)
    charge_limit = sunspec_fields.int16(13, scale_register=25, unit="%")
    charge_limit_raw = sunspec_fields.int16(13, writable=True)
    discharge_limit = sunspec_fields.int16(12, scale_register=25, unit="%")
    discharge_limit_raw = sunspec_fields.int16(12, writable=True)
    limit_sf = sunspec_fields.sunssf(25)
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
        if (sf := self.limit_sf) is None:
            raise SunSpecError("InOutWRte scale factor not implemented")
        if revert_seconds:
            await self.write("revert_seconds", revert_seconds)
        if charge is not None:
            await self.write("charge_limit_raw", round(charge / 10.0**sf))
        if discharge is not None:
            await self.write("discharge_limit_raw", round(discharge / 10.0**sf))
        mode = (_MODE_CHARGE_LIMIT if charge is not None else 0) | (
            _MODE_DISCHARGE_LIMIT if discharge is not None else 0
        )
        await self.write("control_mode", mode)

    async def set_minimum_reserve(self, percent: float) -> None:
        """Set the minimum state of charge reserve in percent."""
        if not 0 <= percent <= 100:
            raise ValueError(f"minimum reserve out of range 0-100: {percent}")
        await self.async_update()
        if (sf := self.minimum_reserve_sf) is None:
            raise SunSpecError("MinRsvPct scale factor not implemented")
        await self.write("minimum_reserve_raw", round(percent / 10.0**sf))

    async def set_grid_charging(self, enabled: bool) -> None:
        """Allow or prevent charging the storage from the grid.

        AND-linked with the "battery charging from grid" web interface
        setting - enabling it here only takes effect if allowed there too.
        """
        await self.async_update()
        await self.write("grid_charging", 1 if enabled else 0)
