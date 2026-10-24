from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import typer
import yaml
from dotenv import load_dotenv
from loguru import logger
from rich.console import Console

from agent.browser import run_portal_automation, BrowserAutomationError
from agent.models import AttendanceSummary
from agent.scraper import scrape_attendance_from_page
from agent.speech import build_attendance_message
from agent.vision import analyze_attendance_screenshot
from agent.voice import speak

# UTF-8 on Windows
if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

app = typer.Typer(
    name="attendance",
    help="Fetch your TCS portal attendance and read it aloud.",
    add_completion=False,
)
console = Console(highlight=False)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def setup_logging(logs_dir: Path, verbose: bool = False) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.add(
        logs_dir / "attendance.log",
        rotation="5 MB",
        retention="10 days",
        level="DEBUG",
        format="{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | {name}:{function}:{line} - {message}",
    )
    if verbose:
        logger.add(sys.stderr, level="INFO",
                   format="{time:HH:mm:ss} {level:<7} {message}")


def load_configuration(base_dir: Path) -> dict:
    config_file = base_dir / "config.yaml"
    if not config_file.exists():
        print(f"Error: config.yaml not found at {config_file}", file=sys.stderr)
        sys.exit(1)
    with open(config_file, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def validate_credentials(base_dir: Path) -> tuple[str, str, str]:
    env_path = base_dir / ".env"
    if not env_path.exists():
        print(".env file not found. Copy .env.example and fill in your credentials.",
              file=sys.stderr)
        sys.exit(1)

    load_dotenv(dotenv_path=env_path)
    portal_url = os.getenv("PORTAL_URL", "").strip()
    username   = os.getenv("PORTAL_USERNAME", "").strip()
    password   = os.getenv("PORTAL_PASSWORD", "").strip()

    missing = []
    if not portal_url:
        missing.append("PORTAL_URL")
    if not username or username in ("your_username_here", "your_username"):
        missing.append("PORTAL_USERNAME")
    if not password or password in ("your_password_here", "your_password"):
        missing.append("PORTAL_PASSWORD")

    if missing:
        print(f"Missing credentials in .env: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    return portal_url, username, password


def show_last_attendance(out_dir: Path, mute: bool = False) -> None:
    """Display and optionally speak the most recent saved report."""
    if not out_dir.exists():
        print("No previous runs found.")
        return
    json_files = sorted(out_dir.glob("attendance_*.json"),
                        key=os.path.getmtime, reverse=True)
    if not json_files:
        print("No saved attendance reports found.")
        return
    try:
        with open(json_files[0], "r", encoding="utf-8") as f:
            summary = AttendanceSummary.model_validate(json.load(f))
        message = build_attendance_message(summary)
        print(message)
        if not mute:
            speak(message)
    except Exception as exc:
        print(f"Could not read saved report: {exc}", file=sys.stderr)


# ─────────────────────────────────────────────────────────────────────────────
# CLI command
# ─────────────────────────────────────────────────────────────────────────────

@app.command()
def main_command(
    headless: bool = typer.Option(False, "--headless",
        help="Run browser without a visible window."),
    last: bool = typer.Option(False, "--last",
        help="Show the previous saved result without opening the browser."),
    mute: bool = typer.Option(False, "--mute",
        help="Skip voice output even if ElevenLabs is configured."),
    verbose: bool = typer.Option(False, "--verbose",
        help="Print progress messages (browser steps, scraper status, etc.)."),
) -> None:
    """Fetch your TCS portal attendance and read it back to you."""
    base_dir = Path(__file__).resolve().parent.parent

    config   = load_configuration(base_dir)
    paths    = config.get("paths", {})
    logs_dir = base_dir / paths.get("logs_dir", "logs")
    out_dir  = base_dir / paths.get("out_dir", "out")
    out_dir.mkdir(parents=True, exist_ok=True)

    setup_logging(logs_dir, verbose=verbose)

    if last:
        show_last_attendance(out_dir, mute=mute)
        return

    portal_url, username, password = validate_credentials(base_dir)

    if verbose:
        print("Launching browser...", flush=True)
    logger.info("Starting attendance monitor workflow...")

    # ── Step 1: browser + live DOM scrape ────────────────────────────────────
    summary: Optional[AttendanceSummary] = None
    try:
        screenshot_path, summary = run_portal_automation(
            config=config,
            portal_url=portal_url,
            username=username,
            password=password,
            headless=headless,
            base_dir=base_dir,
            on_page_ready=scrape_attendance_from_page,
        )
    except BrowserAutomationError as err:
        print(f"Browser error: {err.message}", file=sys.stderr)
        sys.exit(1)
    except Exception as err:
        print(f"Unexpected error: {err}", file=sys.stderr)
        sys.exit(1)

    # ── Step 2: vision fallback if scraper found nothing ────────────────────
    if not summary:
        model_name = config.get("vision", {}).get("model_name", "moondream")
        max_width  = config.get("screenshot", {}).get("max_width", 1280)
        if verbose:
            print(f"DOM scrape empty — trying {model_name}...", flush=True)
        logger.info(f"Falling back to vision model '{model_name}'...")
        try:
            summary, raw_text = analyze_attendance_screenshot(
                image_path=screenshot_path,
                model_name=model_name,
                max_width=max_width,
            )
        except Exception as err:
            print(f"Vision error: {err}", file=sys.stderr)
            sys.exit(1)

        if not summary:
            date_str     = datetime.now().strftime("%Y%m%d_%H%M%S")
            raw_out_file = out_dir / f"attendance_{date_str}_raw.txt"
            raw_out_file.write_text(raw_text, encoding="utf-8")
            print("Could not read attendance data. Raw output saved to "
                  f"{raw_out_file.name}.", file=sys.stderr)
            sys.exit(1)

    # ── Step 3: save JSON ────────────────────────────────────────────────────
    date_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = out_dir / f"attendance_{date_str}.json"
    out_file.write_text(summary.model_dump_json(indent=2), encoding="utf-8")
    logger.info(f"Saved to {out_file.resolve()}")

    # ── Step 4: build message, print, speak ──────────────────────────────────
    message = build_attendance_message(summary)
    print(message)
    if not mute:
        speak(message)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
