"""High-level access to a Fronius inverter unit via Modbus TCP."""

from typing import Final

from modbus_connection import ModbusUnit
from modbus_connection.model import ComponentGroup

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


class FroniusModbusInverter:
    """A Fronius inverter Modbus unit: its discovered SunSpec models.

    Call :meth:`discover` once after connecting - it walks the model chain
    and builds a component per discovered model, exposed as attributes
    (``None`` when the device doesn't have the model)::

        fronius = FroniusModbusInverter(unit)
        await fronius.discover()
        await fronius.async_update()
        fronius.inverter.ac_power
        fronius.mppt.pv_energy_total
        await fronius.storage.set_limits(charge=50.0)

    Each component can also be refreshed on its own (``await
    fronius.storage.async_update()``) and exposes ``add_update_listener``.

    The register map shifts when the data type setting is changed on the
    device: every component verifies its model header on each update and
    raises :class:`SunSpecError` on a mismatch. Recover by calling
    :meth:`discover` again, which rebuilds the components at the new
    addresses - so hold on to this object, not to a component.
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
        self._group: ComponentGroup | None = None
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
        # One pooled-read group over every discovered model: adjacent registers
        # from different models are fetched together on async_update.
        self._group = ComponentGroup(unit, list(self.components))

    @property
    def components(self) -> tuple[SunSpecComponent, ...]:
        """Every discovered model component, for iteration."""
        return tuple(
            component
            for component in (
                self.common,
                self.inverter,
                self.mppt,
                self.storage,
                self.controls,
            )
            if component is not None
        )

    async def async_update(self) -> None:
        """Refresh every discovered model in as few Modbus calls as possible."""
        if self._group is None:
            raise SunSpecError("No models discovered - call discover() first")
        await self._group.async_update()

    @property
    def model_chain(self) -> list[SunSpecModel]:
        """Return the discovered SunSpec models."""
        return self._models

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
