"""Tests for MPPT module parsing, classification and derived totals."""

import pytest
from modbus_connection.mock import MockModbusUnit

from fronius_modbus import FroniusModbusInverter, ModuleRole, MpptData
from fronius_modbus.mppt import classify_modules
from fronius_modbus.testing import MpptModuleSpec, build_sunspec_map

GEN24_HYBRID_MODULES = [
    MpptModuleSpec(
        id_str="String 1", current=82, voltage=4021, power=3300, energy=1_000_000
    ),
    MpptModuleSpec(
        id_str="String 2", current=41, voltage=4022, power=1650, energy=500_000
    ),
    MpptModuleSpec(id_str="unnamed 3", current=0, voltage=0, power=0, energy=200_000),
    MpptModuleSpec(
        id_str="unnamed 4", current=12, voltage=3990, power=480, energy=150_000
    ),
]


async def _read_mppt(
    unit: MockModbusUnit, registers: dict[int, int], has_storage: bool
) -> MpptData:
    unit.holding.update(registers)
    inverter = FroniusModbusInverter(unit, has_storage=has_storage)
    await inverter.discover()
    return await inverter.read_mppt()


async def test_scaled_values(mock_modbus_unit: MockModbusUnit) -> None:
    """Test scale factors are applied to raw register values."""
    data = await _read_mppt(
        mock_modbus_unit, build_sunspec_map(GEN24_HYBRID_MODULES), has_storage=True
    )
    module_1 = data.modules[0]
    assert module_1.index == 1
    assert module_1.id_str == "String 1"
    assert module_1.current == 8.2  # scale factor -1
    assert module_1.voltage == 402.1
    assert module_1.power == 3300  # scale factor 0
    assert module_1.energy == 1_000_000


async def test_not_implemented_sentinels(mock_modbus_unit: MockModbusUnit) -> None:
    """Test not-implemented sentinel values decode to None."""
    data = await _read_mppt(
        mock_modbus_unit,
        build_sunspec_map(
            [
                MpptModuleSpec(id_str="String 1", power=3300),
                MpptModuleSpec(id_str="String 2"),  # all values not implemented
            ]
        ),
        has_storage=False,
    )
    module_1, module_2 = data.modules
    assert module_1.power == 3300
    assert module_1.current is None
    assert module_1.energy is None
    assert module_2.current is None
    assert module_2.voltage is None
    assert module_2.power is None
    assert module_2.energy is None


async def test_derived_totals_gen24_hybrid(mock_modbus_unit: MockModbusUnit) -> None:
    """Test derived energy totals for a GEN24 hybrid (4 modules)."""
    data = await _read_mppt(
        mock_modbus_unit, build_sunspec_map(GEN24_HYBRID_MODULES), has_storage=True
    )
    assert [module.role for module in data.modules] == [
        ModuleRole.PV,
        ModuleRole.PV,
        ModuleRole.STORAGE_CHARGE,
        ModuleRole.STORAGE_DISCHARGE,
    ]
    assert data.pv_energy_total == 1_500_000
    assert data.storage_charge_energy_total == 200_000
    assert data.storage_discharge_energy_total == 150_000


async def test_derived_totals_without_storage(
    mock_modbus_unit: MockModbusUnit,
) -> None:
    """Test all modules default to PV without a storage."""
    data = await _read_mppt(
        mock_modbus_unit, build_sunspec_map(GEN24_HYBRID_MODULES), has_storage=False
    )
    assert all(module.role is ModuleRole.PV for module in data.modules)
    assert data.pv_energy_total == 1_850_000
    assert data.storage_charge_energy_total is None
    assert data.storage_discharge_energy_total is None


async def test_pv_total_none_when_energy_missing(
    mock_modbus_unit: MockModbusUnit,
) -> None:
    """Test the PV total is None when a PV module doesn't report energy."""
    data = await _read_mppt(
        mock_modbus_unit,
        build_sunspec_map(
            [
                MpptModuleSpec(id_str="String 1", energy=1_000_000),
                MpptModuleSpec(id_str="String 2"),  # energy not implemented
            ]
        ),
        has_storage=False,
    )
    assert data.pv_energy_total is None


@pytest.mark.parametrize(
    ("id_strs", "has_storage", "expected"),
    [
        pytest.param(
            ["String 1", "String 2"],
            False,
            [ModuleRole.PV, ModuleRole.PV],
            id="plain_pv",
        ),
        pytest.param(
            ["String 1", "String 2", "unnamed", "unnamed"],
            True,
            [
                ModuleRole.PV,
                ModuleRole.PV,
                ModuleRole.STORAGE_CHARGE,
                ModuleRole.STORAGE_DISCHARGE,
            ],
            id="gen24_hybrid_fallback",
        ),
        pytest.param(
            ["String 1", "String 2", "unnamed", "unnamed"],
            False,
            [ModuleRole.PV] * 4,
            id="tauro_four_pv_trackers",
        ),
        pytest.param(
            ["String 1", "Battery"],
            True,
            [ModuleRole.PV, ModuleRole.STORAGE_BIDIRECTIONAL],
            id="bidirectional_by_id_str",
        ),
        pytest.param(
            ["PV 1", "Battery charge", "Battery discharge"],
            True,
            [
                ModuleRole.PV,
                ModuleRole.STORAGE_CHARGE,
                ModuleRole.STORAGE_DISCHARGE,
            ],
            id="charge_discharge_by_id_str",
        ),
        pytest.param(
            ["String 1", "String 2"],
            True,
            [ModuleRole.PV, ModuleRole.PV],
            id="two_modules_stay_pv_despite_storage",
        ),
    ],
)
def test_classify_modules(
    id_strs: list[str], has_storage: bool, expected: list[ModuleRole]
) -> None:
    """Test MPPT module role classification."""
    assert classify_modules(id_strs, has_storage) == expected
