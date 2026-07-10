"""SunSpec inverter models (101-103 int+SF / 111-113 float).

These are the only models whose encoding follows the data type setting of
the device: with "float" the values are IEEE 754 floats (models 111-113),
with "int+SF" they are integers with scale factors (models 101-103). Both
layouts decode into the same :class:`InverterData`. Single and split phase
models report unsupported phases as not-implemented.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from modbus_connection import ModbusUnit
from modbus_connection.model import Component
from modbus_connection.model import sunspec as sunspec_fields
from modbus_connection.model.fields import FloatField, NumberField

from .sunspec import INVERTER_MODELS_FLOAT, SunSpecModel, check_model_header

# Data block offsets as (float models, int+SF models, int+SF scale factor)
# relative to the first register after the 2-register model header, per the
# SunSpec model definitions. Values in the float models span 2 registers.
_A: Final = (0, 0, 4)
_APH_A: Final = (2, 1, 4)
_APH_B: Final = (4, 2, 4)
_APH_C: Final = (6, 3, 4)
_PPV_AB: Final = (8, 5, 11)
_PPV_BC: Final = (10, 6, 11)
_PPV_CA: Final = (12, 7, 11)
_PHV_A: Final = (14, 8, 11)
_PHV_B: Final = (16, 9, 11)
_PHV_C: Final = (18, 10, 11)
_W: Final = (20, 12, 13)
_HZ: Final = (22, 14, 15)
_VA: Final = (24, 16, 17)
_VAR: Final = (26, 18, 19)
_PF: Final = (28, 20, 21)
_WH: Final = (30, 22, 24)  # int+SF: acc32
_DCA: Final = (32, 25, 26)
_DCV: Final = (34, 27, 28)
_DCW: Final = (36, 29, 30)
_ST: Final = (46, 36)
_ST_VND: Final = (47, 37)
_EVT1: Final = (48, 38)  # bitfield32, 2 registers

type InverterDataReader = Callable[[], Awaitable["InverterData"]]
type _ValueField = FloatField | NumberField[float]


class OperatingState(StrEnum):
    """SunSpec inverter operating state (St)."""

    OFF = "off"
    SLEEPING = "sleeping"
    STARTING = "starting"
    MPPT = "mppt"
    THROTTLED = "throttled"
    SHUTTING_DOWN = "shutting_down"
    FAULT = "fault"
    STANDBY = "standby"


_OPERATING_STATES: Final = {
    1: OperatingState.OFF,
    2: OperatingState.SLEEPING,
    3: OperatingState.STARTING,
    4: OperatingState.MPPT,
    5: OperatingState.THROTTLED,
    6: OperatingState.SHUTTING_DOWN,
    7: OperatingState.FAULT,
    8: OperatingState.STANDBY,
}


@dataclass(frozen=True)
class InverterData:
    """Values of a SunSpec inverter model."""

    ac_current: float | None
    ac_current_phase_1: float | None
    ac_current_phase_2: float | None
    ac_current_phase_3: float | None
    voltage_phase_1: float | None
    voltage_phase_2: float | None
    voltage_phase_3: float | None
    voltage_phase_1_2: float | None
    voltage_phase_2_3: float | None
    voltage_phase_3_1: float | None
    ac_power: float | None
    frequency: float | None
    apparent_power: float | None
    reactive_power: float | None
    power_factor: float | None  # percent, -100..100
    energy_total: float | None
    dc_current: float | None
    dc_voltage: float | None
    dc_power: float | None
    operating_state: OperatingState | None
    vendor_operating_state: int | None
    events: int | None  # Evt1 bitfield


def build_inverter_reader(unit: ModbusUnit, model: SunSpecModel) -> InverterDataReader:
    """Create a reader for an inverter model found at its discovered address.

    One component class serves both encodings: each field is built as a
    float32 or as an integer referencing its scale factor register,
    depending on the discovered model ID.
    """
    is_float = model.model_id in INVERTER_MODELS_FLOAT
    data_address = model.address + 2

    def value(
        offsets: tuple[int, int, int], *, signed: bool = False, unit: str | None = None
    ) -> _ValueField:
        float_offset, int_offset, sf_offset = offsets
        if is_float:
            return sunspec_fields.float32(data_address + float_offset, unit=unit)
        factory = sunspec_fields.int16 if signed else sunspec_fields.uint16
        return factory(
            data_address + int_offset,
            scale_register=data_address + sf_offset,
            unit=unit,
        )

    def energy(offsets: tuple[int, int, int]) -> _ValueField:
        float_offset, int_offset, sf_offset = offsets
        if is_float:
            return sunspec_fields.float32(data_address + float_offset, unit="Wh")
        # a scaled acc32 accumulator: raw 0 means "not accumulated"
        return NumberField(
            data_address + int_offset,
            count=2,
            signed=False,
            nan=0,
            scale_register=data_address + sf_offset,
            unit="Wh",
        )

    def tail_offset(offsets: tuple[int, int]) -> int:
        return data_address + offsets[0 if is_float else 1]

    class InverterComponent(Component):
        """The inverter model, including its header for shift detection."""

        model_id = sunspec_fields.uint16(model.address)
        model_length = sunspec_fields.uint16(model.address + 1)
        ac_current = value(_A, unit="A")
        ac_current_phase_1 = value(_APH_A, unit="A")
        ac_current_phase_2 = value(_APH_B, unit="A")
        ac_current_phase_3 = value(_APH_C, unit="A")
        voltage_phase_1_2 = value(_PPV_AB, unit="V")
        voltage_phase_2_3 = value(_PPV_BC, unit="V")
        voltage_phase_3_1 = value(_PPV_CA, unit="V")
        voltage_phase_1 = value(_PHV_A, unit="V")
        voltage_phase_2 = value(_PHV_B, unit="V")
        voltage_phase_3 = value(_PHV_C, unit="V")
        ac_power = value(_W, signed=True, unit="W")
        frequency = value(_HZ, unit="Hz")
        apparent_power = value(_VA, signed=True, unit="VA")
        reactive_power = value(_VAR, signed=True, unit="var")
        power_factor = value(_PF, signed=True, unit="%")
        energy_total = energy(_WH)
        dc_current = value(_DCA, unit="A")
        dc_voltage = value(_DCV, unit="V")
        dc_power = value(_DCW, signed=True, unit="W")
        operating_state = sunspec_fields.enum16(tail_offset(_ST))
        vendor_operating_state = sunspec_fields.enum16(tail_offset(_ST_VND))
        events = sunspec_fields.bitfield32(tail_offset(_EVT1))

    component = InverterComponent(unit)

    async def read() -> InverterData:
        await component.async_update()
        check_model_header(
            model, component.model_id, component.model_length, "Inverter"
        )
        operating_state = component.operating_state
        vendor_operating_state = component.vendor_operating_state
        events = component.events
        return InverterData(
            ac_current=component.ac_current,
            ac_current_phase_1=component.ac_current_phase_1,
            ac_current_phase_2=component.ac_current_phase_2,
            ac_current_phase_3=component.ac_current_phase_3,
            voltage_phase_1=component.voltage_phase_1,
            voltage_phase_2=component.voltage_phase_2,
            voltage_phase_3=component.voltage_phase_3,
            voltage_phase_1_2=component.voltage_phase_1_2,
            voltage_phase_2_3=component.voltage_phase_2_3,
            voltage_phase_3_1=component.voltage_phase_3_1,
            ac_power=component.ac_power,
            frequency=component.frequency,
            apparent_power=component.apparent_power,
            reactive_power=component.reactive_power,
            power_factor=component.power_factor,
            energy_total=component.energy_total,
            dc_current=component.dc_current,
            dc_voltage=component.dc_voltage,
            dc_power=component.dc_power,
            operating_state=(
                _OPERATING_STATES.get(int(operating_state))
                if operating_state is not None
                else None
            ),
            vendor_operating_state=(
                int(vendor_operating_state)
                if vendor_operating_state is not None
                else None
            ),
            events=int(events) if events is not None else None,
        )

    return read
