"""SunSpec Immediate Controls Model (123) - inverter output power limiting.

Writes require "inverter control via Modbus" to be enabled on the device web
interface, and may be overridden by higher-priority control sources (IO
control, dynamic power reduction).

The framework doesn't write dynamically-scaled fields, so setters read the
scale factor from the same component update and write raw values through
separate unscaled fields at the same addresses.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Final

from modbus_connection import ModbusExceptionError, ModbusUnit
from modbus_connection.model import Component
from modbus_connection.model import sunspec as sunspec_fields

from .sunspec import SunSpecError, SunSpecModel, check_model_header

# Data block offsets, relative to the first register after the 2-register
# model header (per the SunSpec model 123 definition).
_CONN_WIN_TMS: Final = 0
_WMAX_LIM_PCT: Final = 3
_WMAX_LIM_PCT_RVRT_TMS: Final = 5
_WMAX_LIM_ENA: Final = 7
_WMAX_LIM_PCT_SF: Final = 21


@dataclass(frozen=True)
class PowerLimit:
    """Output power limit state of the Immediate Controls model."""

    percent: float | None  # WMaxLimPct, percent of the nominal power WMax
    enabled: bool | None
    revert_seconds: int | None  # 0 means the limit stays until disabled


@dataclass(frozen=True)
class ImmediateControls:
    """Bound operations on a discovered Immediate Controls model."""

    read: Callable[[], Awaitable[PowerLimit]]
    set_power_limit: Callable[[float, int], Awaitable[None]]
    clear_power_limit: Callable[[], Awaitable[None]]
    probe_write_access: Callable[[], Awaitable[bool]]


def build_immediate_controls(
    unit: ModbusUnit, model: SunSpecModel
) -> ImmediateControls:
    """Create operations for the controls model found at its discovered address."""
    data_address = model.address + 2

    class ControlsComponent(Component):
        """The controls model, including its header for shift detection."""

        model_id = sunspec_fields.uint16(model.address)
        model_length = sunspec_fields.uint16(model.address + 1)
        # harmless read-write register used to probe write access
        connect_window = sunspec_fields.uint16(
            data_address + _CONN_WIN_TMS, writable=True
        )
        power_limit = sunspec_fields.uint16(
            data_address + _WMAX_LIM_PCT,
            scale_register=data_address + _WMAX_LIM_PCT_SF,
            unit="%",
        )
        power_limit_raw = sunspec_fields.uint16(
            data_address + _WMAX_LIM_PCT, writable=True
        )
        revert_seconds = sunspec_fields.uint16(
            data_address + _WMAX_LIM_PCT_RVRT_TMS, writable=True
        )
        enabled = sunspec_fields.enum16(data_address + _WMAX_LIM_ENA, writable=True)
        power_limit_sf = sunspec_fields.sunssf(data_address + _WMAX_LIM_PCT_SF)

    component = ControlsComponent(unit)

    async def update_checked() -> None:
        await component.async_update()
        check_model_header(
            model, component.model_id, component.model_length, "Immediate Controls"
        )

    async def read() -> PowerLimit:
        await update_checked()
        enabled = component.enabled
        revert = component.revert_seconds
        return PowerLimit(
            percent=component.power_limit,
            enabled=bool(enabled) if enabled is not None else None,
            revert_seconds=int(revert) if revert is not None else None,
        )

    async def set_power_limit(percent: float, revert_seconds: int) -> None:
        """Limit output power to ``percent`` of the nominal power and enable.

        ``revert_seconds`` > 0 auto-reverts the limit if it isn't refreshed -
        a safety net for controllers that may die; 0 keeps it until disabled.
        """
        if not 0 <= percent <= 100:
            raise ValueError(f"power limit out of range 0-100: {percent}")
        if not 0 <= revert_seconds <= 0xFFFF:
            raise ValueError(f"revert_seconds out of range: {revert_seconds}")
        await update_checked()
        if (sf := component.power_limit_sf) is None:
            raise SunSpecError("WMaxLimPct scale factor not implemented")
        await component.write("revert_seconds", revert_seconds)
        await component.write("power_limit_raw", round(percent / 10.0**sf))
        await component.write("enabled", 1)

    async def clear_power_limit() -> None:
        """Disable the output power limit."""
        await update_checked()
        await component.write("enabled", 0)

    async def probe_write_access() -> bool:
        """Check whether the device accepts Modbus writes.

        There is no register exposing the "inverter control via Modbus" web
        interface setting, so this writes a harmless register's current value
        back to itself and reports whether the device rejected it.
        """
        await update_checked()
        current = component.connect_window
        try:
            await component.write(
                "connect_window", int(current) if current is not None else 0
            )
        except ModbusExceptionError:
            return False
        return True

    return ImmediateControls(
        read=read,
        set_power_limit=set_power_limit,
        clear_power_limit=clear_power_limit,
        probe_write_access=probe_write_access,
    )
