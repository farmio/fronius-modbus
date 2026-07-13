"""SunSpec model-chain discovery.

Fronius devices expose a dynamic SunSpec register map: model addresses shift
with the configured data type (float vs int+SF) and differ between device
generations, so models must be discovered by walking the model chain instead
of using fixed addresses.
"""

from dataclasses import dataclass
from typing import Final

from modbus_connection import ModbusUnit
from modbus_connection.decode import decode_uint32
from modbus_connection.model import Component
from modbus_connection.model import sunspec as sunspec_fields

SUNSPEC_BASE_ADDRESS: Final = 40000
SUNSPEC_MARKER: Final = 0x53756E53  # "SunS"
END_MODEL_ID: Final = 0xFFFF
# Sanity limit against malformed maps sending the chain walk astray.
MAX_MODELS: Final = 50

COMMON_MODEL_ID: Final = 1
INVERTER_MODELS_FLOAT: Final = frozenset({111, 112, 113})
INVERTER_MODELS_INT_SF: Final = frozenset({101, 102, 103})
IMMEDIATE_CONTROLS_MODEL_ID: Final = 123
MULTI_MPPT_MODEL_ID: Final = 160
STORAGE_MODEL_ID: Final = 124


class SunSpecError(Exception):
    """Raised when a device does not behave like a SunSpec device."""


@dataclass(frozen=True)
class SunSpecModel:
    """Location of a SunSpec model in the register map.

    ``address`` points at the 2-register model header (model ID, length);
    ``length`` is the number of data registers following the header.
    """

    model_id: int
    address: int
    length: int


class SunSpecComponent(Component):
    """A discovered SunSpec model, placed at its address and header-checked.

    Subclasses declare their fields relative to the model start: the
    2-register header sits at 0/1, the data block starts at 2. The header is
    verified against the discovered model on every update - the register map
    shifts when the data type setting is changed on the device, and a
    mismatch raises :class:`SunSpecError` so the owner can re-discover.
    """

    model_id = sunspec_fields.uint16(0)
    model_length = sunspec_fields.uint16(1)

    def __init__(self, unit: ModbusUnit, model: SunSpecModel) -> None:
        """Initialize the component at the discovered model's address."""
        super().__init__(unit, base_offset=model.address)
        self._model = model

    async def async_update(self) -> None:
        """Read the model's registers, verifying the header."""
        await super().async_update()
        if (
            self.model_id != self._model.model_id
            or self.model_length != self._model.length
        ):
            raise SunSpecError(
                f"{type(self).__name__} header mismatch:"
                f" expected {self._model.model_id}/{self._model.length},"
                f" read {self.model_id}/{self.model_length}"
                " - the register map has changed"
            )

    def __repr__(self) -> str:
        """Return the component's field values."""
        values = ", ".join(
            f"{name}={getattr(self, name)!r}"
            for name in self._register_fields
            if name not in ("model_id", "model_length")
        )
        return f"{type(self).__name__}({values})"


async def discover_models(unit: ModbusUnit) -> list[SunSpecModel]:
    """Walk the SunSpec model chain and return the discovered models."""
    marker = await unit.read_holding_registers(SUNSPEC_BASE_ADDRESS, 2)
    if decode_uint32(marker) != SUNSPEC_MARKER:
        raise SunSpecError(
            f"No SunSpec marker found at register {SUNSPEC_BASE_ADDRESS + 1}"
        )

    models: list[SunSpecModel] = []
    address = SUNSPEC_BASE_ADDRESS + 2
    for _ in range(MAX_MODELS):
        model_id, length = await unit.read_holding_registers(address, 2)
        if model_id == END_MODEL_ID:
            return models
        models.append(SunSpecModel(model_id=model_id, address=address, length=length))
        address += 2 + length
    raise SunSpecError(f"Model chain not terminated after {MAX_MODELS} models")
