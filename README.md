# fronius-modbus

Async Python library for the Modbus TCP ([SunSpec](https://sunspec.org)) interface
of Fronius inverters, built on
[modbus-connection](https://github.com/home-assistant-libs/modbus-connection).

> [!WARNING]
> **Alpha version — mostly untested.** So far this has been verified against a
> single Symo GEN24 10.0 Plus (firmware 1.40.9-1) and a SnapINverter Symo behind
> a Datamanager 2.0. Write support (power limiting, battery controls) is only
> tested against mocks. Expect breaking API changes. Use at your own risk —
> especially the write operations, which change inverter behavior. Feedback and
> test reports are very welcome!

Supports both Fronius device generations:

- **GEN24 / Tauro** — the inverter itself serves Modbus TCP (unit ID 1)
- **SnapINverter via Datamanager 2.0** — the Datamanager gateways all inverters of a
  Fronius Solar Net ring (unit ID = inverter number, `00` → `100`)

The SunSpec register map of Fronius devices is dynamic: model addresses shift with
the configured data type (*float* vs *int + SF*) and differ between generations.
This library discovers the model chain at runtime, so no register configuration is
needed — and the data type setting is detected automatically.

## Reading

- SunSpec **Common Model (1)**: manufacturer, model, software version and
  serial number.
- SunSpec **inverter models (101-103 / 111-113)** in both encodings:
  AC power, frequency, lifetime energy, per-phase currents and voltages,
  apparent/reactive power, power factor, DC totals, operating state and
  vendor state, event flags.
- SunSpec **Multiple MPPT Inverter Extension Model (160)**: DC current, voltage,
  power and lifetime energy per MPP tracker, with module role classification
  (PV string / storage charge / storage discharge) and derived totals
  (PV-only energy, battery charging/discharging energy).
- SunSpec **Basic Storage Control Model (124)**: state of charge, battery status,
  charge and discharge limits with their enable flags, minimum reserve and grid
  charging.

Each model is refreshed in as few pooled block requests as possible, and values
are read together with their scale factors so the two can never disagree.

> [!NOTE]
> With the device set to *float*, the inverter model's `energy_total` is an IEEE
> 754 single, which represents integers exactly only up to 2^24 = 16.78 MWh. Past
> that it quantises — 4 Wh steps at 48 MWh, coarser as the counter grows. The
> MPPT model counts in `acc32` with a scale factor, so `pv_energy_total` and the
> battery totals keep full resolution at any reading. Prefer them for lifetime
> energy, or set the device to *int + SF*.

## Writing

Write commands require *inverter control via Modbus* to be enabled on the device
web interface; `Controls.probe_write_access()` reports whether the device accepts
writes at all.

- **Immediate Controls (123)**: `set_power_limit()` and `clear_power_limit()` to
  limit the inverter output power.
- **Basic Storage Control (124)**: `set_limits()` for the battery charge and
  discharge rates (including forced charging), plus `set_minimum_reserve()` and
  `set_grid_charging()`.

The limit setters take a `revert_seconds` auto-revert, so a controller that dies
cannot leave the inverter constrained.

## Usage

The library only consumes a `ModbusUnit` — connection lifecycle stays with the
caller (or with Home Assistant's `modbus_connection` integration):

```python
import asyncio

from fronius_modbus import FroniusModbusInverter, GEN24_UNIT_ID
from modbus_connection.tmodbus import connect_tcp


async def main() -> None:
    connection = await connect_tcp("192.168.1.50", port=502)
    inverter = FroniusModbusInverter(connection.for_unit(GEN24_UNIT_ID))
    await inverter.discover()
    print("Data type:", "float" if inverter.float_mode else "int+SF")
    print("Storage:", inverter.has_storage)  # auto-detected during discovery

    # one pooled read refreshes every discovered model
    await inverter.async_update()

    if (identity := inverter.common) is not None:
        print(identity.manufacturer, identity.model, identity.serial_number)

    if (ac_dc := inverter.inverter) is not None:
        print("AC power:", ac_dc.ac_power, "state:", ac_dc.operating_state)

    if (mppt := inverter.mppt) is not None:
        for module, role in zip(mppt.modules, mppt.module_roles, strict=True):
            print(module.id_str, role, module.power)
        print("PV energy total:", mppt.pv_energy_total)
        print("Battery charged:", mppt.storage_charge_energy_total)
        print("Battery discharged:", mppt.storage_discharge_energy_total)

    if (storage := inverter.storage) is not None:
        print("SoC:", storage.state_of_charge, "state:", storage.state)
        await storage.set_limits(charge=50.0, revert_seconds=60)

    await connection.close()


asyncio.run(main())
```

Modbus must be enabled on the inverter web interface
(GEN24: *Communication → Modbus → Slave as Modbus TCP*;
Datamanager: *Settings → Modbus → Data output via Modbus → TCP*).

## Testing on real hardware

Two scripts in `scripts/` help test the library against an inverter (clone the
repo and run them with [uv](https://docs.astral.sh/uv/)):

`read_inverter.py` — a one-shot dump of everything the library reads, plus
optional write commands. Good for quick checks and scripting:

```bash
uv run scripts/read_inverter.py <inverter-ip>
uv run scripts/read_inverter.py <inverter-ip> --set-power-limit 80 --revert 60
uv run scripts/read_inverter.py <inverter-ip> --probe-write
```

`monitor.py` — an interactive terminal UI that polls live at a configurable
interval. It shows the data panels and a status log, a live power graph with a
selectable series list (AC power, per-MPPT and battery charge/discharge), and a
write-command dialog (press `s`) for running every write command with immediate
status feedback:

```bash
uv run scripts/monitor.py <inverter-ip> --interval 2
```

> Write commands require "inverter control via Modbus" to be enabled on the
> device web interface. Limit writes default to a 60 second auto-revert as a
> safety net.

## Testing support

`fronius_modbus.testing` provides `build_sunspec_map()` to build SunSpec register
maps for `modbus_connection.mock.MockModbusUnit`:

```python
from fronius_modbus.testing import MpptModuleSpec, build_sunspec_map
from modbus_connection.mock import MockModbusConnection

connection = MockModbusConnection()
connection.for_unit(1).holding.update(
    build_sunspec_map(
        [MpptModuleSpec(id_str="String 1", current=82, voltage=4021, power=3300)],
        float_mode=True,
    )
)
```

## License

GPL-3.0-or-later, see [LICENSE](LICENSE).

## Disclaimer

This is an unofficial library, not affiliated with Fronius International GmbH.
Based on the public Fronius Modbus TCP & RTU operating instructions and the
Fronius SunSpec register maps (fronius.com/QR-link/0006).
