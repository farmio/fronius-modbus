"""SunSpec model IDs of the Fronius register map."""

from typing import Final

from modbus_connection.model.sunspec import (
    SunSpecComponent,
    SunSpecError,
    SunSpecMapShiftError,
    SunSpecModel,
    SunSpecModels,
    scan,
)

__all__ = [
    "SunSpecComponent",
    "SunSpecError",
    "SunSpecMapShiftError",
    "SunSpecModel",
    "SunSpecModels",
    "scan",
]

SUNSPEC_BASE_ADDRESS: Final = 40000

COMMON_MODEL_ID: Final = 1
INVERTER_MODELS_FLOAT: Final = frozenset({111, 112, 113})
INVERTER_MODELS_INT_SF: Final = frozenset({101, 102, 103})
IMMEDIATE_CONTROLS_MODEL_ID: Final = 123
MULTI_MPPT_MODEL_ID: Final = 160
STORAGE_MODEL_ID: Final = 124
