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

SUNSPEC_BASE_ADDRESS: Final = 40000
SUNSPEC_MARKER: Final = 0x53756E53  # "SunS"
END_MODEL_ID: Final = 0xFFFF
# Sanity limit against malformed maps sending the chain walk astray.
MAX_MODELS: Final = 50

INVERTER_MODELS_FLOAT: Final = frozenset({111, 112, 113})
INVERTER_MODELS_INT_SF: Final = frozenset({101, 102, 103})
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
