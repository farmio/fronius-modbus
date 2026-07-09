"""High-level access to a Fronius inverter unit via Modbus TCP."""

from typing import Final

from modbus_connection import ModbusUnit

from .mppt import MpptData, MpptReader, build_mppt_reader
from .sunspec import (
    INVERTER_MODELS_FLOAT,
    INVERTER_MODELS_INT_SF,
    MULTI_MPPT_MODEL_ID,
    SunSpecError,
    SunSpecModel,
    discover_models,
)

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

    def __init__(self, unit: ModbusUnit, has_storage: bool) -> None:
        """Initialize with a Modbus unit addressing the inverter.

        ``has_storage`` hints the MPPT module role classification when the
        module ID strings are inconclusive.
        """
        self._unit = unit
        self._has_storage = has_storage
        self._models: list[SunSpecModel] = []
        self._mppt_reader: MpptReader | None = None
        self.float_mode: bool | None = None

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
        mppt_model = next(
            (model for model in self._models if model.model_id == MULTI_MPPT_MODEL_ID),
            None,
        )
        self._mppt_reader = (
            build_mppt_reader(self._unit, mppt_model, self._has_storage)
            if mppt_model is not None
            else None
        )

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
