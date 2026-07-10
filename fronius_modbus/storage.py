"""SunSpec Basic Storage Control Model (124).

Read-only battery state plus charge/discharge control setpoints. Writes
require "inverter control via Modbus" to be enabled on the device web
interface. Setpoint semantics per the Fronius documentation: ``InWRte`` /
``OutWRte`` limit charge/discharge rates in percent of ``WChaMax``; negative
values force charging/discharging (Solar.web shows "Forced Recharge").

The framework doesn't write dynamically-scaled fields, so setters read the
scale factor from the same component update and write raw values through
separate unscaled fields at the same addresses.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from modbus_connection import ModbusUnit
from modbus_connection.model import Component
from modbus_connection.model import sunspec as sunspec_fields

from .sunspec import SunSpecError, SunSpecModel, check_model_header

# Data block offsets, relative to the first register after the 2-register
# model header (per the SunSpec model 124 definition).
WCHA_MAX: Final = 0
_STOR_CTL_MOD: Final = 3
_MIN_RSV_PCT: Final = 5
_CHA_STATE: Final = 6
_CHA_ST: Final = 9
_OUT_W_RTE: Final = 10
_IN_W_RTE: Final = 11
_IN_OUT_W_RTE_RVRT_TMS: Final = 13
_CHA_GRI_SET: Final = 15
_WCHA_MAX_SF: Final = 16
_MIN_RSV_PCT_SF: Final = 19
_CHA_STATE_SF: Final = 20
_IN_OUT_W_RTE_SF: Final = 23

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


@dataclass(frozen=True)
class StorageData:
    """Values of the Basic Storage Control Model."""

    state_of_charge: float | None  # percent
    state: StorageState | None
    charge_reference_power: float | None  # WChaMax, W
    minimum_reserve: float | None  # percent
    charge_limit: float | None  # InWRte, percent of WChaMax
    discharge_limit: float | None  # OutWRte, percent of WChaMax
    charge_limit_enabled: bool | None
    discharge_limit_enabled: bool | None
    grid_charging: bool | None


@dataclass(frozen=True)
class StorageControls:
    """Bound operations on a discovered Basic Storage Control model."""

    read: Callable[[], Awaitable[StorageData]]
    set_limits: Callable[[float | None, float | None, int], Awaitable[None]]
    set_minimum_reserve: Callable[[float], Awaitable[None]]
    set_grid_charging: Callable[[bool], Awaitable[None]]


def build_storage_controls(unit: ModbusUnit, model: SunSpecModel) -> StorageControls:
    """Create operations for the storage model found at its discovered address."""
    data_address = model.address + 2

    class StorageComponent(Component):
        """The storage model, including its header for shift detection."""

        model_id = sunspec_fields.uint16(model.address)
        model_length = sunspec_fields.uint16(model.address + 1)
        charge_reference_power = sunspec_fields.uint16(
            data_address + WCHA_MAX,
            scale_register=data_address + _WCHA_MAX_SF,
            unit="W",
        )
        state_of_charge = sunspec_fields.uint16(
            data_address + _CHA_STATE,
            scale_register=data_address + _CHA_STATE_SF,
            unit="%",
        )
        state = sunspec_fields.enum16(data_address + _CHA_ST)
        minimum_reserve = sunspec_fields.uint16(
            data_address + _MIN_RSV_PCT,
            scale_register=data_address + _MIN_RSV_PCT_SF,
            unit="%",
        )
        minimum_reserve_raw = sunspec_fields.uint16(
            data_address + _MIN_RSV_PCT, writable=True
        )
        minimum_reserve_sf = sunspec_fields.sunssf(data_address + _MIN_RSV_PCT_SF)
        charge_limit = sunspec_fields.int16(
            data_address + _IN_W_RTE,
            scale_register=data_address + _IN_OUT_W_RTE_SF,
            unit="%",
        )
        charge_limit_raw = sunspec_fields.int16(data_address + _IN_W_RTE, writable=True)
        discharge_limit = sunspec_fields.int16(
            data_address + _OUT_W_RTE,
            scale_register=data_address + _IN_OUT_W_RTE_SF,
            unit="%",
        )
        discharge_limit_raw = sunspec_fields.int16(
            data_address + _OUT_W_RTE, writable=True
        )
        limit_sf = sunspec_fields.sunssf(data_address + _IN_OUT_W_RTE_SF)
        control_mode = sunspec_fields.bitfield16(
            data_address + _STOR_CTL_MOD, writable=True
        )
        revert_seconds = sunspec_fields.uint16(
            data_address + _IN_OUT_W_RTE_RVRT_TMS, writable=True
        )
        grid_charging = sunspec_fields.enum16(
            data_address + _CHA_GRI_SET, writable=True
        )

    component = StorageComponent(unit)

    async def update_checked() -> None:
        await component.async_update()
        check_model_header(model, component.model_id, component.model_length, "Storage")

    async def read() -> StorageData:
        await update_checked()
        state = component.state
        mode = component.control_mode
        grid = component.grid_charging
        return StorageData(
            state_of_charge=component.state_of_charge,
            state=_STORAGE_STATES.get(int(state)) if state is not None else None,
            charge_reference_power=component.charge_reference_power,
            minimum_reserve=component.minimum_reserve,
            charge_limit=component.charge_limit,
            discharge_limit=component.discharge_limit,
            charge_limit_enabled=(
                bool(int(mode) & _MODE_CHARGE_LIMIT) if mode is not None else None
            ),
            discharge_limit_enabled=(
                bool(int(mode) & _MODE_DISCHARGE_LIMIT) if mode is not None else None
            ),
            grid_charging=bool(grid) if grid is not None else None,
        )

    async def set_limits(
        charge: float | None, discharge: float | None, revert_seconds: int
    ) -> None:
        """Limit charge / discharge rates in percent of WChaMax.

        ``None`` deactivates the respective limit. Negative values force
        charging / discharging. ``revert_seconds`` > 0 auto-reverts the
        limits if they aren't refreshed; support varies by device generation.
        """
        for limit in (charge, discharge):
            if limit is not None and not -100 <= limit <= 100:
                raise ValueError(f"limit out of range -100..100: {limit}")
        await update_checked()
        if (sf := component.limit_sf) is None:
            raise SunSpecError("InOutWRte scale factor not implemented")
        if revert_seconds:
            await component.write("revert_seconds", revert_seconds)
        if charge is not None:
            await component.write("charge_limit_raw", round(charge / 10.0**sf))
        if discharge is not None:
            await component.write("discharge_limit_raw", round(discharge / 10.0**sf))
        mode = (_MODE_CHARGE_LIMIT if charge is not None else 0) | (
            _MODE_DISCHARGE_LIMIT if discharge is not None else 0
        )
        await component.write("control_mode", mode)

    async def set_minimum_reserve(percent: float) -> None:
        """Set the minimum state of charge reserve in percent."""
        if not 0 <= percent <= 100:
            raise ValueError(f"minimum reserve out of range 0-100: {percent}")
        await update_checked()
        if (sf := component.minimum_reserve_sf) is None:
            raise SunSpecError("MinRsvPct scale factor not implemented")
        await component.write("minimum_reserve_raw", round(percent / 10.0**sf))

    async def set_grid_charging(enabled: bool) -> None:
        """Allow or prevent charging the storage from the grid.

        AND-linked with the "battery charging from grid" web interface
        setting - enabling it here only takes effect if allowed there too.
        """
        await update_checked()
        await component.write("grid_charging", 1 if enabled else 0)

    return StorageControls(
        read=read,
        set_limits=set_limits,
        set_minimum_reserve=set_minimum_reserve,
        set_grid_charging=set_grid_charging,
    )
