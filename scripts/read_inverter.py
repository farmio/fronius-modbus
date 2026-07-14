"""Read SunSpec MPPT data from a Fronius inverter for testing.

Usage:
    uv run scripts/read_inverter.py <host> [--port 502] [--unit 1] [--storage]

GEN24 / Tauro: the inverter responds on unit ID 1 (default).
Datamanager (SnapINverter): pass --unit <inverter number> (00 -> 100);
repeat --unit to read multiple inverters of a Solar Net ring.
"""

import argparse
import asyncio
import logging
import sys

from modbus_connection import ModbusError
from modbus_connection.tmodbus import connect_tcp

from fronius_modbus import Controls, FroniusModbusInverter, Storage, SunSpecError

MODEL_NAMES = {
    1: "Common",
    101: "Inverter (int+SF, single phase)",
    102: "Inverter (int+SF, split phase)",
    103: "Inverter (int+SF, three phase)",
    111: "Inverter (float, single phase)",
    112: "Inverter (float, split phase)",
    113: "Inverter (float, three phase)",
    120: "Nameplate",
    121: "Basic Settings",
    122: "Extended Measurements & Status",
    123: "Immediate Controls",
    124: "Basic Storage Controls",
    160: "Multiple MPPT",
    211: "Meter (float, single phase)",
    213: "Meter (float, three phase)",
}


def _controls(inverter: FroniusModbusInverter) -> Controls:
    if inverter.controls is None:
        raise SunSpecError("Immediate Controls model not available")
    return inverter.controls


def _storage(inverter: FroniusModbusInverter) -> Storage:
    if inverter.storage is None:
        raise SunSpecError("Storage model not available")
    return inverter.storage


async def run_write_commands(
    inverter: FroniusModbusInverter, args: argparse.Namespace
) -> None:
    """Execute requested write commands and report the results."""
    try:
        if args.probe_write:
            allowed = await _controls(inverter).probe_write_access()
            print(f"\nWrite access probe: {'accepted' if allowed else 'REJECTED'}")

        if args.set_power_limit is not None:
            controls = _controls(inverter)
            await controls.set_power_limit(
                args.set_power_limit, revert_seconds=args.revert
            )
            await controls.async_update()
            print(
                f"\nPower limit set: {controls.power_limit} %"
                f" (enabled: {controls.enabled}, revert: {controls.revert_seconds} s)"
            )
        elif args.clear_power_limit:
            controls = _controls(inverter)
            await controls.clear_power_limit()
            await controls.async_update()
            print(
                f"\nPower limit cleared: {controls.power_limit} %"
                f" (enabled: {controls.enabled}, revert: {controls.revert_seconds} s)"
            )

        if args.set_charge_limit is not None or args.set_discharge_limit is not None:
            storage = _storage(inverter)
            await storage.set_limits(
                charge=args.set_charge_limit,
                discharge=args.set_discharge_limit,
                revert_seconds=args.revert,
            )
            await storage.async_update()
            print(
                f"\nStorage limits set: charge {storage.charge_limit} %"
                f" (enabled: {storage.charge_limit_enabled}),"
                f" discharge {storage.discharge_limit} %"
                f" (enabled: {storage.discharge_limit_enabled})"
            )
        elif args.clear_storage_limits:
            storage = _storage(inverter)
            await storage.set_limits()
            await storage.async_update()
            print(
                f"\nStorage limits cleared: charge {storage.charge_limit} %"
                f" (enabled: {storage.charge_limit_enabled}),"
                f" discharge {storage.discharge_limit} %"
                f" (enabled: {storage.discharge_limit_enabled})"
            )

        if args.set_reserve is not None:
            storage = _storage(inverter)
            await storage.set_minimum_reserve(args.set_reserve)
            await storage.async_update()
            print(f"\nMinimum reserve set: {storage.minimum_reserve} %")

        if args.set_grid_charging is not None:
            storage = _storage(inverter)
            await storage.set_grid_charging(args.set_grid_charging == "on")
            await storage.async_update()
            print(f"\nGrid charging set: {storage.grid_charging}")
    except (ModbusError, SunSpecError) as err:
        print(f"\nWrite failed: {err}")
        print('Is "inverter control via Modbus" enabled on the web interface?')


async def read_unit(host: str, unit_id: int, args: argparse.Namespace) -> None:
    """Read and print SunSpec data of one Modbus unit."""
    port: int = args.port
    print(f"\n=== {host}:{port} unit {unit_id} ===")
    try:
        connection = await connect_tcp(host, port=port)
    except ModbusError as err:
        print(f"Connection failed: {err}")
        print("Is Modbus TCP enabled on the inverter web interface?")
        return

    try:
        inverter = FroniusModbusInverter(
            connection.for_unit(unit_id), has_storage=args.storage
        )
        try:
            await inverter.discover()
        except (ModbusError, SunSpecError) as err:
            print(f"Discovery failed: {err}")
            return

        data_type = {True: "float", False: "int+SF", None: "unknown"}
        print(f"Data type setting: {data_type[inverter.float_mode]}")
        print(f"Storage detected: {inverter.has_storage}")
        print("Discovered SunSpec models:")
        for model in inverter.model_chain:
            name = MODEL_NAMES.get(model.model_id, "?")
            print(
                f"  model {model.model_id:>5}  register {model.address + 1:>5}"
                f"  length {model.length:>3}  {name}"
            )

        try:
            await inverter.async_update()
        except (ModbusError, SunSpecError) as err:
            print(f"Reading data failed: {err}")
            return

        if (identity := inverter.common) is not None:
            print("\nDevice identity:")
            print(f"  manufacturer:  {identity.manufacturer}")
            print(f"  model:         {identity.model}")
            print(f"  options:       {identity.options}")
            print(f"  version:       {identity.software_version}")
            print(f"  serial number: {identity.serial_number}")

        if (ac_dc := inverter.inverter) is not None:
            print("\nInverter:")
            print(f"  AC power:        {ac_dc.ac_power} W")
            print(f"  frequency:       {ac_dc.frequency} Hz")
            print(f"  energy total:    {ac_dc.energy_total} Wh")
            print(f"  AC current:      {ac_dc.ac_current} A")
            print(
                "  phase currents:  "
                f"{ac_dc.ac_current_phase_1} / {ac_dc.ac_current_phase_2}"
                f" / {ac_dc.ac_current_phase_3} A"
            )
            print(
                "  phase voltages:  "
                f"{ac_dc.voltage_phase_1} / {ac_dc.voltage_phase_2}"
                f" / {ac_dc.voltage_phase_3} V"
            )
            print(
                "  phase-phase:     "
                f"{ac_dc.voltage_phase_1_2} / {ac_dc.voltage_phase_2_3}"
                f" / {ac_dc.voltage_phase_3_1} V"
            )
            print(f"  apparent power:  {ac_dc.apparent_power} VA")
            print(f"  reactive power:  {ac_dc.reactive_power} var")
            print(f"  power factor:    {ac_dc.power_factor} %")
            print(
                f"  DC totals:       {ac_dc.dc_current} A / {ac_dc.dc_voltage} V"
                f" / {ac_dc.dc_power} W"
            )
            print(f"  operating state: {ac_dc.operating_state}")
            print(f"  vendor state:    {ac_dc.vendor_operating_state}")
            print(
                f"  events:          {ac_dc.events:#010x}"
                if ac_dc.events is not None
                else "  events:          None"
            )

        if (storage := inverter.storage) is not None:
            print("\nStorage:")
            print(f"  state of charge:        {storage.state_of_charge} %")
            print(f"  state:                  {storage.state}")
            print(f"  charge reference power: {storage.charge_reference_power} W")
            print(f"  minimum reserve:        {storage.minimum_reserve} %")
            print(
                f"  charge limit:           {storage.charge_limit} %"
                f" (enabled: {storage.charge_limit_enabled})"
            )
            print(
                f"  discharge limit:        {storage.discharge_limit} %"
                f" (enabled: {storage.discharge_limit_enabled})"
            )
            print(f"  grid charging:          {storage.grid_charging}")

        if (limit := inverter.controls) is not None:
            print("\nPower limit:")
            print(f"  percent:        {limit.power_limit} %")
            print(f"  enabled:        {limit.enabled}")
            print(f"  revert seconds: {limit.revert_seconds}")

        await run_write_commands(inverter, args)

        if (data := inverter.mppt) is None:
            print("No Multiple MPPT model (160) found.")
            return

        print(f"\nMPPT modules (classified with has_storage={inverter.has_storage}):")
        for index, (module, role) in enumerate(
            zip(data.modules, data.module_roles, strict=True), start=1
        ):
            print(f"  module {index}: IDStr={module.id_str!r}")
            print(f"    role:    {role}")
            print(f"    current: {module.current} A")
            print(f"    voltage: {module.voltage} V")
            print(f"    power:   {module.power} W")
            print(f"    energy:  {module.energy} Wh")
        print("\nDerived totals:")
        print(f"  PV energy total:           {data.pv_energy_total} Wh")
        print(f"  Battery charged total:     {data.storage_charge_energy_total} Wh")
        print(f"  Battery discharged total:  {data.storage_discharge_energy_total} Wh")
    finally:
        await connection.close()


async def main() -> int:
    """Run the reader for all requested units."""
    parser = argparse.ArgumentParser(
        description="Read SunSpec MPPT data from a Fronius inverter"
    )
    parser.add_argument("host", help="IP address or hostname of the inverter")
    parser.add_argument("--port", type=int, default=502, help="Modbus TCP port")
    parser.add_argument(
        "--unit",
        type=int,
        action="append",
        help="Modbus unit ID; repeatable (default: 1)",
    )
    parser.add_argument(
        "--storage",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="override the storage auto-detection (--storage / --no-storage)",
    )
    parser.add_argument(
        "--debug", action="store_true", help="enable verbose protocol logging"
    )
    write_group = parser.add_argument_group(
        "write commands",
        'require "inverter control via Modbus" enabled on the web interface',
    )
    write_group.add_argument(
        "--probe-write",
        action="store_true",
        help="check whether the device accepts Modbus writes (harmless self-write)",
    )
    write_group.add_argument(
        "--set-power-limit",
        type=float,
        metavar="PCT",
        help="limit output power to PCT %% of nominal power",
    )
    write_group.add_argument(
        "--clear-power-limit", action="store_true", help="disable the power limit"
    )
    write_group.add_argument(
        "--set-charge-limit",
        type=float,
        metavar="PCT",
        help="limit battery charge rate to PCT %% of WChaMax"
        " (negative: force discharge)",
    )
    write_group.add_argument(
        "--set-discharge-limit",
        type=float,
        metavar="PCT",
        help="limit battery discharge rate to PCT %% of WChaMax"
        " (negative: force charge)",
    )
    write_group.add_argument(
        "--clear-storage-limits",
        action="store_true",
        help="deactivate battery charge/discharge limits",
    )
    write_group.add_argument(
        "--set-reserve",
        type=float,
        metavar="PCT",
        help="set the minimum state of charge reserve",
    )
    write_group.add_argument(
        "--set-grid-charging",
        choices=["on", "off"],
        help="allow or prevent charging the battery from the grid",
    )
    write_group.add_argument(
        "--revert",
        type=int,
        default=60,
        metavar="SECONDS",
        help="auto-revert timeout for limit writes (default: 60, 0 = permanent)",
    )
    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.getLogger("tmodbus").setLevel(logging.CRITICAL)

    for unit_id in args.unit or [1]:
        await read_unit(args.host, unit_id, args)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
