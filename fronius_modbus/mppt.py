"""SunSpec Multiple MPPT Inverter Extension Model (160).

The model is always encoded as integers with scale factors, regardless of the
float / int+SF setting of the inverter and meter models. Since its address is
only known after model-chain discovery and the ``modbus_connection.model``
framework resolves ``scale_register`` addresses absolutely, the component
classes are built by a factory binding the discovered address.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from modbus_connection import ModbusUnit
from modbus_connection.model import Component, repeating_group
from modbus_connection.model import sunspec as sunspec_fields
from modbus_connection.model.fields import NumberField

from .sunspec import SunSpecModel, check_model_header

# Data block offsets, relative to the first register after the 2-register
# model header. Verified against the Fronius register tables
# (fronius.com/QR-link/0006) for Datamanager and GEN24.
_DCA_SF: Final = 0
_DCV_SF: Final = 1
_DCW_SF: Final = 2
_DCWH_SF: Final = 3
_MODULE_COUNT: Final = 6

_MODULE_BLOCK_START: Final = 8
_MODULE_BLOCK_LENGTH: Final = 20
# Offsets within a repeating module block.
_MODULE_ID_STR: Final = 1  # 8 registers
_MODULE_DCA: Final = 9
_MODULE_DCV: Final = 10
_MODULE_DCW: Final = 11
_MODULE_DCWH: Final = 12  # 2 registers, acc32

type MpptReader = Callable[[], Awaitable["MpptData"]]


class ModuleRole(StrEnum):
    """Role of an MPPT module in the system."""

    PV = "pv"
    STORAGE_CHARGE = "storage_charge"
    STORAGE_DISCHARGE = "storage_discharge"
    STORAGE_BIDIRECTIONAL = "storage_bidirectional"


@dataclass(frozen=True)
class MpptModule:
    """Values of a single MPPT module (DC input)."""

    index: int  # 1-based module position
    id_str: str
    role: ModuleRole
    current: float | None
    voltage: float | None
    power: float | None
    energy: float | None


@dataclass(frozen=True)
class MpptData:
    """Parsed data of SunSpec model 160."""

    modules: list[MpptModule]

    @property
    def pv_energy_total(self) -> float | None:
        """Lifetime energy produced by PV only, excluding storage discharge.

        None if any PV module doesn't report energy to avoid undercounting.
        """
        pv_energies = [
            module.energy for module in self.modules if module.role is ModuleRole.PV
        ]
        if not pv_energies or None in pv_energies:
            return None
        return sum(energy for energy in pv_energies if energy is not None)

    @property
    def storage_charge_energy_total(self) -> float | None:
        """Lifetime energy charged into the storage."""
        return self._storage_energy(ModuleRole.STORAGE_CHARGE)

    @property
    def storage_discharge_energy_total(self) -> float | None:
        """Lifetime energy discharged from the storage."""
        return self._storage_energy(ModuleRole.STORAGE_DISCHARGE)

    def _storage_energy(self, role: ModuleRole) -> float | None:
        return next(
            (module.energy for module in self.modules if module.role is role), None
        )


def classify_modules(id_strs: list[str], has_storage: bool) -> list[ModuleRole]:
    """Determine the role of each MPPT module.

    Storage-related ID strings are matched first - GEN24 hybrids name their
    modules "MPPT 1", "MPPT 2", "StCha 3" and "StDisCha 4". When the names
    are inconclusive and the system has a storage, a 4-module inverter is
    assumed to be a hybrid exposing dedicated charge/discharge modules after
    the PV strings. Everything else defaults to PV - including module 2 of a
    Symo Hybrid, which is safe since those don't support lifetime energy
    anyway and a plain 2-MPPT inverter in a SolarNet ring with a hybrid
    would otherwise get wrong PV totals.
    """
    roles: list[ModuleRole | None] = []
    for id_str in id_strs:
        name = id_str.lower()
        if "discha" in name:  # "StDisCha 4" / "discharge"
            roles.append(ModuleRole.STORAGE_DISCHARGE)
        elif "cha" in name:  # "StCha 3" / "charge"
            roles.append(ModuleRole.STORAGE_CHARGE)
        elif "bat" in name or "storage" in name:
            roles.append(ModuleRole.STORAGE_BIDIRECTIONAL)
        else:
            roles.append(None)

    if has_storage and len(roles) == 4 and all(role is None for role in roles):
        roles[2] = ModuleRole.STORAGE_CHARGE
        roles[3] = ModuleRole.STORAGE_DISCHARGE

    return [role if role is not None else ModuleRole.PV for role in roles]


def build_mppt_reader(
    unit: ModbusUnit, model: SunSpecModel, has_storage: bool
) -> MpptReader:
    """Create a reader for a model 160 found at its discovered address.

    The component classes are created here so all register addresses -
    including the ``scale_register`` references into the model's fixed
    block - are absolute. The framework pools the reads into few block
    requests, keeping values and scale factors of one update consistent.
    """
    data_address = model.address + 2

    class MpptModuleComponent(Component):
        """One MPPT module, addressed at the first module block."""

        input_id_str = sunspec_fields.string(
            data_address + _MODULE_BLOCK_START + _MODULE_ID_STR, 8
        )
        current = sunspec_fields.uint16(
            data_address + _MODULE_BLOCK_START + _MODULE_DCA,
            scale_register=data_address + _DCA_SF,
            unit="A",
        )
        voltage = sunspec_fields.uint16(
            data_address + _MODULE_BLOCK_START + _MODULE_DCV,
            scale_register=data_address + _DCV_SF,
            unit="V",
        )
        power = sunspec_fields.uint16(
            data_address + _MODULE_BLOCK_START + _MODULE_DCW,
            scale_register=data_address + _DCW_SF,
            unit="W",
        )
        # a scaled acc32 accumulator: raw 0 means "not accumulated"
        energy: NumberField[float] = NumberField(
            data_address + _MODULE_BLOCK_START + _MODULE_DCWH,
            count=2,
            signed=False,
            nan=0,
            scale_register=data_address + _DCWH_SF,
            unit="Wh",
        )

    # module count N: unsigned with the uint16 not-implemented sentinel
    module_count: NumberField[int] = NumberField(
        data_address + _MODULE_COUNT, signed=False, nan=0xFFFF
    )

    class MultiMpptComponent(Component):
        """The full model, including its header for shift detection."""

        model_id = sunspec_fields.uint16(model.address)
        model_length = sunspec_fields.uint16(model.address + 1)
        modules = repeating_group(
            module_count,
            MpptModuleComponent,
            stride=_MODULE_BLOCK_LENGTH,
        )

    component = MultiMpptComponent(unit)

    async def read() -> MpptData:
        """Update the component and return its parsed data.

        The register map shifts when the data type setting is changed on the
        device, so the read-back header is verified against the discovered
        model first.
        """
        await component.async_update()
        check_model_header(
            model, component.model_id, component.model_length, "Multiple MPPT"
        )

        id_strs = [module.input_id_str or "" for module in component.modules]
        roles = classify_modules(id_strs, has_storage)
        return MpptData(
            modules=[
                MpptModule(
                    index=index + 1,
                    id_str=id_strs[index],
                    role=roles[index],
                    current=module.current,
                    voltage=module.voltage,
                    power=module.power,
                    energy=module.energy,
                )
                for index, module in enumerate(component.modules)
            ]
        )

    return read
