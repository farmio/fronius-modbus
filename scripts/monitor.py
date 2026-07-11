"""Live Textual monitor for a Fronius inverter over Modbus TCP.

Polls the inverter at a configurable interval and shows the read data, and
provides interactive controls for every write command with status feedback.

Usage:
    uv run scripts/monitor.py <host> [--port 502] [--unit 1] [--interval 2]

Write commands require "inverter control via Modbus" to be enabled on the
device web interface.
"""

import argparse
import asyncio
import sys
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, ClassVar

from modbus_connection import ModbusConnection, ModbusError
from modbus_connection.tmodbus import connect_tcp
from rich.table import Table
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import BindingType
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Button, Footer, Header, Input, Label, RichLog, Static

from fronius_modbus import FroniusModbusInverter, SunSpecError

type Connector = Callable[[str, int], Awaitable[ModbusConnection]]


async def _default_connect(host: str, port: int) -> ModbusConnection:
    return await connect_tcp(host, port=port)


def _fmt(value: float | None, unit: str = "", digits: int = 2) -> str:
    """Format an optional number with a unit, showing an em dash for None."""
    if value is None:
        return "—"
    formatted = f"{value:.{digits}f}" if isinstance(value, float) else str(value)
    return f"{formatted} {unit}".strip()


def _bool(value: bool | None) -> str:
    """Format an optional boolean."""
    return "—" if value is None else ("yes" if value else "no")


class MonitorApp(App[None]):
    """A live monitor and control panel for one Fronius Modbus unit."""

    CSS = """
    #body { height: 1fr; }
    #data { width: 60%; }
    #controls { width: 40%; padding: 0 1; }
    #data Static { border: round $panel; padding: 0 1; margin-bottom: 1; }
    #controls Input { width: 1fr; margin-bottom: 1; }
    #controls Button { width: 1fr; margin-bottom: 1; }
    #controls Label { color: $text-muted; margin-top: 1; }
    #log { height: 10; border: round $panel; }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        ("q", "quit", "Quit"),
        ("r", "refresh", "Refresh now"),
    ]

    def __init__(
        self,
        *,
        host: str,
        port: int,
        unit_id: int,
        interval: float,
        storage_override: bool | None,
        connect: Connector = _default_connect,
    ) -> None:
        """Set up the monitor for one inverter unit."""
        super().__init__()
        self._host = host
        self._port = port
        self._unit_id = unit_id
        self._interval = interval
        self._storage_override = storage_override
        self._connect = connect
        self._connection: ModbusConnection | None = None
        self._inverter: FroniusModbusInverter | None = None
        self._lock = asyncio.Lock()
        self._timer: Any = None

    def compose(self) -> ComposeResult:
        """Build the widget tree."""
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            with VerticalScroll(id="data"):
                yield Static(id="connection")
                yield Static(id="inverter")
                yield Static(id="mppt")
                yield Static(id="storage")
                yield Static(id="powerlimit")
            with VerticalScroll(id="controls"):
                yield Label("Poll interval (seconds)")
                yield Input(str(self._interval), id="interval_input", type="number")
                yield Label("Revert timeout (seconds, 0 = permanent)")
                yield Input("60", id="revert_input", type="integer")
                yield Label("Output power limit (0-100 %)")
                yield Input(placeholder="percent", id="power_input", type="number")
                yield Button("Set power limit", id="set_power", variant="primary")
                yield Button("Clear power limit", id="clear_power")
                yield Label("Battery charge / discharge limit (-100..100 %)")
                yield Input(placeholder="charge %", id="charge_input", type="number")
                yield Input(
                    placeholder="discharge %", id="discharge_input", type="number"
                )
                yield Button("Set storage limits", id="set_storage", variant="primary")
                yield Button("Clear storage limits", id="clear_storage")
                yield Label("Minimum reserve (0-100 %)")
                yield Input(placeholder="percent", id="reserve_input", type="number")
                yield Button("Set minimum reserve", id="set_reserve", variant="primary")
                yield Label("Grid charging")
                with Horizontal():
                    yield Button("Grid charge ON", id="grid_on")
                    yield Button("Grid charge OFF", id="grid_off")
                yield Button("Probe write access", id="probe", variant="warning")
        yield RichLog(id="log", markup=True, wrap=True)
        yield Footer()

    def on_mount(self) -> None:
        """Set the window title."""
        self.title = "fronius-modbus monitor"
        self.sub_title = f"{self._host}:{self._port} unit {self._unit_id}"

    async def on_ready(self) -> None:
        """Connect, do an initial poll, then start the poll timer.

        Runs after the first paint, so all panels are mounted.
        """
        await self._reconnect()
        await self._poll()
        self._timer = self.set_interval(self._interval, self._poll)

    def _log(self, message: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        self.query_one("#log", RichLog).write(f"[dim]{stamp}[/dim] {message}")

    async def _reconnect(self) -> None:
        if self._connection is not None:
            await self._connection.close()
            self._connection = None
            self._inverter = None
        try:
            connection = await self._connect(self._host, self._port)
            inverter = FroniusModbusInverter(
                connection.for_unit(self._unit_id), has_storage=self._storage_override
            )
            await inverter.discover()
        except (ModbusError, SunSpecError) as err:
            self._log(f"[red]Connection failed:[/red] {err}")
            return
        self._connection = connection
        self._inverter = inverter
        self._log("[green]Connected and discovered SunSpec models[/green]")

    async def _poll(self) -> None:
        """Read all available models and refresh the panels."""
        if self._inverter is None or self._lock.locked():
            return
        async with self._lock:
            try:
                await self._refresh_panels(self._inverter)
            except (ModbusError, SunSpecError) as err:
                self._log(f"[red]Read failed:[/red] {err} — reconnecting")
                await self._reconnect()

    async def _refresh_panels(self, inverter: FroniusModbusInverter) -> None:
        now = datetime.now().strftime("%H:%M:%S")
        data_type = {True: "float", False: "int+SF", None: "unknown"}
        connection = Table.grid(padding=(0, 1))
        connection.add_row(
            "Connection", f"{self._host}:{self._port} unit {self._unit_id}"
        )
        connection.add_row("Data type", data_type[inverter.float_mode])
        connection.add_row("Storage detected", _bool(inverter.has_storage))
        connection.add_row("Poll interval", f"{self._interval:g} s")
        connection.add_row("Last update", now)
        self._panel("connection", "Connection", connection)

        if inverter.has_inverter_model:
            data = await inverter.read_inverter()
            table = Table.grid(padding=(0, 1))
            table.add_row("AC power", _fmt(data.ac_power, "W"))
            table.add_row("Frequency", _fmt(data.frequency, "Hz"))
            table.add_row("Energy total", _fmt(data.energy_total, "Wh"))
            table.add_row("AC current", _fmt(data.ac_current, "A"))
            table.add_row(
                "Phase voltages",
                f"{_fmt(data.voltage_phase_1)} / {_fmt(data.voltage_phase_2)}"
                f" / {_fmt(data.voltage_phase_3)} V",
            )
            table.add_row(
                "Apparent / reactive",
                f"{_fmt(data.apparent_power, 'VA')}"
                f" / {_fmt(data.reactive_power, 'var')}",
            )
            table.add_row("Power factor", _fmt(data.power_factor, "%"))
            table.add_row("DC power", _fmt(data.dc_power, "W"))
            table.add_row("Operating state", str(data.operating_state or "—"))
            self._panel("inverter", "Inverter", table)

        if inverter.has_mppt:
            mppt = await inverter.read_mppt()
            table = Table(expand=True)
            for column in ("Module", "Role", "Current", "Voltage", "Power", "Energy"):
                table.add_column(column)
            for module in mppt.modules:
                table.add_row(
                    f"{module.index} {module.id_str}",
                    module.role.value,
                    _fmt(module.current, "A"),
                    _fmt(module.voltage, "V"),
                    _fmt(module.power, "W"),
                    _fmt(module.energy, "Wh"),
                )
            table.add_section()
            table.add_row(
                "totals",
                "",
                "",
                "",
                f"PV {_fmt(mppt.pv_energy_total, 'Wh')}",
                f"+{_fmt(mppt.storage_charge_energy_total, 'Wh')}"
                f" / -{_fmt(mppt.storage_discharge_energy_total, 'Wh')}",
            )
            self._panel("mppt", "MPPT modules", table)

        if inverter.has_storage_model:
            storage = await inverter.read_storage()
            table = Table.grid(padding=(0, 1))
            table.add_row("State of charge", _fmt(storage.state_of_charge, "%"))
            table.add_row("State", str(storage.state or "—"))
            table.add_row(
                "Charge ref. power", _fmt(storage.charge_reference_power, "W")
            )
            table.add_row("Minimum reserve", _fmt(storage.minimum_reserve, "%"))
            table.add_row(
                "Charge limit",
                f"{_fmt(storage.charge_limit, '%')} (enabled: "
                f"{_bool(storage.charge_limit_enabled)})",
            )
            table.add_row(
                "Discharge limit",
                f"{_fmt(storage.discharge_limit, '%')} (enabled: "
                f"{_bool(storage.discharge_limit_enabled)})",
            )
            table.add_row("Grid charging", _bool(storage.grid_charging))
            self._panel("storage", "Storage", table)

        if inverter.has_immediate_controls:
            limit = await inverter.read_power_limit()
            table = Table.grid(padding=(0, 1))
            table.add_row("Limit", _fmt(limit.percent, "%"))
            table.add_row("Enabled", _bool(limit.enabled))
            table.add_row("Revert", _fmt(limit.revert_seconds, "s", digits=0))
            self._panel("powerlimit", "Power limit", table)

    def _panel(self, widget_id: str, title: str, body: Table) -> None:
        renderable = Table.grid()
        renderable.add_row(Text(title, style="bold"))
        renderable.add_row(body)
        self.query_one(f"#{widget_id}", Static).update(renderable)

    def _input(self, widget_id: str) -> float | None:
        value = self.query_one(f"#{widget_id}", Input).value.strip()
        return float(value) if value else None

    async def _run_write(self, description: str, action: Awaitable[None]) -> None:
        """Run a write action under the lock and report the outcome."""
        if self._inverter is None:
            self._log("[red]Not connected[/red]")
            return
        async with self._lock:
            try:
                await action
            except ValueError as err:
                self._log(f"[red]{description} rejected:[/red] {err}")
                return
            except (ModbusError, SunSpecError) as err:
                self._log(f"[red]{description} failed:[/red] {err}")
                self._log('Is "inverter control via Modbus" enabled on the web UI?')
                return
            self._log(f"[green]{description} ok[/green]")
        await self._poll()

    @on(Button.Pressed)
    async def _on_button(self, event: Button.Pressed) -> None:
        inverter = self._inverter
        if inverter is None:
            self._log("[red]Not connected[/red]")
            return
        revert_value = self.query_one("#revert_input", Input).value.strip()
        revert = int(revert_value) if revert_value else 0
        match event.button.id:
            case "set_power":
                percent = self._input("power_input")
                if percent is None:
                    self._log("[red]Enter a power limit percent[/red]")
                    return
                await self._run_write(
                    f"Set power limit {percent} %",
                    inverter.set_power_limit(percent, revert_seconds=revert),
                )
            case "clear_power":
                await self._run_write("Clear power limit", inverter.clear_power_limit())
            case "set_storage":
                await self._run_write(
                    "Set storage limits",
                    inverter.set_storage_limits(
                        charge=self._input("charge_input"),
                        discharge=self._input("discharge_input"),
                        revert_seconds=revert,
                    ),
                )
            case "clear_storage":
                await self._run_write(
                    "Clear storage limits", inverter.set_storage_limits()
                )
            case "set_reserve":
                reserve = self._input("reserve_input")
                if reserve is None:
                    self._log("[red]Enter a reserve percent[/red]")
                    return
                await self._run_write(
                    f"Set minimum reserve {reserve} %",
                    inverter.set_minimum_reserve(reserve),
                )
            case "grid_on":
                await self._run_write(
                    "Enable grid charging", inverter.set_grid_charging(True)
                )
            case "grid_off":
                await self._run_write(
                    "Disable grid charging", inverter.set_grid_charging(False)
                )
            case "probe":
                await self._probe(inverter)

    async def _probe(self, inverter: FroniusModbusInverter) -> None:
        async with self._lock:
            try:
                allowed = await inverter.probe_write_access()
            except (ModbusError, SunSpecError) as err:
                self._log(f"[red]Write probe failed:[/red] {err}")
                return
        if allowed:
            self._log("[green]Write access: accepted[/green]")
        else:
            self._log("[yellow]Write access: REJECTED by device[/yellow]")

    @on(Input.Submitted, "#interval_input")
    def _on_interval(self, event: Input.Submitted) -> None:
        try:
            interval = float(event.value)
        except ValueError:
            return
        if interval <= 0:
            return
        self._interval = interval
        if self._timer is not None:
            self._timer.stop()
        self._timer = self.set_interval(interval, self._poll)
        self._log(f"Poll interval set to {interval:g} s")

    async def action_refresh(self) -> None:
        """Poll immediately."""
        await self._poll()


def main() -> int:
    """Parse arguments and run the monitor app."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("host", help="IP address or hostname of the inverter")
    parser.add_argument("--port", type=int, default=502, help="Modbus TCP port")
    parser.add_argument("--unit", type=int, default=1, help="Modbus unit ID")
    parser.add_argument(
        "--interval", type=float, default=2.0, help="poll interval in seconds"
    )
    parser.add_argument(
        "--storage",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="override the storage auto-detection",
    )
    args = parser.parse_args()
    app = MonitorApp(
        host=args.host,
        port=args.port,
        unit_id=args.unit,
        interval=args.interval,
        storage_override=args.storage,
    )
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
