"""High-level access to a Fronius inverter unit via Modbus TCP."""

import functools
from collections.abc import Awaitable, Callable
from typing import Any, Concatenate, Final

from modbus_connection import ModbusUnit

from .common import Common
from .controls import Controls
from .inverter_model import Inverter, InverterFloat, InverterInteger
from .mppt import Mppt
from .storage import WCHA_MAX, Storage
from .sunspec import (
    COMMON_MODEL_ID,
    IMMEDIATE_CONTROLS_MODEL_ID,
    INVERTER_MODELS_FLOAT,
    INVERTER_MODELS_INT_SF,
    MULTI_MPPT_MODEL_ID,
    STORAGE_MODEL_ID,
    SunSpecError,
    SunSpecModel,
    discover_models,
)

# GEN24 and Tauro inverters always respond on unit ID 1
GEN24_UNIT_ID: Final = 1

_WCHA_MAX_NOT_IMPLEMENTED: Final = 0xFFFF


def datamanager_unit_id(inverter_number: str) -> int | None:
    """Return the Modbus unit ID for a SolarNet inverter number on a Datamanager."""
    try:
        number = int(inverter_number)
    except ValueError:
        return None
    # SolarNet inverter number 00 maps to unit ID 100
    return number or 100


def _uses_model[**P, T](
    attr: str,
) -> Callable[
    [Callable[Concatenate["FroniusModbusInverter", Any, P], Awaitable[T]]],
    Callable[Concatenate["FroniusModbusInverter", P], Awaitable[T]],
]:
    """Resolve the method's component and recover from register map shifts.

    The component named by ``attr`` is passed as the method's first argument.
    A missing model gets one re-discovery before failing - it may have
    appeared since. A :class:`SunSpecError` from the method means the
    register map shifted (the header check failed), so re-discover,
    rebuilding the components, and retry once.
    """

    def decorator(
        method: Callable[Concatenate[FroniusModbusInverter, Any, P], Awaitable[T]],
    ) -> Callable[Concatenate[FroniusModbusInverter, P], Awaitable[T]]:
        @functools.wraps(method)
        async def wrapper(
            self: FroniusModbusInverter, /, *args: P.args, **kwargs: P.kwargs
        ) -> T:
            if getattr(self, attr) is None:
                await self.discover()
            if (component := getattr(self, attr)) is None:
                raise SunSpecError(f"{attr.title()} model not available")
            try:
                return await method(self, component, *args, **kwargs)
            except SunSpecError:
                await self.discover()
                if (component := getattr(self, attr)) is None:
                    raise
                return await method(self, component, *args, **kwargs)

        return wrapper

    return decorator


class FroniusModbusInverter:
    """A Fronius inverter Modbus unit: discovery plus its SunSpec models.

    :meth:`discover` walks the model chain and builds a component per
    discovered model, exposed as attributes (``None`` when the device
    doesn't have the model). Read and set through the ``read_*`` / ``set_*``
    methods - they re-discover once when the register map shifts (the data
    type setting was changed on the device), which rebuilds the components,
    so hold on to the inverter, not to a component.
    """

    def __init__(self, unit: ModbusUnit, has_storage: bool | None = None) -> None:
        """Initialize with a Modbus unit addressing the inverter.

        ``has_storage`` overrides the storage detection used to classify MPPT
        module roles when their ID strings are inconclusive; ``None``
        auto-detects a connected storage from the Basic Storage Control Model
        (124) during discovery.
        """
        self._unit = unit
        self._has_storage_override = has_storage
        self._models: list[SunSpecModel] = []
        self.common: Common | None = None
        self.inverter: Inverter | None = None
        self.mppt: Mppt | None = None
        self.storage: Storage | None = None
        self.controls: Controls | None = None
        self.float_mode: bool | None = None
        self.has_storage: bool | None = has_storage

    async def discover(self) -> None:
        """Discover the SunSpec models and build their components."""
        self._models = await discover_models(self._unit)
        model_ids = {model.model_id for model in self._models}
        if model_ids & INVERTER_MODELS_FLOAT:
            self.float_mode = True
        elif model_ids & INVERTER_MODELS_INT_SF:
            self.float_mode = False
        else:
            self.float_mode = None
        has_storage = (
            self._has_storage_override
            if self._has_storage_override is not None
            else await self._detect_storage()
        )
        self.has_storage = has_storage

        unit = self._unit
        common = self._find_model(COMMON_MODEL_ID)
        self.common = Common(unit, common) if common else None
        inverter = self._find_model(*INVERTER_MODELS_FLOAT, *INVERTER_MODELS_INT_SF)
        if inverter is None:
            self.inverter = None
        elif inverter.model_id in INVERTER_MODELS_FLOAT:
            self.inverter = InverterFloat(unit, inverter)
        else:
            self.inverter = InverterInteger(unit, inverter)
        mppt = self._find_model(MULTI_MPPT_MODEL_ID)
        self.mppt = Mppt(unit, mppt, has_storage) if mppt else None
        storage = self._find_model(STORAGE_MODEL_ID)
        self.storage = Storage(unit, storage) if storage else None
        controls = self._find_model(IMMEDIATE_CONTROLS_MODEL_ID)
        self.controls = Controls(unit, controls) if controls else None

    def _find_model(self, *model_ids: int) -> SunSpecModel | None:
        return next(
            (model for model in self._models if model.model_id in model_ids), None
        )

    async def _detect_storage(self) -> bool:
        """Detect a connected storage from the Basic Storage Control Model."""
        storage_model = self._find_model(STORAGE_MODEL_ID)
        if storage_model is None:
            return False
        # WChaMax reads 0 when a storage-capable inverter has no storage
        # connected, otherwise the reference value for charge/discharge limits
        wcha_max = (
            await self._unit.read_holding_registers(
                storage_model.address + 2 + WCHA_MAX, 1
            )
        )[0]
        return wcha_max not in (0, _WCHA_MAX_NOT_IMPLEMENTED)

    @property
    def model_chain(self) -> list[SunSpecModel]:
        """Return the discovered SunSpec models."""
        return self._models

    @_uses_model("common")
    async def read_common(self, common: Common) -> Common:
        """Read manufacturer, model, version and serial from the Common model."""
        await common.async_update()
        return common

    @_uses_model("inverter")
    async def read_inverter(self, inverter: Inverter) -> Inverter:
        """Read AC/DC values, energy and state from the inverter model."""
        await inverter.async_update()
        return inverter

    @_uses_model("mppt")
    async def read_mppt(self, mppt: Mppt) -> Mppt:
        """Read per-module DC values from the Multiple MPPT model."""
        await mppt.async_update()
        return mppt

    @_uses_model("storage")
    async def read_storage(self, storage: Storage) -> Storage:
        """Read battery state and control setpoints from the storage model."""
        await storage.async_update()
        return storage

    @_uses_model("controls")
    async def read_controls(self, controls: Controls) -> Controls:
        """Read the output power limit state from the Immediate Controls model."""
        await controls.async_update()
        return controls

    @_uses_model("controls")
    async def probe_write_access(self, controls: Controls) -> bool:
        """Check whether the device accepts Modbus writes."""
        return await controls.probe_write_access()

    @_uses_model("controls")
    async def set_power_limit(
        self, controls: Controls, percent: float, *, revert_seconds: int = 0
    ) -> None:
        """Limit output power to ``percent`` of the nominal power WMax.

        ``revert_seconds`` > 0 auto-reverts the limit if it isn't refreshed -
        recommended as a safety net; 0 keeps it active until cleared.
        """
        await controls.set_power_limit(percent, revert_seconds)

    @_uses_model("controls")
    async def clear_power_limit(self, controls: Controls) -> None:
        """Disable the output power limit."""
        await controls.clear_power_limit()

    @_uses_model("storage")
    async def set_storage_limits(
        self,
        storage: Storage,
        *,
        charge: float | None = None,
        discharge: float | None = None,
        revert_seconds: int = 0,
    ) -> None:
        """Limit storage charge / discharge rates in percent of WChaMax.

        ``None`` deactivates the respective limit; negative values force
        charging / discharging. ``revert_seconds`` > 0 auto-reverts the limits
        if they aren't refreshed; support varies by device generation.
        """
        await storage.set_limits(charge, discharge, revert_seconds)

    @_uses_model("storage")
    async def set_minimum_reserve(self, storage: Storage, percent: float) -> None:
        """Set the minimum state of charge reserve in percent."""
        await storage.set_minimum_reserve(percent)

    @_uses_model("storage")
    async def set_grid_charging(self, storage: Storage, enabled: bool) -> None:
        """Allow or prevent charging the storage from the grid."""
        await storage.set_grid_charging(enabled)
