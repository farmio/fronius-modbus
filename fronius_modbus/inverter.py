"""High-level access to a Fronius inverter unit via Modbus TCP."""

from typing import Final

from modbus_connection import ModbusUnit

from .mppt import MpptData, MpptReader, build_mppt_reader
from .sunspec import (
    INVERTER_MODELS_FLOAT,
    INVERTER_MODELS_INT_SF,
    MULTI_MPPT_MODEL_ID,
    STORAGE_MODEL_ID,
    SunSpecError,
    SunSpecModel,
    discover_models,
)

_WCHA_MAX_NOT_IMPLEMENTED: Final = 0xFFFF

# GEN24 and Tauro inverters always respond on unit ID 1
GEN24_UNIT_ID: Final = 1


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
        self._mppt_reader: MpptReader | None = None
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
        mppt_model = next(
            (model for model in self._models if model.model_id == MULTI_MPPT_MODEL_ID),
            None,
        )
        self._mppt_reader = (
            build_mppt_reader(self._unit, mppt_model, has_storage)
            if mppt_model is not None
            else None
        )

    async def _detect_storage(self) -> bool:
        """Detect a connected storage from the Basic Storage Control Model."""
        storage_model = next(
            (model for model in self._models if model.model_id == STORAGE_MODEL_ID),
            None,
        )
        if storage_model is None:
            return False
        # WChaMax reads 0 when a storage-capable inverter has no storage
        # connected, otherwise the reference value for charge/discharge limits
        wcha_max = (
            await self._unit.read_holding_registers(storage_model.address + 2, 1)
        )[0]
        return wcha_max not in (0, _WCHA_MAX_NOT_IMPLEMENTED)

    @property
    def model_chain(self) -> list[SunSpecModel]:
        """Return the discovered SunSpec models."""
        return self._models

    @property
    def has_mppt(self) -> bool:
        """Return whether the device exposes the Multiple MPPT model."""
        return self._mppt_reader is not None

    async def read_mppt(self) -> MpptData:
        """Read the Multiple MPPT model in few pooled block requests.

        Values and their scale factors are read in the same update, keeping
        them consistent even though scale factors may change at runtime.
        """
        if self._mppt_reader is None:
            raise SunSpecError("Multiple MPPT model not available")
        try:
            return await self._mppt_reader()
        except SunSpecError:
            # The register map shifts when the data type setting is changed
            # on the device. Re-discover once and retry.
            await self.discover()
            if self._mppt_reader is None:
                raise
            return await self._mppt_reader()
