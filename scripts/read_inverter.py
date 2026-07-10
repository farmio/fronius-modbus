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

from fronius_modbus import FroniusModbusInverter, SunSpecError

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


async def read_unit(
    host: str, port: int, unit_id: int, has_storage: bool | None
) -> None:
    """Read and print SunSpec data of one Modbus unit."""
    print(f"\n=== {host}:{port} unit {unit_id} ===")
    try:
        connection = await connect_tcp(host, port=port)
    except ModbusError as err:
        print(f"Connection failed: {err}")
        print("Is Modbus TCP enabled on the inverter web interface?")
        return

    try:
        inverter = FroniusModbusInverter(
            connection.for_unit(unit_id), has_storage=has_storage
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

        if not inverter.has_mppt:
            print("No Multiple MPPT model (160) found.")
            return

        try:
            data = await inverter.read_mppt()
        except (ModbusError, SunSpecError) as err:
            print(f"Reading MPPT data failed: {err}")
            return

        print(f"\nMPPT modules (classified with has_storage={inverter.has_storage}):")
        for module in data.modules:
            print(f"  module {module.index}: IDStr={module.id_str!r}")
            print(f"    role:    {module.role}")
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
    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.getLogger("tmodbus").setLevel(logging.CRITICAL)

    for unit_id in args.unit or [1]:
        await read_unit(args.host, args.port, unit_id, args.storage)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
