"""Tests for the common block, inverter model and storage readers."""

import pytest
from modbus_connection.mock import MockModbusUnit

from fronius_modbus import (
    FroniusModbusInverter,
    OperatingState,
    StorageState,
    SunSpecError,
)
from fronius_modbus.testing import InverterModelSpec, build_sunspec_map

INVERTER_SPEC = InverterModelSpec(
    ac_current=9.75,
    ac_current_phase_1=3.25,
    ac_current_phase_2=3.25,
    ac_current_phase_3=3.25,
    voltage_phase_1=230.25,
    voltage_phase_2=231.5,
    voltage_phase_3=229.75,
    voltage_phase_1_2=400.5,
    voltage_phase_2_3=401.25,
    voltage_phase_3_1=399.75,
    ac_power=2271.5,
    frequency=50.0,
    apparent_power=2280.5,
    reactive_power=-12.5,
    power_factor=97.5,
    energy_total=1_234_567,
    dc_current=5.75,
    dc_voltage=425.5,
    dc_power=2400.5,
    operating_state=4,  # MPPT
    vendor_operating_state=4,
    events=0,
)


async def test_device_identity(mock_modbus_unit: MockModbusUnit) -> None:
    """Test reading the Common model."""
    mock_modbus_unit.holding.update(
        build_sunspec_map(
            [],
            manufacturer="Fronius",
            device_model="Symo GEN24 10.0",
            version="1.36.5-1",
            serial_number="37152250",
        )
    )
    inverter = FroniusModbusInverter(mock_modbus_unit)
    await inverter.discover()
    assert inverter.common is not None

    await inverter.async_update()
    identity = inverter.common
    assert identity.manufacturer == "Fronius"
    assert identity.model == "Symo GEN24 10.0"
    assert identity.software_version == "1.36.5-1"
    assert identity.serial_number == "37152250"


@pytest.mark.parametrize(
    "float_mode",
    [pytest.param(True, id="float"), pytest.param(False, id="int_sf")],
)
async def test_inverter_data(
    mock_modbus_unit: MockModbusUnit, float_mode: bool
) -> None:
    """Test reading the inverter model in both encodings."""
    mock_modbus_unit.holding.update(
        build_sunspec_map([], float_mode=float_mode, inverter=INVERTER_SPEC)
    )
    inverter = FroniusModbusInverter(mock_modbus_unit)
    await inverter.discover()
    assert inverter.inverter is not None

    await inverter.async_update()
    data = inverter.inverter
    assert data.ac_current == 9.75
    assert data.ac_current_phase_1 == 3.25
    assert data.voltage_phase_1 == 230.25
    assert data.voltage_phase_2 == 231.5
    assert data.voltage_phase_3 == 229.75
    assert data.voltage_phase_1_2 == 400.5
    assert data.voltage_phase_2_3 == 401.25
    assert data.voltage_phase_3_1 == 399.75
    assert data.ac_power == 2271.5
    assert data.frequency == 50.0
    assert data.apparent_power == 2280.5
    assert data.reactive_power == -12.5
    assert data.power_factor == 97.5
    assert data.energy_total == 1_234_567
    assert data.dc_current == 5.75
    assert data.dc_voltage == 425.5
    assert data.dc_power == 2400.5
    assert data.operating_state is OperatingState.MPPT
    assert data.vendor_operating_state == 4
    assert data.events == 0


@pytest.mark.parametrize(
    "float_mode",
    [pytest.param(True, id="float"), pytest.param(False, id="int_sf")],
)
async def test_inverter_data_not_implemented(
    mock_modbus_unit: MockModbusUnit, float_mode: bool
) -> None:
    """Test not-implemented inverter values decode to None in both encodings."""
    mock_modbus_unit.holding.update(
        build_sunspec_map(
            [],
            float_mode=float_mode,
            # single phase inverter: only phase 1 implemented
            inverter=InverterModelSpec(
                ac_current=3.25, ac_current_phase_1=3.25, ac_power=750.5
            ),
        )
    )
    inverter = FroniusModbusInverter(mock_modbus_unit)
    await inverter.discover()

    await inverter.async_update()
    data = inverter.inverter
    assert data is not None
    assert data.ac_current == 3.25
    assert data.ac_current_phase_1 == 3.25
    assert data.ac_current_phase_2 is None
    assert data.ac_current_phase_3 is None
    assert data.voltage_phase_1 is None
    assert data.reactive_power is None
    assert data.power_factor is None
    assert data.energy_total is None
    assert data.operating_state is None
    assert data.vendor_operating_state is None


async def test_storage_data(mock_modbus_unit: MockModbusUnit) -> None:
    """Test reading the storage model."""
    mock_modbus_unit.holding.update(
        build_sunspec_map(
            [],
            storage_wcha_max=10000,
            storage_soc=55.5,
            storage_state=4,  # CHARGING
        )
    )
    inverter = FroniusModbusInverter(mock_modbus_unit)
    await inverter.discover()
    assert inverter.storage is not None

    await inverter.async_update()
    data = inverter.storage
    assert data.charge_reference_power == 10000
    assert data.state_of_charge == 55.5
    assert data.state is StorageState.CHARGING


async def test_storage_data_not_implemented(mock_modbus_unit: MockModbusUnit) -> None:
    """Test not-implemented storage values decode to None."""
    mock_modbus_unit.holding.update(build_sunspec_map([], storage_wcha_max=10000))
    inverter = FroniusModbusInverter(mock_modbus_unit)
    await inverter.discover()

    await inverter.async_update()
    data = inverter.storage
    assert data is not None
    assert data.state_of_charge is None
    assert data.state is None


async def test_storage_model_not_available(mock_modbus_unit: MockModbusUnit) -> None:
    """Test reading storage on a device without the model."""
    mock_modbus_unit.holding.update(build_sunspec_map([]))
    inverter = FroniusModbusInverter(mock_modbus_unit)
    await inverter.discover()
    assert inverter.storage is None


async def test_inverter_data_after_data_type_change(
    mock_modbus_unit: MockModbusUnit,
) -> None:
    """Test a register map shift raises, and re-discovery recovers."""
    mock_modbus_unit.holding.update(
        build_sunspec_map([], float_mode=True, inverter=INVERTER_SPEC)
    )
    inverter = FroniusModbusInverter(mock_modbus_unit)
    await inverter.discover()
    await inverter.async_update()
    data = inverter.inverter
    assert data is not None
    assert data.ac_power == 2271.5
    assert inverter.float_mode is True

    mock_modbus_unit.holding.clear()
    mock_modbus_unit.holding.update(
        build_sunspec_map([], float_mode=False, inverter=INVERTER_SPEC)
    )
    with pytest.raises(SunSpecError):
        await inverter.async_update()

    await inverter.discover()
    await inverter.async_update()
    data = inverter.inverter
    assert data is not None
    assert data.ac_power == 2271.5
    assert inverter.float_mode is False
