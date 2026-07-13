"""High-level access to a Fronius inverter unit via Modbus TCP."""

import functools
from collections.abc import Awaitable, Callable
from typing import Concatenate, Final

from modbus_connection import ModbusUnit

from .common import CommonModel
from .controls import ControlsModel
from .inverter_model import InverterFloatModel, InverterIntegerModel, InverterModel
from .mppt import MpptModel
from .storage import WCHA_MAX, StorageModel
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


def _rediscovers_on_shift[**P, T](
    method: Callable[Concatenate["FroniusModbusInverter", P], Awaitable[T]],
) -> Callable[Concatenate["FroniusModbusInverter", P], Awaitable[T]]:
    """Retry a method once after re-discovery when it hits a map shift.

    The register map shifts when the data type setting is changed on the
    device; a component's header check then raises, so re-discover
    (rebuilding the components) and retry once.
    """

    @functools.wraps(method)
    async def wrapper(
        self: FroniusModbusInverter, /, *args: P.args, **kwargs: P.kwargs
    ) -> T:
        try:
            return await method(self, *args, **kwargs)
        except SunSpecError:
            await self.discover()
            return await method(self, *args, **kwargs)

    return wrapper


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
        self.common: CommonModel | None = None
        self.inverter: InverterModel | None = None
        self.mppt: MpptModel | None = None
        self.storage: StorageModel | None = None
        self.controls: ControlsModel | None = None
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
        self.common = CommonModel(unit, common) if common else None
        inverter = self._find_model(*INVERTER_MODELS_FLOAT, *INVERTER_MODELS_INT_SF)
        if inverter is None:
            self.inverter = None
        elif inverter.model_id in INVERTER_MODELS_FLOAT:
            self.inverter = InverterFloatModel(unit, inverter)
        else:
            self.inverter = InverterIntegerModel(unit, inverter)
        mppt = self._find_model(MULTI_MPPT_MODEL_ID)
        self.mppt = MpptModel(unit, mppt, has_storage) if mppt else None
        storage = self._find_model(STORAGE_MODEL_ID)
        self.storage = StorageModel(unit, storage) if storage else None
        controls = self._find_model(IMMEDIATE_CONTROLS_MODEL_ID)
        self.controls = ControlsModel(unit, controls) if controls else None

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

    @_rediscovers_on_shift
    async def read_common(self) -> CommonModel:
        """Read manufacturer, model, version and serial from the Common model."""
        if (common := self.common) is None:
            raise SunSpecError("Common model not available")
        await common.async_update()
        return common

    @_rediscovers_on_shift
    async def read_inverter(self) -> InverterModel:
        """Read AC/DC values, energy and state from the inverter model."""
        if (inverter := self.inverter) is None:
            raise SunSpecError("Inverter model not available")
        await inverter.async_update()
        return inverter

    @_rediscovers_on_shift
    async def read_mppt(self) -> MpptModel:
        """Read per-module DC values from the Multiple MPPT model."""
        if (mppt := self.mppt) is None:
            raise SunSpecError("Multiple MPPT model not available")
        await mppt.async_update()
        return mppt

    @_rediscovers_on_shift
    async def read_storage(self) -> StorageModel:
        """Read battery state and control setpoints from the storage model."""
        if (storage := self.storage) is None:
            raise SunSpecError("Storage model not available")
        await storage.async_update()
        return storage

    @_rediscovers_on_shift
    async def read_controls(self) -> ControlsModel:
        """Read the output power limit state from the Immediate Controls model."""
        if (controls := self.controls) is None:
            raise SunSpecError("Immediate Controls model not available")
        await controls.async_update()
        return controls

    @_rediscovers_on_shift
    async def probe_write_access(self) -> bool:
        """Check whether the device accepts Modbus writes."""
        if (controls := self.controls) is None:
            raise SunSpecError("Immediate Controls model not available")
        return await controls.probe_write_access()

    @_rediscovers_on_shift
    async def set_power_limit(self, percent: float, *, revert_seconds: int = 0) -> None:
        """Limit output power to ``percent`` of the nominal power WMax.

        ``revert_seconds`` > 0 auto-reverts the limit if it isn't refreshed -
        recommended as a safety net; 0 keeps it active until cleared.
        """
        if (controls := self.controls) is None:
            raise SunSpecError("Immediate Controls model not available")
        await controls.set_power_limit(percent, revert_seconds)

    @_rediscovers_on_shift
    async def clear_power_limit(self) -> None:
        """Disable the output power limit."""
        if (controls := self.controls) is None:
            raise SunSpecError("Immediate Controls model not available")
        await controls.clear_power_limit()

    @_rediscovers_on_shift
    async def set_storage_limits(
        self,
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
        if (storage := self.storage) is None:
            raise SunSpecError("Storage model not available")
        await storage.set_limits(charge, discharge, revert_seconds)

    @_rediscovers_on_shift
    async def set_minimum_reserve(self, percent: float) -> None:
        """Set the minimum state of charge reserve in percent."""
        if (storage := self.storage) is None:
            raise SunSpecError("Storage model not available")
        await storage.set_minimum_reserve(percent)

    @_rediscovers_on_shift
    async def set_grid_charging(self, enabled: bool) -> None:
        """Allow or prevent charging the storage from the grid."""
        if (storage := self.storage) is None:
            raise SunSpecError("Storage model not available")
        await storage.set_grid_charging(enabled)
