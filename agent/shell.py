"""
agent/shell.py
--------------
A lightweight interactive terminal shell for the Attendance Agent.

Run it with:
    python -m agent.shell
  or (after pip install -e .):
    agent-shell

The shell lets you call registered tasks by name, view help, and quit —
all from a single prompt loop.  Adding new tasks is as simple as
decorating a function with @shell.register(name, description).
"""
from __future__ import annotations

import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional

from rich.console import Console
from rich.table import Table

# ──────────────────────────────────────────────────────────────────────────────
# Ensure UTF-8 on Windows
# ──────────────────────────────────────────────────────────────────────────────
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

console = Console(highlight=False)


# ──────────────────────────────────────────────────────────────────────────────
# Task registry
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class Task:
    name: str
    description: str
    usage: str
    handler: Callable[[List[str]], None]


class TaskRegistry:
    """Holds all registered shell tasks and dispatches commands to them."""

    def __init__(self) -> None:
        self._tasks: Dict[str, Task] = {}

    def register(
        self,
        name: str,
        description: str = "",
        usage: str = "",
    ) -> Callable:
        """Decorator factory — registers a function as a named shell task."""
        def decorator(fn: Callable[[List[str]], None]) -> Callable:
            self._tasks[name.lower()] = Task(
                name=name.lower(),
                description=description,
                usage=usage or name,
                handler=fn,
            )
            return fn
        return decorator

    def get(self, name: str) -> Optional[Task]:
        return self._tasks.get(name.lower())

    def all_tasks(self) -> List[Task]:
        return list(self._tasks.values())


registry = TaskRegistry()


# ──────────────────────────────────────────────────────────────────────────────
# Built-in help / clear / quit commands
# ──────────────────────────────────────────────────────────────────────────────

def _print_help() -> None:
    table = Table(
        title="Available Commands",
        show_header=True,
        header_style="bold magenta",
        border_style="dim",
        show_lines=False,
    )
    table.add_column("Command", style="bold cyan", min_width=18)
    table.add_column("Description", style="white")
    table.add_column("Usage", style="dim")

    for task in registry.all_tasks():
        table.add_row(task.name, task.description, task.usage)

    table.add_row("help", "Show this help message", "help")
    table.add_row("clear", "Clear the terminal screen", "clear")
    table.add_row("quit / exit", "Exit the shell", "quit")

    console.print()
    console.print(table)
    console.print()


def _print_banner() -> None:
    console.print("Attendance Agent  —  type 'help' to list commands, 'quit' to exit.\n")


# ──────────────────────────────────────────────────────────────────────────────
# Registered tasks
# ──────────────────────────────────────────────────────────────────────────────

@registry.register(
    name="attendance",
    description="Fetch & analyse period-wise attendance from TCS portal",
    usage="attendance [--headless] [--last] [--mute] [--verbose]",
)
def _task_attendance(args: List[str]) -> None:
    """Delegates to the main attendance CLI workflow."""
    import os
    import yaml
    from datetime import datetime

    from agent.browser import run_portal_automation, BrowserAutomationError
    from agent.models import AttendanceSummary
    from agent.scraper import scrape_attendance_from_page
    from agent.speech import build_attendance_message
    from agent.vision import analyze_attendance_screenshot
    from agent.voice import speak
    from agent.cli import (
        load_configuration,
        validate_credentials,
        setup_logging,
        show_last_attendance,
    )

    base_dir = Path(__file__).resolve().parent.parent

    config = load_configuration(base_dir)
    paths_cfg = config.get("paths", {})
    logs_dir = base_dir / paths_cfg.get("logs_dir", "logs")
    out_dir = base_dir / paths_cfg.get("out_dir", "out")
    out_dir.mkdir(parents=True, exist_ok=True)

    setup_logging(logs_dir)

    headless = "--headless" in args
    relogin  = "--relogin" in args
    last     = "--last" in args
    mute     = "--mute" in args
    verbose  = "--verbose" in args

    if last:
        show_last_attendance(out_dir, mute=mute)
        return

    portal_url, username, password = validate_credentials(base_dir)

    if verbose:
        console.print("Launching browser...")

    summary = None
    try:
        screenshot_path, summary = run_portal_automation(
            config=config,
            portal_url=portal_url,
            username=username,
            password=password,
            headless=headless,
            relogin=relogin,
            base_dir=base_dir,
            on_page_ready=scrape_attendance_from_page,
        )
    except BrowserAutomationError as err:
        console.print(f"Browser error: {err.message}")
        return
    except Exception as err:
        console.print(f"Unexpected error: {err}")
        return

    if not summary:
        model_name = config.get("vision", {}).get("model_name", "moondream")
        max_width  = config.get("screenshot", {}).get("max_width", 1280)
        if verbose:
            console.print(f"DOM scrape empty — trying {model_name}...")
        try:
            summary, raw_text = analyze_attendance_screenshot(
                image_path=screenshot_path,
                model_name=model_name,
                max_width=max_width,
            )
        except Exception as err:
            console.print(f"Vision error: {err}")
            return

    date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = out_dir / f"attendance_{date_str}.json"

    if summary:
        out_file.write_text(summary.model_dump_json(indent=2), encoding="utf-8")
        message = build_attendance_message(summary)
        print(message)
        if not mute:
            speak(message)
    else:
        raw_out = out_dir / f"attendance_{date_str}_raw.txt"
        raw_out.write_text(raw_text, encoding="utf-8")
        console.print(f"Could not read attendance data. Raw output: {raw_out.name}")


# ──────────────────────────────────────────────────────────────────────────────
# Main REPL loop
# ──────────────────────────────────────────────────────────────────────────────

def run_shell() -> None:
    """Starts the interactive shell REPL."""
    _print_banner()

    while True:
        try:
            raw = console.input("[bold green]agent[/bold green][dim]>[/dim] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Bye![/dim]")
            break

        if not raw:
            continue

        try:
            parts = shlex.split(raw)
        except ValueError as e:
            console.print(f"[yellow]Parse error:[/yellow] {e}")
            continue

        cmd, *cmd_args = parts

        if cmd in ("quit", "exit", "q"):
            console.print("[dim]Bye![/dim]")
            break
        elif cmd in ("help", "?", "h"):
            _print_help()
        elif cmd == "clear":
            console.clear()
            _print_banner()
        else:
            task = registry.get(cmd)
            if task:
                try:
                    task.handler(cmd_args)
                except SystemExit:
                    pass  # tasks may call sys.exit; catch it so the shell keeps running
                except Exception as exc:
                    console.print(f"[bold red]Error running '{cmd}':[/bold red] {exc}")
            else:
                console.print(
                    f"[yellow]Unknown command:[/yellow] [bold]{cmd}[/bold]  "
                    f"(type [cyan]help[/cyan] to see available commands)"
                )


def main() -> None:
    """Entry point used by the console script."""
    run_shell()


if __name__ == "__main__":
    main()
