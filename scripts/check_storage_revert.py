"""Check how a Fronius inverter handles the battery limit fallback period.

Usage:
    uv run scripts/check_storage_revert.py <host> [--port 502] [--unit 1]
        [--period 60]

Answers three questions about ``InOutWRte_RvrtTms`` (storage model 124),
each in its own phase:

1. Does the inverter drop a battery limit once the period has passed with
   no Modbus traffic at all?
2. Does reading the model restart the period, as the Fronius manual says
   every received Modbus message does?
3. Does writing the limit again restart it?

The probe is behaviour-neutral: it enables the charge limit at 100 %,
which allows what the inverter does anyway. Everything is put back the way
it was found. Each phase takes about 1.5 periods, so the default run is
around five minutes. "Inverter control via Modbus" has to be enabled on the
device web interface.
"""

import argparse
import asyncio
import sys
import time

from modbus_connection import ModbusError
from modbus_connection.tmodbus import connect_tcp

from fronius_modbus import FroniusModbusInverter, Storage, SunSpecError

POLL_SECONDS = 10


def _stamp(started: float) -> str:
    return f"[{time.monotonic() - started:5.0f} s]"


async def _arm(storage: Storage, period: int) -> None:
    """Enable the 100 % charge limit with the period, in the documented order."""
    await storage.write("revert_seconds", period)
    await storage.write("charge_limit", 100.0)
    await storage.write("charge_limit_enabled", True)
    await storage.async_update()
    if storage.charge_limit_enabled is not True:
        raise SunSpecError("the inverter did not accept the charge limit")


async def _watch(
    storage: Storage,
    period: int,
    *,
    touch: str | None,
) -> float | None:
    """Poll until the limit is released; return when, in seconds, or None.

    ``touch`` names what each poll does besides reading: ``"write"`` sends
    the limit value again, ``None`` leaves it at the read.
    """
    started = time.monotonic()
    deadline = started + period * 1.5 + POLL_SECONDS
    while time.monotonic() < deadline:
        await asyncio.sleep(POLL_SECONDS)
        if touch == "write":
            await storage.write("charge_limit", 100.0)
        await storage.async_update()
        enabled = storage.charge_limit_enabled
        print(
            f"  {_stamp(started)} enabled={enabled}"
            f" revert_seconds={storage.revert_seconds}"
        )
        if enabled is not True:
            return time.monotonic() - started
    return None


async def run(host: str, port: int, unit_id: int, period: int) -> int:
    """Run the three phases and print a verdict."""
    connection = await connect_tcp(host, port=port)
    try:
        inverter = FroniusModbusInverter(connection.for_unit(unit_id))
        await inverter.discover()
        if (storage := inverter.storage) is None:
            print("No storage model (124) found - not a hybrid inverter?")
            return 1
        await storage.async_update()
        original = {
            "revert_seconds": storage.revert_seconds,
            "charge_limit": storage.charge_limit,
            "charge_limit_enabled": storage.charge_limit_enabled,
        }
        print(f"Found as: {original}")
        if original["charge_limit_enabled"] is True:
            print("A charge limit is already active - not touching it.")
            return 1

        try:
            print(f"\nPhase 1: arm, then {period * 1.5:.0f} s of silence")
            await _arm(storage, period)
            await asyncio.sleep(period * 1.5)
            await storage.async_update()
            silent_release = storage.charge_limit_enabled is not True
            print(f"  released without traffic: {silent_release}")
            if not silent_release:
                print("\nVerdict: the inverter ignores InOutWRte_RvrtTms.")
                return 0

            print(f"\nPhase 2: arm, then read every {POLL_SECONDS} s")
            await _arm(storage, period)
            read_release = await _watch(storage, period, touch=None)

            print(f"\nPhase 3: arm, then write the limit every {POLL_SECONDS} s")
            await _arm(storage, period)
            write_release = await _watch(storage, period, touch="write")
        finally:
            print("\nRestoring the original settings")
            await storage.write("charge_limit_enabled", False)
            for field, value in original.items():
                if value is not None:
                    await storage.write(field, value)

        print("\nVerdict:")
        print("  the period is honoured: yes")
        if read_release is None:
            print("  reads restart the period: yes (the manual is right)")
        else:
            print(
                f"  reads restart the period: no, released after {read_release:.0f} s"
            )
        if write_release is None:
            print("  writes restart the period: yes (a heartbeat keeps the limit)")
        else:
            print(
                "  writes restart the period: NO, released after"
                f" {write_release:.0f} s - the heartbeat would not work"
            )
        return 0
    finally:
        await connection.close()


def main() -> int:
    """Parse the arguments and run."""
    parser = argparse.ArgumentParser(
        description="Check the battery limit fallback period of a Fronius inverter"
    )
    parser.add_argument("host", help="IP address or hostname of the inverter")
    parser.add_argument("--port", type=int, default=502, help="Modbus TCP port")
    parser.add_argument("--unit", type=int, default=1, help="Modbus unit ID")
    parser.add_argument(
        "--period",
        type=int,
        default=60,
        help="fallback period to test with, in seconds (default: 60)",
    )
    args = parser.parse_args()
    try:
        return asyncio.run(run(args.host, args.port, args.unit, args.period))
    except (ModbusError, SunSpecError) as err:
        print(f"Failed: {err}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
