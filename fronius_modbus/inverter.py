"""High-level access to a Fronius inverter unit via Modbus TCP."""

from collections.abc import Awaitable, Callable
from typing import Final

from modbus_connection import ModbusUnit

from .common import DeviceIdentity, DeviceIdentityReader, build_device_identity_reader
from .inverter_model import InverterData, InverterDataReader, build_inverter_reader
from .mppt import MpptData, MpptReader, build_mppt_reader
from .storage import WCHA_MAX, StorageData, StorageDataReader, build_storage_reader
from .sunspec import (
    COMMON_MODEL_ID,
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
        self._storage_reader: StorageDataReader | None = None
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
        self._storage_reader = (
            build_storage_reader(self._unit, storage_model)
            if storage_model is not None
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
        return self._storage_reader is not None

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
        """Read state of charge and status from the storage model."""
        return await self._read(lambda: self._storage_reader, "Storage")

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
