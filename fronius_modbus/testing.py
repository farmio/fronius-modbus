"""SunSpec register map builder for tests.

Builds holding-register maps for ``modbus_connection.mock.MockModbusUnit``::

    unit.holding.update(build_sunspec_map([MpptModuleSpec(id_str="String 1")]))
"""

import math
import struct
from dataclasses import dataclass

from .sunspec import SUNSPEC_BASE_ADDRESS

NOT_IMPLEMENTED_UINT16 = 0xFFFF
NOT_IMPLEMENTED_INT16 = 0x8000
NOT_IMPLEMENTED_ACC32 = 0

# Fixed scale factors the builder uses to encode int+SF inverter models.
_INT_SF_EXPONENTS = {
    "ac": -2,  # A_SF
    "voltage": -2,  # V_SF
    "power": -1,  # W_SF, VA_SF, VAr_SF, DCW_SF
    "frequency": -2,  # Hz_SF
    "percent": -1,  # PF_SF
    "energy": 0,  # WH_SF
    "dc": -2,  # DCA_SF, DCV_SF
}


@dataclass
class MpptModuleSpec:
    """Raw (unscaled) register values for one MPPT module of SunSpec model 160."""

    id_str: str
    current: int = NOT_IMPLEMENTED_UINT16
    voltage: int = NOT_IMPLEMENTED_UINT16
    power: int = NOT_IMPLEMENTED_UINT16
    energy: int = NOT_IMPLEMENTED_ACC32


@dataclass
class InverterModelSpec:
    """Engineering values for a SunSpec inverter model.

    The builder encodes them as float32 (models 111-113) or as integers with
    fixed scale factors (models 101-103), so values should be exactly
    representable in both: use multiples of 0.25 for currents and voltages,
    0.5 for powers, 0.01 for frequency and integers for energy.
    ``None`` encodes the type's not-implemented sentinel.
    """

    ac_current: float | None = None
    ac_current_phase_1: float | None = None
    ac_current_phase_2: float | None = None
    ac_current_phase_3: float | None = None
    voltage_phase_1: float | None = None
    voltage_phase_2: float | None = None
    voltage_phase_3: float | None = None
    voltage_phase_1_2: float | None = None
    voltage_phase_2_3: float | None = None
    voltage_phase_3_1: float | None = None
    ac_power: float | None = None
    frequency: float | None = None
    apparent_power: float | None = None
    reactive_power: float | None = None
    power_factor: float | None = None
    energy_total: int | None = None
    dc_current: float | None = None
    dc_voltage: float | None = None
    dc_power: float | None = None
    operating_state: int | None = None
    vendor_operating_state: int | None = None
    events: int = 0


def _string_words(value: str, register_count: int) -> list[int]:
    raw = value.encode("ascii").ljust(register_count * 2, b"\x00")
    return [int.from_bytes(raw[i : i + 2], "big") for i in range(0, len(raw), 2)]


def _float32_words(value: float | None) -> list[int]:
    raw = struct.pack(">f", math.nan if value is None else value)
    return [int.from_bytes(raw[:2], "big"), int.from_bytes(raw[2:], "big")]


def _scaled_word(value: float | None, exponent: int, *, signed: bool = False) -> int:
    if value is None:
        return NOT_IMPLEMENTED_INT16 if signed else NOT_IMPLEMENTED_UINT16
    raw = round(value * 10.0**-exponent)
    return raw & 0xFFFF


def _uint32_words(value: int) -> list[int]:
    return [value >> 16, value & 0xFFFF]


def _sf_word(exponent: int) -> int:
    return exponent & 0xFFFF


def _common_model_data(
    manufacturer: str, device_model: str, version: str, serial_number: str
) -> list[int]:
    return [
        *_string_words(manufacturer, 16),
        *_string_words(device_model, 16),
        *_string_words("", 8),  # Opt
        *_string_words(version, 8),
        *_string_words(serial_number, 16),
        1,  # DA
        0,  # Pad
    ]


def _float_inverter_data(spec: InverterModelSpec) -> list[int]:
    return [
        *_float32_words(spec.ac_current),
        *_float32_words(spec.ac_current_phase_1),
        *_float32_words(spec.ac_current_phase_2),
        *_float32_words(spec.ac_current_phase_3),
        *_float32_words(spec.voltage_phase_1_2),
        *_float32_words(spec.voltage_phase_2_3),
        *_float32_words(spec.voltage_phase_3_1),
        *_float32_words(spec.voltage_phase_1),
        *_float32_words(spec.voltage_phase_2),
        *_float32_words(spec.voltage_phase_3),
        *_float32_words(spec.ac_power),
        *_float32_words(spec.frequency),
        *_float32_words(spec.apparent_power),
        *_float32_words(spec.reactive_power),
        *_float32_words(spec.power_factor),
        *_float32_words(spec.energy_total),
        *_float32_words(spec.dc_current),
        *_float32_words(spec.dc_voltage),
        *_float32_words(spec.dc_power),
        *_float32_words(None),  # TmpCab
        *_float32_words(None),  # TmpSnk
        *_float32_words(None),  # TmpTrns
        *_float32_words(None),  # TmpOt
        spec.operating_state if spec.operating_state is not None else 0xFFFF,  # St
        (
            spec.vendor_operating_state
            if spec.vendor_operating_state is not None
            else 0xFFFF
        ),  # StVnd
        *_uint32_words(spec.events),  # Evt1
        *_uint32_words(0),  # Evt2
        *_uint32_words(0),  # EvtVnd1
        *_uint32_words(0),  # EvtVnd2
        *_uint32_words(0),  # EvtVnd3
        *_uint32_words(0),  # EvtVnd4
    ]


def _int_sf_inverter_data(spec: InverterModelSpec) -> list[int]:
    sf = _INT_SF_EXPONENTS
    return [
        _scaled_word(spec.ac_current, sf["ac"]),
        _scaled_word(spec.ac_current_phase_1, sf["ac"]),
        _scaled_word(spec.ac_current_phase_2, sf["ac"]),
        _scaled_word(spec.ac_current_phase_3, sf["ac"]),
        _sf_word(sf["ac"]),  # A_SF
        _scaled_word(spec.voltage_phase_1_2, sf["voltage"]),
        _scaled_word(spec.voltage_phase_2_3, sf["voltage"]),
        _scaled_word(spec.voltage_phase_3_1, sf["voltage"]),
        _scaled_word(spec.voltage_phase_1, sf["voltage"]),
        _scaled_word(spec.voltage_phase_2, sf["voltage"]),
        _scaled_word(spec.voltage_phase_3, sf["voltage"]),
        _sf_word(sf["voltage"]),  # V_SF
        _scaled_word(spec.ac_power, sf["power"], signed=True),
        _sf_word(sf["power"]),  # W_SF
        _scaled_word(spec.frequency, sf["frequency"]),
        _sf_word(sf["frequency"]),  # Hz_SF
        _scaled_word(spec.apparent_power, sf["power"], signed=True),
        _sf_word(sf["power"]),  # VA_SF
        _scaled_word(spec.reactive_power, sf["power"], signed=True),
        _sf_word(sf["power"]),  # VAr_SF
        _scaled_word(spec.power_factor, sf["percent"], signed=True),
        _sf_word(sf["percent"]),  # PF_SF
        *_uint32_words(spec.energy_total if spec.energy_total is not None else 0),  # WH
        _sf_word(sf["energy"]),  # WH_SF
        _scaled_word(spec.dc_current, sf["dc"]),
        _sf_word(sf["dc"]),  # DCA_SF
        _scaled_word(spec.dc_voltage, sf["dc"]),
        _sf_word(sf["dc"]),  # DCV_SF
        _scaled_word(spec.dc_power, sf["power"], signed=True),
        _sf_word(sf["power"]),  # DCW_SF
        NOT_IMPLEMENTED_INT16,  # TmpCab
        NOT_IMPLEMENTED_INT16,  # TmpSnk
        NOT_IMPLEMENTED_INT16,  # TmpTrns
        NOT_IMPLEMENTED_INT16,  # TmpOt
        _sf_word(0),  # Tmp_SF
        spec.operating_state if spec.operating_state is not None else 0xFFFF,  # St
        (
            spec.vendor_operating_state
            if spec.vendor_operating_state is not None
            else 0xFFFF
        ),  # StVnd
        *_uint32_words(spec.events),  # Evt1
        *_uint32_words(0),  # Evt2
        *_uint32_words(0),  # EvtVnd1
        *_uint32_words(0),  # EvtVnd2
        *_uint32_words(0),  # EvtVnd3
        *_uint32_words(0),  # EvtVnd4
    ]


def build_sunspec_map(
    modules: list[MpptModuleSpec],
    *,
    float_mode: bool = True,
    include_mppt_model: bool = True,
    current_sf: int = -1,
    voltage_sf: int = -1,
    power_sf: int = 0,
    energy_sf: int = 0,
    storage_wcha_max: int | None = None,
    storage_soc: float | None = None,
    storage_state: int | None = None,
    inverter: InverterModelSpec | None = None,
    manufacturer: str = "Fronius",
    device_model: str = "Test Model",
    version: str = "1.2.3",
    serial_number: str = "1234567890",
) -> dict[int, int]:
    """Build a holding register map of a chain of SunSpec models.

    MPPT module scale factors of -1 mean raw current / voltage values are in
    deciampere / decivolt.

    ``storage_wcha_max`` adds a Basic Storage Control Model (124) reporting
    the given WChaMax value - storage-capable inverters expose it with 0
    when no storage is connected; plain inverters don't expose it at all.
    ``storage_soc`` (percent) and ``storage_state`` (ChaSt enum) fill its
    read-only values.
    """
    registers: dict[int, int] = {
        SUNSPEC_BASE_ADDRESS: 0x5375,  # "Su"
        SUNSPEC_BASE_ADDRESS + 1: 0x6E53,  # "nS"
    }
    address = SUNSPEC_BASE_ADDRESS + 2

    def add_model(model_id: int, data: list[int], length: int | None = None) -> None:
        nonlocal address
        length = len(data) if length is None else length
        registers[address] = model_id
        registers[address + 1] = length
        for offset, word in enumerate(data):
            registers[address + 2 + offset] = word
        address += 2 + length

    add_model(1, _common_model_data(manufacturer, device_model, version, serial_number))
    # the float and int+SF inverter models have different lengths, shifting
    # the addresses of subsequent models like on a real device
    inverter_spec = inverter if inverter is not None else InverterModelSpec()
    if float_mode:
        add_model(113, _float_inverter_data(inverter_spec))
    else:
        add_model(103, _int_sf_inverter_data(inverter_spec))

    if include_mppt_model:
        data = [
            current_sf & 0xFFFF,
            voltage_sf & 0xFFFF,
            power_sf & 0xFFFF,
            energy_sf & 0xFFFF,
            0,  # Evt
            0,
            len(modules),  # N
            NOT_IMPLEMENTED_UINT16,  # TmsPer
        ]
        for index, module in enumerate(modules, start=1):
            data += [
                index,  # ID
                *_string_words(module.id_str, 8),  # IDStr
                module.current,  # DCA
                module.voltage,  # DCV
                module.power,  # DCW
                module.energy >> 16,  # DCWH
                module.energy & 0xFFFF,
                0,  # Tms
                0,
                NOT_IMPLEMENTED_INT16,  # Tmp
                4,  # DCSt: MPPT
                0,  # DCEvt
                0,
            ]
        add_model(160, data)

    if storage_wcha_max is not None:
        storage_data = [0] * 24
        storage_data[0] = storage_wcha_max  # WChaMax
        storage_data[6] = (  # ChaState, encoded with ChaState_SF -2
            _scaled_word(storage_soc, -2)
            if storage_soc is not None
            else NOT_IMPLEMENTED_UINT16
        )
        storage_data[9] = (  # ChaSt
            storage_state if storage_state is not None else NOT_IMPLEMENTED_UINT16
        )
        storage_data[16] = _sf_word(0)  # WChaMax_SF
        storage_data[20] = _sf_word(-2)  # ChaState_SF
        add_model(124, storage_data)

    # end model marker
    registers[address] = 0xFFFF
    registers[address + 1] = 0
    return registers


__all__ = [
    "InverterModelSpec",
    "MpptModuleSpec",
    "build_sunspec_map",
]
