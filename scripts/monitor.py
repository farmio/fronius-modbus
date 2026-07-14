"""Live Textual monitor for a Fronius inverter over Modbus TCP.

Polls the inverter at a configurable interval, shows the read data and a live
power graph, and provides a write-command dialog (hotkey ``s``) with status
feedback.

Usage:
    uv run scripts/monitor.py <host> [--port 502] [--unit 1] [--interval 2]

Write commands require "inverter control via Modbus" to be enabled on the
device web interface.
"""

import argparse
import asyncio
import sys
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import datetime
from time import monotonic
from typing import Any, ClassVar, cast

from modbus_connection import ModbusConnection, ModbusError
from modbus_connection.tmodbus import connect_tcp
from rich.table import Table
from rich.text import Text
from textual import on
from textual.app import App, ComposeResult
from textual.binding import BindingType
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    ContentSwitcher,
    Footer,
    Header,
    Input,
    Label,
    RichLog,
    Select,
    SelectionList,
    Static,
)
from textual_plotext import PlotextPlot

from fronius_modbus import (
    Controls,
    FroniusModbusInverter,
    ModuleRole,
    Storage,
    SunSpecError,
)


def _controls(inverter: FroniusModbusInverter) -> Controls:
    if inverter.controls is None:
        raise SunSpecError("Immediate Controls model not available")
    return inverter.controls


def _storage(inverter: FroniusModbusInverter) -> Storage:
    if inverter.storage is None:
        raise SunSpecError("Storage model not available")
    return inverter.storage


type Connector = Callable[[str, int], Awaitable[ModbusConnection]]

_HISTORY_LENGTH = 600  # samples kept for the graph


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


class ControlsScreen(ModalScreen[None]):
    """A modal dialog to set one inverter value at a time.

    A menu selects which value to control; the matching input(s) and action
    buttons are then shown, keeping the dialog uncluttered.
    """

    BINDINGS: ClassVar[list[BindingType]] = [("escape", "close", "Close")]

    _MENU: ClassVar[list[tuple[str, str]]] = [
        ("Output power limit", "power"),
        ("Battery charge / discharge limit", "storage"),
        ("Minimum reserve", "reserve"),
        ("Grid charging", "grid"),
        ("Poll interval", "interval"),
        ("Probe write access", "probe"),
    ]

    CSS = """
    ControlsScreen { align: center middle; }
    #dialog {
        width: 68; height: auto; padding: 1 2;
        border: thick $primary; background: $surface;
    }
    #dialog > Label { color: $text-muted; margin-top: 1; }
    #switcher { height: auto; margin-top: 1; }
    #switcher Input { margin-bottom: 1; }
    .actions { height: auto; }
    .actions Button { width: 1fr; margin-right: 1; }
    #dialog_status { margin-top: 1; }
    #close { width: 1fr; margin-top: 1; }
    """

    def compose(self) -> ComposeResult:
        """Build the dialog."""
        with Vertical(id="dialog"):
            yield Label("Set value (requires Modbus control enabled)")
            yield Select(
                self._MENU, id="menu", value="power", allow_blank=False, compact=True
            )
            with ContentSwitcher(initial="panel_power", id="switcher"):
                with Vertical(id="panel_power"):
                    yield Label("Output power limit (0-100 %)")
                    yield Input(placeholder="percent", id="power_input", type="number")
                    yield Label("Revert timeout (seconds, 0 = permanent)")
                    yield Input("60", id="power_revert", type="integer")
                    with Horizontal(classes="actions"):
                        yield Button("Set", id="set_power", variant="primary")
                        yield Button("Reset", id="clear_power")
                with Vertical(id="panel_storage"):
                    yield Label("Charge limit (-100..100 %, empty = off)")
                    yield Input(
                        placeholder="charge %", id="charge_input", type="number"
                    )
                    yield Label("Discharge limit (-100..100 %, empty = off)")
                    yield Input(
                        placeholder="discharge %", id="discharge_input", type="number"
                    )
                    yield Label("Revert timeout (seconds, 0 = permanent)")
                    yield Input("60", id="storage_revert", type="integer")
                    with Horizontal(classes="actions"):
                        yield Button("Set", id="set_storage", variant="primary")
                        yield Button("Reset", id="clear_storage")
                with Vertical(id="panel_reserve"):
                    yield Label("Minimum reserve (0-100 %)")
                    yield Input(
                        placeholder="percent", id="reserve_input", type="number"
                    )
                    with Horizontal(classes="actions"):
                        yield Button("Set", id="set_reserve", variant="primary")
                with Vertical(id="panel_grid"):
                    yield Label("Charge the battery from the grid")
                    with Horizontal(classes="actions"):
                        yield Button("Enable", id="grid_on", variant="primary")
                        yield Button("Disable", id="grid_off")
                with Vertical(id="panel_interval"):
                    yield Label("Poll interval (seconds)")
                    yield Input(id="interval_input", type="number")
                    with Horizontal(classes="actions"):
                        yield Button("Apply", id="apply_interval", variant="primary")
                with Vertical(id="panel_probe"):
                    yield Label("Check whether the device accepts Modbus writes")
                    with Horizontal(classes="actions"):
                        yield Button("Run probe", id="probe", variant="warning")
            yield Static(id="dialog_status")
            yield Button("Close", id="close")

    def on_mount(self) -> None:
        """Prefill the poll interval from the running app."""
        self.query_one("#interval_input", Input).value = f"{self._monitor.interval:g}"

    @property
    def _monitor(self) -> "MonitorApp":
        return cast("MonitorApp", self.app)

    def _status(self, message: str) -> None:
        self.query_one("#dialog_status", Static).update(message)

    def _input(self, widget_id: str) -> float | None:
        value = self.query_one(f"#{widget_id}", Input).value.strip()
        return float(value) if value else None

    def _revert(self, widget_id: str) -> int:
        value = self.query_one(f"#{widget_id}", Input).value.strip()
        return int(value) if value else 0

    def action_close(self) -> None:
        """Close the dialog."""
        self.dismiss()

    @on(Select.Changed, "#menu")
    def _on_menu(self, event: Select.Changed) -> None:
        self.query_one("#switcher", ContentSwitcher).current = f"panel_{event.value}"

    @on(Button.Pressed)
    async def _on_button(self, event: Button.Pressed) -> None:
        monitor = self._monitor
        inverter = monitor.current_inverter
        match event.button.id:
            case "close":
                self.dismiss()
            case "apply_interval":
                interval = self._input("interval_input")
                if interval is None or interval <= 0:
                    self._status("[red]Enter a positive interval[/red]")
                    return
                monitor.set_poll_interval(interval)
                self._status(f"Poll interval set to {interval:g} s")
            case _ if inverter is None:
                self._status("[red]Not connected[/red]")
            case "set_power":
                percent = self._input("power_input")
                if percent is None:
                    self._status("[red]Enter a power limit percent[/red]")
                    return
                self._status(
                    await monitor.run_write(
                        f"Set power limit {percent} %",
                        lambda: _controls(inverter).set_power_limit(
                            percent, revert_seconds=self._revert("power_revert")
                        ),
                    )
                )
            case "clear_power":
                self._status(
                    await monitor.run_write(
                        "Clear power limit",
                        lambda: _controls(inverter).clear_power_limit(),
                    )
                )
            case "set_storage":
                self._status(
                    await monitor.run_write(
                        "Set storage limits",
                        lambda: _storage(inverter).set_limits(
                            charge=self._input("charge_input"),
                            discharge=self._input("discharge_input"),
                            revert_seconds=self._revert("storage_revert"),
                        ),
                    )
                )
            case "clear_storage":
                self._status(
                    await monitor.run_write(
                        "Clear storage limits",
                        lambda: _storage(inverter).set_limits(),
                    )
                )
            case "set_reserve":
                reserve = self._input("reserve_input")
                if reserve is None:
                    self._status("[red]Enter a reserve percent[/red]")
                    return
                self._status(
                    await monitor.run_write(
                        f"Set minimum reserve {reserve} %",
                        lambda: _storage(inverter).set_minimum_reserve(reserve),
                    )
                )
            case "grid_on":
                self._status(
                    await monitor.run_write(
                        "Enable grid charging",
                        lambda: _storage(inverter).set_grid_charging(True),
                    )
                )
            case "grid_off":
                self._status(
                    await monitor.run_write(
                        "Disable grid charging",
                        lambda: _storage(inverter).set_grid_charging(False),
                    )
                )
            case "probe":
                self._status(await monitor.run_probe())


class MonitorApp(App[None]):
    """A live monitor and control panel for one Fronius Modbus unit."""

    CSS = """
    #top { height: 60%; }
    #data { width: 62%; }
    #data Static { border: round $panel; padding: 0 1; margin-bottom: 1; }
    #log { width: 38%; border: round $panel; }
    #bottom { height: 40%; }
    #series { width: 28; border: round $panel; }
    #graph { width: 1fr; border: round $panel; }
    """

    BINDINGS: ClassVar[list[BindingType]] = [
        ("s", "controls", "Set…"),
        ("r", "refresh", "Refresh now"),
        ("q", "quit", "Quit"),
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
        self.interval = interval
        self._storage_override = storage_override
        self._connect = connect
        self._connection: ModbusConnection | None = None
        self._inverter: FroniusModbusInverter | None = None
        self._lock = asyncio.Lock()
        self._timer: Any = None
        self._history: deque[tuple[float, dict[str, float | None]]] = deque(
            maxlen=_HISTORY_LENGTH
        )
        self._series: list[tuple[str, str]] = []  # (key, label)

    @property
    def current_inverter(self) -> FroniusModbusInverter | None:
        """Return the connected inverter, or None."""
        return self._inverter

    def compose(self) -> ComposeResult:
        """Build the widget tree."""
        yield Header(show_clock=True)
        with Horizontal(id="top"):
            with VerticalScroll(id="data"):
                yield Static(id="connection")
                yield Static(id="inverter")
                yield Static(id="mppt")
                yield Static(id="storage")
                yield Static(id="powerlimit")
            yield RichLog(id="log", markup=True, wrap=True)
        with Horizontal(id="bottom"):
            yield SelectionList[str](id="series")
            yield PlotextPlot(id="graph")
        yield Footer()

    def on_mount(self) -> None:
        """Set the window title."""
        self.title = "fronius-modbus monitor"
        self.sub_title = f"{self._host}:{self._port} unit {self._unit_id}"

    async def on_ready(self) -> None:
        """Connect, poll once, then start the poll timer (after first paint)."""
        await self._reconnect()
        await self._poll()
        self._timer = self.set_interval(self.interval, self._poll)

    def action_controls(self) -> None:
        """Open the write-command dialog."""
        self.push_screen(ControlsScreen())

    async def action_refresh(self) -> None:
        """Poll immediately."""
        await self._poll()

    def set_poll_interval(self, interval: float) -> None:
        """Change the polling interval and restart the timer."""
        self.interval = interval
        if self._timer is not None:
            self._timer.stop()
        self._timer = self.set_interval(interval, self._poll)
        self._log(f"Poll interval set to {interval:g} s")

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
        """Read all available models, refresh the panels and the graph."""
        if self._inverter is None or self._lock.locked():
            return
        async with self._lock:
            try:
                await self._refresh(self._inverter)
            except (ModbusError, SunSpecError) as err:
                self._log(f"[red]Read failed:[/red] {err} — reconnecting")
                await self._reconnect()

    async def _refresh(self, inverter: FroniusModbusInverter) -> None:
        await inverter.async_update()
        now = datetime.now().strftime("%H:%M:%S")
        data_type = {True: "float", False: "int+SF", None: "unknown"}
        connection = Table.grid(padding=(0, 1))
        connection.add_row("Data type", data_type[inverter.float_mode])
        connection.add_row("Storage detected", _bool(inverter.has_storage))
        connection.add_row("Poll interval", f"{self.interval:g} s")
        connection.add_row("Last update", now)
        self._panel("connection", "Connection", connection)

        sample: dict[str, float | None] = {}

        if (data := inverter.inverter) is not None:
            sample["ac_power"] = data.ac_power
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
            table.add_row("Power factor", _fmt(data.power_factor, "%"))
            table.add_row("DC power", _fmt(data.dc_power, "W"))
            state = data.operating_state
            table.add_row("Operating state", state.name if state is not None else "—")
            self._panel("inverter", "Inverter", table)

        if (mppt := inverter.mppt) is not None:
            table = Table(expand=True)
            for column in ("Module", "Role", "Current", "Voltage", "Power", "Energy"):
                table.add_column(column)
            for index, (module, role) in enumerate(
                zip(mppt.modules, mppt.module_roles, strict=True), start=1
            ):
                sample[f"mod_{index}"] = module.power
                table.add_row(
                    f"{index} {module.id_str}",
                    role.value,
                    _fmt(module.current, "A"),
                    _fmt(module.voltage, "V"),
                    _fmt(module.power, "W"),
                    _fmt(module.energy, "Wh"),
                )
            self._panel("mppt", "MPPT modules", table)
            self._ensure_series(inverter.inverter is not None, mppt.module_roles)

        if (storage := inverter.storage) is not None:
            table = Table.grid(padding=(0, 1))
            table.add_row("State of charge", _fmt(storage.state_of_charge, "%"))
            charge_state = storage.state
            table.add_row(
                "State", charge_state.name if charge_state is not None else "—"
            )
            table.add_row(
                "Charge ref. power", _fmt(storage.charge_reference_power, "W")
            )
            table.add_row("Minimum reserve", _fmt(storage.minimum_reserve, "%"))
            table.add_row(
                "Charge limit",
                f"{_fmt(storage.charge_limit, '%')}"
                f" ({_bool(storage.charge_limit_enabled)})",
            )
            table.add_row(
                "Discharge limit",
                f"{_fmt(storage.discharge_limit, '%')}"
                f" ({_bool(storage.discharge_limit_enabled)})",
            )
            table.add_row("Grid charging", _bool(storage.grid_charging))
            self._panel("storage", "Storage", table)

        if (limit := inverter.controls) is not None:
            table = Table.grid(padding=(0, 1))
            table.add_row("Limit", _fmt(limit.power_limit, "%"))
            table.add_row("Enabled", _bool(limit.enabled))
            table.add_row("Revert", _fmt(limit.revert_seconds, "s", digits=0))
            self._panel("powerlimit", "Power limit", table)

        self._history.append((monotonic(), sample))
        self._redraw_graph()

    def _ensure_series(self, has_ac: bool, roles: list[ModuleRole]) -> None:
        """Populate the series selector once, from the discovered layout."""
        if self._series:
            return
        series: list[tuple[str, str]] = []
        if has_ac:
            series.append(("ac_power", "AC power"))
        role_labels = {
            ModuleRole.STORAGE_CHARGE: "Battery charge",
            ModuleRole.STORAGE_DISCHARGE: "Battery discharge",
            ModuleRole.STORAGE_BIDIRECTIONAL: "Battery",
        }
        for index, role in enumerate(roles, start=1):
            label = role_labels.get(role, f"MPPT {index}")
            series.append((f"mod_{index}", label))
        self._series = series
        selector = self.query_one("#series", SelectionList)
        selector.border_title = "Series"
        selector.add_options([(label, key, True) for key, label in series])

    def _redraw_graph(self) -> None:
        graph = self.query_one("#graph", PlotextPlot)
        plt = graph.plt
        plt.clear_figure()
        graph.border_title = "Power (W)"
        selected = set(self.query_one("#series", SelectionList).selected)
        if self._history and selected:
            start = self._history[0][0]
            times = [stamp - start for stamp, _ in self._history]
            for key, label in self._series:
                if key not in selected:
                    continue
                points = [
                    (t, values[key])
                    for t, (_, values) in zip(times, self._history, strict=True)
                    if values.get(key) is not None
                ]
                if points:
                    plt.plot(
                        [t for t, _ in points],
                        [v for _, v in points],
                        label=label,
                        marker="braille",
                    )
            plt.xlabel("seconds")
        graph.refresh()

    @on(SelectionList.SelectedChanged, "#series")
    def _on_series_changed(self) -> None:
        self._redraw_graph()

    def _panel(self, widget_id: str, title: str, body: Table) -> None:
        renderable = Table.grid()
        renderable.add_row(Text(title, style="bold"))
        renderable.add_row(body)
        self.query_one(f"#{widget_id}", Static).update(renderable)

    async def run_write(
        self, description: str, action: Callable[[], Awaitable[None]]
    ) -> str:
        """Run a write action under the lock, log and return a status string."""
        if self._inverter is None:
            self._log("[red]Not connected[/red]")
            return "[red]Not connected[/red]"
        async with self._lock:
            try:
                await action()
            except ValueError as err:
                message = f"{description} rejected: {err}"
                self._log(f"[red]{message}[/red]")
                return f"[red]{message}[/red]"
            except (ModbusError, SunSpecError) as err:
                self._log(f"[red]{description} failed:[/red] {err}")
                self._log('Is "inverter control via Modbus" enabled on the web UI?')
                return f"[red]{description} failed: {err}[/red]"
        self._log(f"[green]{description} ok[/green]")
        await self._poll()
        return f"[green]{description} ok[/green]"

    async def run_probe(self) -> str:
        """Probe write access and return a status string."""
        if self._inverter is None:
            return "[red]Not connected[/red]"
        async with self._lock:
            try:
                allowed = await _controls(self._inverter).probe_write_access()
            except (ModbusError, SunSpecError) as err:
                self._log(f"[red]Write probe failed:[/red] {err}")
                return f"[red]Write probe failed: {err}[/red]"
        if allowed:
            self._log("[green]Write access: accepted[/green]")
            return "[green]Write access: accepted[/green]"
        self._log("[yellow]Write access: REJECTED by device[/yellow]")
        return "[yellow]Write access: REJECTED by device[/yellow]"


def main() -> int:
    """Parse arguments and run the monitor app."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
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
    MonitorApp(
        host=args.host,
        port=args.port,
        unit_id=args.unit,
        interval=args.interval,
        storage_override=args.storage,
    ).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
