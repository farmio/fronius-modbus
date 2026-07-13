"""SunSpec Multiple MPPT Inverter Extension Model (160).

The model is always encoded as integers with scale factors, regardless of
the float / int+SF setting of the inverter and meter models. Register
addresses relative to the model start, verified against the Fronius register
tables (fronius.com/QR-link/0006) for Datamanager and GEN24.
"""

from enum import StrEnum
from functools import cached_property

from modbus_connection import ModbusUnit
from modbus_connection.model import Component, repeating_group
from modbus_connection.model import sunspec as sunspec_fields
from modbus_connection.model.fields import NumberField

from .sunspec import SunSpecComponent, SunSpecModel


class ModuleRole(StrEnum):
    """Role of an MPPT module in the system."""

    PV = "pv"
    STORAGE_CHARGE = "storage_charge"
    STORAGE_DISCHARGE = "storage_discharge"
    STORAGE_BIDIRECTIONAL = "storage_bidirectional"


class MpptModule(Component):
    """One MPPT module (DC input), declared at the first module block.

    Scale factors are shared by all modules and sit in the model's fixed
    block. Roles are classified by the owning :class:`Mppt`.
    """

    id_str = sunspec_fields.string(11, 8)
    current = sunspec_fields.uint16(19, scale_register=2, unit="A")
    voltage = sunspec_fields.uint16(20, scale_register=3, unit="V")
    power = sunspec_fields.uint16(21, scale_register=4, unit="W")
    # a scaled acc32 accumulator: raw 0 means "not accumulated"
    energy: NumberField[float] = NumberField(
        22, count=2, signed=False, nan=0, scale_register=5, unit="Wh"
    )


class Mppt(SunSpecComponent):
    """The Multiple MPPT model: per-module DC values, roles classified."""

    # count register N: unsigned with the uint16 not-implemented sentinel
    modules = repeating_group(
        NumberField(8, signed=False, nan=0xFFFF), MpptModule, stride=20
    )

    def __init__(
        self, unit: ModbusUnit, model: SunSpecModel, has_storage: bool
    ) -> None:
        """``has_storage`` steers the module role classification."""
        super().__init__(unit, model)
        self._has_storage = has_storage
        # re-classify when an update resizes or renames the modules
        self.add_update_listener(lambda: self.__dict__.pop("module_roles", None))

    @cached_property
    def module_roles(self) -> list[ModuleRole]:
        """The role of each MPPT module, aligned with :attr:`modules`.

        Storage-related ID strings are matched first - GEN24 hybrids name
        their modules "MPPT 1", "MPPT 2", "StCha 3" and "StDisCha 4". When
        the names are inconclusive and the system has a storage, a 4-module
        inverter is assumed to be a hybrid exposing dedicated
        charge/discharge modules after the PV strings. Everything else
        defaults to PV - including module 2 of a Symo Hybrid, which is safe
        since those don't support lifetime energy anyway and a plain 2-MPPT
        inverter in a SolarNet ring with a hybrid would otherwise get wrong
        PV totals.
        """
        roles: list[ModuleRole | None] = []
        for module in self.modules:
            name = (module.id_str or "").lower()
            if "discha" in name:  # "StDisCha 4" / "discharge"
                roles.append(ModuleRole.STORAGE_DISCHARGE)
            elif "cha" in name:  # "StCha 3" / "charge"
                roles.append(ModuleRole.STORAGE_CHARGE)
            elif "bat" in name or "storage" in name:
                roles.append(ModuleRole.STORAGE_BIDIRECTIONAL)
            else:
                roles.append(None)

        if self._has_storage and len(roles) == 4 and all(r is None for r in roles):
            roles[2] = ModuleRole.STORAGE_CHARGE
            roles[3] = ModuleRole.STORAGE_DISCHARGE

        return [role if role is not None else ModuleRole.PV for role in roles]

    @property
    def pv_energy_total(self) -> float | None:
        """Lifetime energy produced by PV only, excluding storage discharge.

        None if any PV module doesn't report energy to avoid undercounting.
        """
        pv_energies = [
            module.energy
            for module, role in zip(self.modules, self.module_roles, strict=True)
            if role is ModuleRole.PV
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

    def _storage_energy(self, wanted: ModuleRole) -> float | None:
        return next(
            (
                module.energy
                for module, role in zip(self.modules, self.module_roles, strict=True)
                if role is wanted
            ),
            None,
        )
