"""High-level access to a Fronius inverter unit via Modbus TCP."""

from collections.abc import Awaitable, Callable
from typing import Final

from modbus_connection import ModbusUnit

from .common import DeviceIdentity, DeviceIdentityReader, build_device_identity_reader
from .controls import ImmediateControls, PowerLimit, build_immediate_controls
from .inverter_model import InverterData, InverterDataReader, build_inverter_reader
from .mppt import MpptData, MpptReader, build_mppt_reader
from .storage import WCHA_MAX, StorageControls, StorageData, build_storage_controls
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


class FroniusModbusInverter:
    """Read SunSpec data from a Fronius inverter Modbus unit."""

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
        self._device_identity_reader: DeviceIdentityReader | None = None
        self._inverter_reader: InverterDataReader | None = None
        self._mppt_reader: MpptReader | None = None
        self._storage: StorageControls | None = None
        self._controls: ImmediateControls | None = None
        self.float_mode: bool | None = None
        self.has_storage: bool | None = has_storage

    async def discover(self) -> None:
        """Discover the SunSpec models exposed by the device."""
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

        common_model = self._find_model(COMMON_MODEL_ID)
        self._device_identity_reader = (
            build_device_identity_reader(self._unit, common_model)
            if common_model is not None
            else None
        )
        inverter_model = self._find_model(
            *INVERTER_MODELS_FLOAT, *INVERTER_MODELS_INT_SF
        )
        self._inverter_reader = (
            build_inverter_reader(self._unit, inverter_model)
            if inverter_model is not None
            else None
        )
        mppt_model = self._find_model(MULTI_MPPT_MODEL_ID)
        self._mppt_reader = (
            build_mppt_reader(self._unit, mppt_model, has_storage)
            if mppt_model is not None
            else None
        )
        storage_model = self._find_model(STORAGE_MODEL_ID)
        self._storage = (
            build_storage_controls(self._unit, storage_model)
            if storage_model is not None
            else None
        )
        controls_model = self._find_model(IMMEDIATE_CONTROLS_MODEL_ID)
        self._controls = (
            build_immediate_controls(self._unit, controls_model)
            if controls_model is not None
            else None
        )

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

    @property
    def has_common_model(self) -> bool:
        """Return whether the device exposes the Common model."""
        return self._device_identity_reader is not None

    @property
    def has_inverter_model(self) -> bool:
        """Return whether the device exposes an inverter model."""
        return self._inverter_reader is not None

    @property
    def has_mppt(self) -> bool:
        """Return whether the device exposes the Multiple MPPT model."""
        return self._mppt_reader is not None

    @property
    def has_storage_model(self) -> bool:
        """Return whether the device exposes the Basic Storage Control model."""
        return self._storage is not None

    @property
    def has_immediate_controls(self) -> bool:
        """Return whether the device exposes the Immediate Controls model."""
        return self._controls is not None

    async def read_device_identity(self) -> DeviceIdentity:
        """Read manufacturer, model, version and serial from the Common model."""
        return await self._read(lambda: self._device_identity_reader, "Common")

    async def read_inverter(self) -> InverterData:
        """Read AC/DC values, energy and state from the inverter model."""
        return await self._read(lambda: self._inverter_reader, "Inverter")

    async def read_mppt(self) -> MpptData:
        """Read per-module DC values from the Multiple MPPT model."""
        return await self._read(lambda: self._mppt_reader, "Multiple MPPT")

    async def read_storage(self) -> StorageData:
        """Read battery state and control setpoints from the storage model."""
        return await self._read(
            lambda: self._storage.read if self._storage else None, "Storage"
        )

    async def read_power_limit(self) -> PowerLimit:
        """Read the output power limit state from the Immediate Controls model."""
        return await self._read(
            lambda: self._controls.read if self._controls else None,
            "Immediate Controls",
        )

    async def probe_write_access(self) -> bool:
        """Check whether the device accepts Modbus writes.

        Writes a harmless register's current value back to itself. There is
        no register exposing the "inverter control via Modbus" web interface
        setting.
        """
        return await self._read(
            lambda: self._controls.probe_write_access if self._controls else None,
            "Immediate Controls",
        )

    async def set_power_limit(self, percent: float, *, revert_seconds: int = 0) -> None:
        """Limit output power to ``percent`` of the nominal power WMax.

        ``revert_seconds`` > 0 auto-reverts the limit if it isn't refreshed -
        recommended as a safety net; 0 keeps it active until cleared.
        """
        await self._read(
            lambda: self._controls_op(
                lambda controls: controls.set_power_limit(percent, revert_seconds)
            ),
            "Immediate Controls",
        )

    async def clear_power_limit(self) -> None:
        """Disable the output power limit."""
        await self._read(
            lambda: self._controls_op(lambda controls: controls.clear_power_limit()),
            "Immediate Controls",
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
        await self._read(
            lambda: self._storage_op(
                lambda storage: storage.set_limits(charge, discharge, revert_seconds)
            ),
            "Storage",
        )

    async def set_minimum_reserve(self, percent: float) -> None:
        """Set the minimum state of charge reserve in percent."""
        await self._read(
            lambda: self._storage_op(
                lambda storage: storage.set_minimum_reserve(percent)
            ),
            "Storage",
        )

    async def set_grid_charging(self, enabled: bool) -> None:
        """Allow or prevent charging the storage from the grid."""
        await self._read(
            lambda: self._storage_op(
                lambda storage: storage.set_grid_charging(enabled)
            ),
            "Storage",
        )

    def _controls_op(
        self, operation: Callable[[ImmediateControls], Awaitable[None]]
    ) -> Callable[[], Awaitable[None]] | None:
        """Bind an operation to the current controls, refreshed on re-discovery."""
        if (controls := self._controls) is None:
            return None
        return lambda: operation(controls)

    def _storage_op(
        self, operation: Callable[[StorageControls], Awaitable[None]]
    ) -> Callable[[], Awaitable[None]] | None:
        """Bind an operation to the current storage, refreshed on re-discovery."""
        if (storage := self._storage) is None:
            return None
        return lambda: operation(storage)

    async def _read[DataT](
        self,
        get_reader: Callable[[], Callable[[], Awaitable[DataT]] | None],
        name: str,
    ) -> DataT:
        """Run a model reader, re-discovering once on a register map shift.

        Each reader batches its model into few pooled block requests, keeping
        values and their scale factors of one update consistent.
        """
        if (reader := get_reader()) is None:
            raise SunSpecError(f"{name} model not available")
        try:
            return await reader()
        except SunSpecError:
            # The register map shifts when the data type setting is changed
            # on the device. Re-discover once and retry.
            await self.discover()
            if (reader := get_reader()) is None:
                raise
            return await reader()
