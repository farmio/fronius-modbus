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

Currently implemented (read-only):

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
- SunSpec **Basic Storage Control Model (124)**, read-only values: state of
  charge, battery status, nominal charge/discharge power reference.

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

    identity = await inverter.read_device_identity()
    print(identity.manufacturer, identity.model, identity.serial_number)

    ac_dc = await inverter.read_inverter()
    print("AC power:", ac_dc.ac_power, "state:", ac_dc.operating_state)

    if inverter.has_mppt:
        data = await inverter.read_mppt()
        for module in data.modules:
            print(module.index, module.id_str, module.role, module.power)
        print("PV energy total:", data.pv_energy_total)
        print("Battery charged:", data.storage_charge_energy_total)
        print("Battery discharged:", data.storage_discharge_energy_total)

    if inverter.has_storage_model:
        storage = await inverter.read_storage()
        print("SoC:", storage.state_of_charge, "state:", storage.state)

    await connection.close()


asyncio.run(main())
```

Modbus must be enabled on the inverter web interface
(GEN24: *Communication → Modbus → Slave as Modbus TCP*;
Datamanager: *Settings → Modbus → Data output via Modbus → TCP*).

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

## Disclaimer

This is an unofficial library, not affiliated with Fronius International GmbH.
Based on the public Fronius Modbus TCP & RTU operating instructions and the
Fronius SunSpec register maps (fronius.com/QR-link/0006).
