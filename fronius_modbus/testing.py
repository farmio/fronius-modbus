"""SunSpec register map builder for tests.

Builds holding-register maps for ``modbus_connection.mock.MockModbusUnit``::

    unit.holding.update(build_sunspec_map([MpptModuleSpec(id_str="String 1")]))
"""

from dataclasses import dataclass

from .sunspec import SUNSPEC_BASE_ADDRESS

NOT_IMPLEMENTED_UINT16 = 0xFFFF
NOT_IMPLEMENTED_INT16 = 0x8000
NOT_IMPLEMENTED_ACC32 = 0


@dataclass
class MpptModuleSpec:
    """Raw (unscaled) register values for one MPPT module of SunSpec model 160."""

    id_str: str
    current: int = NOT_IMPLEMENTED_UINT16
    voltage: int = NOT_IMPLEMENTED_UINT16
    power: int = NOT_IMPLEMENTED_UINT16
    energy: int = NOT_IMPLEMENTED_ACC32


def _string_words(value: str, register_count: int) -> list[int]:
    raw = value.encode("ascii").ljust(register_count * 2, b"\x00")
    return [int.from_bytes(raw[i : i + 2], "big") for i in range(0, len(raw), 2)]


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
) -> dict[int, int]:
    """Build a holding register map of a chain of SunSpec models.

    Scale factors of -1 mean raw current / voltage values are in
    deciampere / decivolt.

    ``storage_wcha_max`` adds a Basic Storage Control Model (124) reporting
    the given WChaMax value - storage-capable inverters expose it with 0
    when no storage is connected; plain inverters don't expose it at all.
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

    # common model - content is irrelevant for these tests
    add_model(1, [], length=66)
    # the float and int+SF inverter models have different lengths, shifting
    # the addresses of subsequent models like on a real device
    if float_mode:
        add_model(113, [], length=60)
    else:
        add_model(103, [], length=50)

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
        # WChaMax at data offset 0; remaining 23 registers read 0
        add_model(124, [storage_wcha_max], length=24)

    # end model marker
    registers[address] = 0xFFFF
    registers[address + 1] = 0
    return registers
