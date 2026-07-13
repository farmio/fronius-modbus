"""High-level access to a Fronius inverter unit via Modbus TCP."""

from collections.abc import Awaitable, Callable
from typing import Final

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
    SunSpecComponent,
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


async def _updated[C: SunSpecComponent](component: C) -> C:
    await component.async_update()
    return component


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

    async def read_common(self) -> CommonModel:
        """Read manufacturer, model, version and serial from the Common model."""
        return await self._use(lambda: self.common, "Common", _updated)

    async def read_inverter(self) -> InverterModel:
        """Read AC/DC values, energy and state from the inverter model."""
        # spelled out instead of using _use: mypy cannot infer a union type
        # parameter for the float/int variant classes
        if (component := self.inverter) is None:
            raise SunSpecError("Inverter model not available")
        try:
            await component.async_update()
        except SunSpecError:
            await self.discover()
            if (component := self.inverter) is None:
                raise
            await component.async_update()
        return component

    async def read_mppt(self) -> MpptModel:
        """Read per-module DC values from the Multiple MPPT model."""
        return await self._use(lambda: self.mppt, "Multiple MPPT", _updated)

    async def read_storage(self) -> StorageModel:
        """Read battery state and control setpoints from the storage model."""
        return await self._use(lambda: self.storage, "Storage", _updated)

    async def read_controls(self) -> ControlsModel:
        """Read the output power limit state from the Immediate Controls model."""
        return await self._use(lambda: self.controls, "Immediate Controls", _updated)

    async def probe_write_access(self) -> bool:
        """Check whether the device accepts Modbus writes."""
        return await self._use(
            lambda: self.controls,
            "Immediate Controls",
            lambda controls: controls.probe_write_access(),
        )

    async def set_power_limit(self, percent: float, *, revert_seconds: int = 0) -> None:
        """Limit output power to ``percent`` of the nominal power WMax.

        ``revert_seconds`` > 0 auto-reverts the limit if it isn't refreshed -
        recommended as a safety net; 0 keeps it active until cleared.
        """
        await self._use(
            lambda: self.controls,
            "Immediate Controls",
            lambda controls: controls.set_power_limit(percent, revert_seconds),
        )

    async def clear_power_limit(self) -> None:
        """Disable the output power limit."""
        await self._use(
            lambda: self.controls,
            "Immediate Controls",
            lambda controls: controls.clear_power_limit(),
        )

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
        await self._use(
            lambda: self.storage,
            "Storage",
            lambda storage: storage.set_limits(charge, discharge, revert_seconds),
        )

    async def set_minimum_reserve(self, percent: float) -> None:
        """Set the minimum state of charge reserve in percent."""
        await self._use(
            lambda: self.storage,
            "Storage",
            lambda storage: storage.set_minimum_reserve(percent),
        )

    async def set_grid_charging(self, enabled: bool) -> None:
        """Allow or prevent charging the storage from the grid."""
        await self._use(
            lambda: self.storage,
            "Storage",
            lambda storage: storage.set_grid_charging(enabled),
        )

    async def _use[C, T](
        self,
        get: Callable[[], C | None],
        name: str,
        operation: Callable[[C], Awaitable[T]],
    ) -> T:
        """Run an operation on a component, re-discovering once on a map shift.

        The register map shifts when the data type setting is changed on the
        device; the operation's update then raises on the header check, so
        re-discover (rebuilding the components) and retry once.
        """
        if (component := get()) is None:
            raise SunSpecError(f"{name} model not available")
        try:
            return await operation(component)
        except SunSpecError:
            await self.discover()
            if (component := get()) is None:
                raise
            return await operation(component)
