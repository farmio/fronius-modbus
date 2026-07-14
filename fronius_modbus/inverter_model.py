"""SunSpec inverter models (101-103 int+SF / 111-113 float).

These are the only models whose encoding follows the data type setting of
the device: with "float" the values are IEEE 754 floats (models 111-113),
with "int+SF" they are integers with scale factors (models 101-103). The two
component classes expose the same attributes, so consumers can treat them
alike. Single and split phase models report unsupported phases as
not-implemented. Register addresses per the SunSpec model definitions,
relative to the model start.
"""

from enum import IntEnum

from modbus_connection.model import sunspec as sunspec_fields

from .sunspec import SunSpecComponent


class OperatingState(IntEnum):
    """SunSpec inverter operating state (St)."""

    OFF = 1
    SLEEPING = 2
    STARTING = 3
    MPPT = 4
    THROTTLED = 5
    SHUTTING_DOWN = 6
    FAULT = 7
    STANDBY = 8


class InverterFloat(SunSpecComponent):
    """An inverter model in float encoding (111-113)."""

    ac_current = sunspec_fields.float32(2, unit="A")
    ac_current_phase_1 = sunspec_fields.float32(4, unit="A")
    ac_current_phase_2 = sunspec_fields.float32(6, unit="A")
    ac_current_phase_3 = sunspec_fields.float32(8, unit="A")
    voltage_phase_1_2 = sunspec_fields.float32(10, unit="V")
    voltage_phase_2_3 = sunspec_fields.float32(12, unit="V")
    voltage_phase_3_1 = sunspec_fields.float32(14, unit="V")
    voltage_phase_1 = sunspec_fields.float32(16, unit="V")
    voltage_phase_2 = sunspec_fields.float32(18, unit="V")
    voltage_phase_3 = sunspec_fields.float32(20, unit="V")
    ac_power = sunspec_fields.float32(22, unit="W")
    frequency = sunspec_fields.float32(24, unit="Hz")
    apparent_power = sunspec_fields.float32(26, unit="VA")
    reactive_power = sunspec_fields.float32(28, unit="var")
    power_factor = sunspec_fields.float32(30, unit="%")  # -100..100
    energy_total = sunspec_fields.float32(32, unit="Wh")
    dc_current = sunspec_fields.float32(34, unit="A")
    dc_voltage = sunspec_fields.float32(36, unit="V")
    dc_power = sunspec_fields.float32(38, unit="W")
    operating_state = sunspec_fields.enum16(48, OperatingState)
    vendor_operating_state = sunspec_fields.enum16(49)
    events = sunspec_fields.bitfield32(50)  # Evt1


class InverterInteger(SunSpecComponent):
    """An inverter model in int+SF encoding (101-103)."""

    ac_current = sunspec_fields.uint16(2, scale_register=6, unit="A")
    ac_current_phase_1 = sunspec_fields.uint16(3, scale_register=6, unit="A")
    ac_current_phase_2 = sunspec_fields.uint16(4, scale_register=6, unit="A")
    ac_current_phase_3 = sunspec_fields.uint16(5, scale_register=6, unit="A")
    voltage_phase_1_2 = sunspec_fields.uint16(7, scale_register=13, unit="V")
    voltage_phase_2_3 = sunspec_fields.uint16(8, scale_register=13, unit="V")
    voltage_phase_3_1 = sunspec_fields.uint16(9, scale_register=13, unit="V")
    voltage_phase_1 = sunspec_fields.uint16(10, scale_register=13, unit="V")
    voltage_phase_2 = sunspec_fields.uint16(11, scale_register=13, unit="V")
    voltage_phase_3 = sunspec_fields.uint16(12, scale_register=13, unit="V")
    ac_power = sunspec_fields.int16(14, scale_register=15, unit="W")
    frequency = sunspec_fields.uint16(16, scale_register=17, unit="Hz")
    apparent_power = sunspec_fields.int16(18, scale_register=19, unit="VA")
    reactive_power = sunspec_fields.int16(20, scale_register=21, unit="var")
    power_factor = sunspec_fields.int16(22, scale_register=23, unit="%")
    energy_total = sunspec_fields.acc32(24, scale_register=26, unit="Wh")
    dc_current = sunspec_fields.uint16(27, scale_register=28, unit="A")
    dc_voltage = sunspec_fields.uint16(29, scale_register=30, unit="V")
    dc_power = sunspec_fields.int16(31, scale_register=32, unit="W")
    operating_state = sunspec_fields.enum16(38, OperatingState)
    vendor_operating_state = sunspec_fields.enum16(39)
    events = sunspec_fields.bitfield32(40)  # Evt1


type Inverter = InverterFloat | InverterInteger
