from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple
from loguru import logger
from playwright.sync_api import (
    sync_playwright,
    Browser,
    BrowserContext,
    Page,
    TimeoutError as PlaywrightTimeoutError,
)


# ─────────────────────────────────────────────────────────────────────────────
# Custom exception
# ─────────────────────────────────────────────────────────────────────────────

class BrowserAutomationError(Exception):
    """Raised when a browser automation step fails after all recovery attempts."""
    def __init__(self, step_name: str, message: str, original_exc: Optional[Exception] = None):
        self.step_name = step_name
        self.message = message
        self.original_exc = original_exc
        super().__init__(f"Step '{step_name}' failed: {message}")


# ─────────────────────────────────────────────────────────────────────────────
# Screenshot helpers
# ─────────────────────────────────────────────────────────────────────────────

def _save_debug_screenshot(page: Optional[Page], shots_dir: Path, step_name: str) -> Optional[Path]:
    """Saves a debug screenshot when an error occurs."""
    if not page:
        return None
    try:
        shots_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_step = step_name.lower().replace(" ", "_").replace("/", "_")
        shot_path = shots_dir / f"error_{safe_step}_{timestamp}.png"
        page.screenshot(path=str(shot_path), full_page=True)
        logger.error(f"Debug screenshot: {shot_path.resolve()}")
        return shot_path
    except Exception as exc:
        logger.warning(f"Could not capture debug screenshot: {exc}")
        return None


def _capture_screenshot(
    page: Page,
    shots_dir: Path,
    timestamp: str,
    clicked_in_iframe: bool,
) -> Path:
    """
    Takes the best available screenshot.
    When content is inside an iframe, also tries an element-level screenshot
    of that iframe for a cleaner view (no portal chrome/sidebars).
    Returns path to the best screenshot.
    """
    full_page_path = shots_dir / f"attendance_{timestamp}.png"
    page.screenshot(path=str(full_page_path), full_page=True)
    logger.info(f"Full-page screenshot saved: {full_page_path.name}")

    if not clicked_in_iframe:
        return full_page_path

    try:
        iframe_el = page.locator("iframe").first
        iframe_shot_path = shots_dir / f"attendance_{timestamp}_iframe.png"
        iframe_el.screenshot(path=str(iframe_shot_path))
        logger.info(f"Iframe screenshot saved: {iframe_shot_path.name}")
        return iframe_shot_path
    except Exception as e:
        logger.debug(f"Iframe screenshot failed: {e}. Using full-page.")
        return full_page_path


# ─────────────────────────────────────────────────────────────────────────────
# AI page diagnosis — asks moondream what it sees when something breaks
# ─────────────────────────────────────────────────────────────────────────────

def _ai_diagnose_page(
    page: Optional[Page],
    shots_dir: Path,
    step_name: str,
    model_name: str = "moondream",
) -> str:
    """
    Snapshot the current page and ask the vision model to describe it.
    Returns a short diagnosis string — used for error recovery decisions.
    Falls back silently if Ollama is unavailable.
    """
    if not page:
        return ""
    shot_path = _save_debug_screenshot(page, shots_dir, f"ai_diagnosis_{step_name}")
    if not shot_path:
        return ""
    try:
        import ollama
        response = ollama.chat(
            model=model_name,
            messages=[{
                "role": "user",
                "content": (
                    f"The browser automation stalled at: '{step_name}'. "
                    "In 2-3 sentences describe what you see on this page. "
                    "Then on a new line starting with 'SUGGESTION:' say what "
                    "the automation should do to recover. Be specific."
                ),
                "images": [str(shot_path.resolve())],
            }],
        )
        diagnosis = ""
        if hasattr(response, "message") and hasattr(response.message, "content"):
            diagnosis = str(response.message.content or "").strip()
        elif isinstance(response, dict):
            msg = response.get("message", {})
            diagnosis = str(msg.get("content", "")).strip() if isinstance(msg, dict) else ""
        if diagnosis:
            logger.info(f"[AI @ '{step_name}']: {diagnosis}")
        return diagnosis
    except Exception as e:
        logger.debug(f"AI diagnosis unavailable: {e}")
        return ""


def _ai_suggests_continue(diagnosis: str) -> bool:
    if not diagnosis:
        return False
    lower = diagnosis.lower()
    return any(h in lower for h in [
        "attendance", "table", "period", "subject", "percentage",
        "looks correct", "continue", "proceed", "data is visible",
    ])


def _ai_suggests_login_needed(diagnosis: str) -> bool:
    if not diagnosis:
        return False
    lower = diagnosis.lower()
    return any(h in lower for h in [
        "login", "sign in", "username", "password", "credential",
        "session expired", "not logged in", "authentication",
    ])


# ─────────────────────────────────────────────────────────────────────────────
# Cookie popup dismissal
# ─────────────────────────────────────────────────────────────────────────────

def dismiss_cookie_popups(page: Optional[Page], custom_selector: Optional[str] = None) -> None:
    if not page:
        return
    try:
        cookie_loc = (
            page.locator("#onetrust-accept-btn-handler")
            .or_(page.locator(".cookie-accept-btn"))
            .or_(page.locator("#acceptCookie"))
            .or_(page.locator(".acceptcookies"))
            .or_(page.get_by_role("button", name=re.compile(
                r"accept all|accept cookies|accept|agree|i agree|allow all|got it|ok", re.I
            )))
            .or_(page.get_by_role("link", name=re.compile(
                r"accept all|accept cookies|accept|i agree", re.I
            )))
        )
        if custom_selector and "," not in custom_selector:
            cookie_loc = cookie_loc.or_(page.locator(custom_selector))
        if cookie_loc.first.is_visible(timeout=500):
            logger.info("Dismissing cookie banner...")
            cookie_loc.first.click(timeout=1000)
            page.wait_for_timeout(300)
    except Exception as e:
        logger.debug(f"Cookie banner: {e}")
    try:
        page.evaluate("""() => {
            ['#onetrust-banner-sdk','#onetrust-consent-sdk','.onetrust-pc-dark-filter',
             '[id*="cookie" i]','[class*="cookie" i]','.cookie-banner',
             '.cookieNotice','#cookieNotice','.cookieModal'].forEach(sel => {
                document.querySelectorAll(sel).forEach(el => {
                    if (!el.querySelector('input[type="password"]')) {
                        el.style.display='none'; el.remove();
                    }
                });
            });
        }""")
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Scroll helpers
# ─────────────────────────────────────────────────────────────────────────────

def _scroll_page(page: Page, clicked_in_iframe: bool) -> None:
    """Scrolls to bottom and back to top to trigger lazy-loaded rows."""
    try:
        if clicked_in_iframe and len(page.frames) > 1:
            inner = page.frames[1]
            inner.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(800)
            inner.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(400)
            return
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(800)
        page.evaluate("window.scrollTo(0, 0)")
        page.wait_for_timeout(400)
    except Exception as e:
        logger.debug(f"Scroll: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Login helper
# ─────────────────────────────────────────────────────────────────────────────

def _perform_login(
    page: Page,
    username: str,
    password: str,
    selectors: Dict[str, Any],
    shots_dir: Path,
    timeout_ms: int,
    vision_model: str,
) -> None:
    """Fills and submits the login form, waits for dashboard confirmation."""
    cookie_sel = selectors.get("cookies", {}).get("accept_button")
    user_sel = selectors.get("login", {}).get("username_field")
    pass_sel = selectors.get("login", {}).get("password_field")
    submit_sel = selectors.get("login", {}).get("submit_button")
    logged_in_sel = selectors.get("session", {}).get("logged_in_indicator", "")
    captcha_sel = selectors.get("verification", {}).get("captcha_or_otp_indicator")

    dismiss_cookie_popups(page, cookie_sel)

    logger.info("Waiting for login form...")
    page.wait_for_selector(user_sel, state="visible", timeout=timeout_ms)
    page.fill(user_sel, username)
    page.wait_for_selector(pass_sel, state="visible", timeout=timeout_ms)
    page.fill(pass_sel, password)
    logger.info("Credentials filled.")

    dismiss_cookie_popups(page, cookie_sel)

    submitted = False
    try:
        visible_submit = page.locator(submit_sel).locator("visible=true").first
        if visible_submit.count() > 0:
            logger.info("Clicking submit button...")
            visible_submit.click(timeout=5000)
            submitted = True
    except Exception as e:
        logger.debug(f"Submit click: {e}")

    if not submitted:
        logger.info("Pressing Enter on password field...")
        try:
            page.locator(pass_sel).press("Enter", timeout=5000)
        except Exception:
            page.keyboard.press("Enter")

    if captcha_sel:
        try:
            page.wait_for_selector(captcha_sel, timeout=4000, state="visible")
            logger.warning("CAPTCHA/OTP detected — waiting for manual completion...")
            print("\n=======================================================")
            print("Complete the verification in the browser, then press Enter")
            print("=======================================================\n")
            input()
        except PlaywrightTimeoutError:
            pass

    logger.info("Waiting for dashboard after login...")
    session_loc = (
        page.locator(logged_in_sel)
        .or_(page.get_by_text(re.compile(r"student\s+dashboard", re.I)))
        .or_(page.get_by_text(re.compile(r"attendance\s+analysis", re.I)))
        .or_(page.get_by_text(re.compile(r"self\s+service", re.I)))
        .first
    )
    try:
        session_loc.wait_for(state="visible", timeout=timeout_ms)
        logger.info("Dashboard element visible — login successful.")
        return
    except PlaywrightTimeoutError:
        pass

    if "SelfServices" in page.url or "home" in page.url:
        logger.info(f"Dashboard URL confirmed: {page.url}")
        return

    page.wait_for_timeout(3000)
    if "SelfServices" in page.url or "home" in page.url:
        logger.info(f"Dashboard URL confirmed (delayed): {page.url}")
        return

    diagnosis = _ai_diagnose_page(page, shots_dir, "post_login_check", model_name=vision_model)
    if _ai_suggests_continue(diagnosis):
        logger.info("AI says dashboard is loaded — continuing.")
        return

    raise RuntimeError(
        f"Could not confirm successful login. URL: {page.url}. "
        f"AI: {diagnosis or 'unavailable'}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Main automation entry point
# ─────────────────────────────────────────────────────────────────────────────

def run_portal_automation(
    config: Dict[str, Any],
    portal_url: str,
    username: str,
    password: str,
    headless: bool = False,
    relogin: bool = False,      # kept for API compat; ignored — always fresh login
    base_dir: Optional[Path] = None,
    on_page_ready: Optional[Callable[[Page], Any]] = None,
) -> Tuple[Path, Any]:
    """
    Runs the full browser workflow:
      1. Fresh browser context — no saved session, always logs in.
      2. Logs in with credentials.
      3. Navigates to 'Period-wise Attendance'.
      4. Waits for attendance content across all frames.
      5. Calls on_page_ready(page) while the browser is still open — this is
         where the DOM scraper runs to read data directly off the live page.
      6. Scrolls for lazy-loaded rows, captures screenshot, closes browser.

    Returns (screenshot_path, on_page_ready_result).
    If on_page_ready is None, the second element is None.
    """
    if base_dir is None:
        base_dir = Path.cwd()

    shots_dir = base_dir / config.get("paths", {}).get("shots_dir", "shots")
    shots_dir.mkdir(parents=True, exist_ok=True)

    timeout_ms = config.get("browser", {}).get("timeout_ms", 30000)
    selectors = config.get("selectors", {})
    vision_model = config.get("vision", {}).get("model_name", "moondream")
    cookie_sel = selectors.get("cookies", {}).get("accept_button")

    current_step = "Initializing Browser"
    page: Optional[Page] = None

    with sync_playwright() as playwright:
        try:
            logger.info(f"Launching Chromium (headless={headless})...")
            browser: Browser = playwright.chromium.launch(
                headless=headless,
                args=["--start-maximized"] if not headless else [],
            )

            # Always fresh — no saved cookies or storage state
            context: BrowserContext = browser.new_context(
                viewport=None if not headless else config.get("browser", {}).get(
                    "viewport", {"width": 1280, "height": 800}
                ),
                no_viewport=not headless,
            )
            context.on("page", lambda new_p: dismiss_cookie_popups(new_p, cookie_sel))

            page = context.new_page()
            page.set_default_timeout(timeout_ms)

            # ── STEP 1: Open portal ───────────────────────────────────────────
            current_step = "Opening Portal URL"
            logger.info(f"Going to {portal_url}")
            page.goto(portal_url, wait_until="load")
            dismiss_cookie_popups(page, cookie_sel)

            # ── STEP 2: Login (always fresh) ──────────────────────────────────
            current_step = "Logging In"
            logger.info("Starting fresh login...")
            try:
                _perform_login(
                    page=page, username=username, password=password,
                    selectors=selectors, shots_dir=shots_dir,
                    timeout_ms=timeout_ms, vision_model=vision_model,
                )
            except Exception as login_err:
                logger.warning(f"Login raised: {login_err}")
                diagnosis = _ai_diagnose_page(page, shots_dir, "login_failure", model_name=vision_model)
                if _ai_suggests_continue(diagnosis):
                    logger.info("AI sees dashboard — login succeeded silently.")
                elif _ai_suggests_login_needed(diagnosis):
                    logger.info("AI sees login page — retrying once...")
                    page.goto(portal_url, wait_until="load")
                    dismiss_cookie_popups(page, cookie_sel)
                    _perform_login(
                        page=page, username=username, password=password,
                        selectors=selectors, shots_dir=shots_dir,
                        timeout_ms=timeout_ms, vision_model=vision_model,
                    )
                else:
                    raise RuntimeError(
                        f"Login failed. AI: {diagnosis or 'unavailable'}"
                    ) from login_err

            logger.info("Logged in. On dashboard.")

            # ── STEP 3: Navigate to Period-wise Attendance ────────────────────
            current_step = "Navigating to Period-Wise Attendance"

            try:
                close_btn = (
                    page.locator(".notification-close")
                    .or_(page.locator(".modal-close"))
                    .or_(page.locator("button.close"))
                    .or_(page.get_by_text("✖"))
                    .first
                )
                if close_btn.is_visible(timeout=1000):
                    close_btn.click(timeout=1000)
            except Exception:
                pass

            attendance_pattern = re.compile(r"period[\s-]*wise\s+attendance", re.I)

            main_link_loc = (
                page.get_by_text(attendance_pattern)
                .or_(page.get_by_role("link", name=attendance_pattern))
                .or_(page.get_by_role("button", name=attendance_pattern))
                .or_(page.locator("a[title*='Attendance' i]"))
                .or_(page.locator("[id*='periodWise' i]"))
                .first
            )

            clicked_in_iframe = False
            target_to_click = None

            logger.info("Waiting for 'Period-wise Attendance' link...")
            try:
                main_link_loc.wait_for(state="visible", timeout=20000)
                target_to_click = main_link_loc
                logger.info("Found link on main page.")
            except PlaywrightTimeoutError:
                logger.info("Not on main page — checking iframes...")
                try:
                    iframe = page.frame_locator("iframe").first
                    iframe_link = (
                        iframe.get_by_text(attendance_pattern)
                        .or_(iframe.get_by_role("link", name=attendance_pattern))
                        .or_(iframe.get_by_role("button", name=attendance_pattern))
                        .first
                    )
                    iframe_link.wait_for(state="visible", timeout=8000)
                    target_to_click = iframe_link
                    clicked_in_iframe = True
                    logger.info("Found link inside iframe.")
                except Exception as e:
                    logger.debug(f"Iframe search: {e}")

            if not target_to_click:
                nav_cfg = selectors.get("navigation", {})
                for step_info in nav_cfg.get("menu_steps", []):
                    sname = step_info.get("name", "step")
                    sel = step_info.get("selector", "")
                    try:
                        loc = page.locator(sel).first
                        if loc.is_visible(timeout=3000):
                            loc.click(timeout=3000)
                            page.wait_for_load_state("domcontentloaded")
                    except Exception:
                        pass

            if not target_to_click:
                diagnosis = _ai_diagnose_page(
                    page, shots_dir, "finding_attendance_link", model_name=vision_model
                )
                if not _ai_suggests_continue(diagnosis):
                    raise RuntimeError(
                        f"Cannot find 'Period-wise Attendance' link. "
                        f"AI: {diagnosis or 'unavailable'}"
                    )
                logger.info("AI says we may already be on the attendance page.")

            if target_to_click:
                try:
                    target_to_click.scroll_into_view_if_needed(timeout=3000)
                except Exception:
                    pass

                box = None
                try:
                    box = target_to_click.bounding_box()
                    if box:
                        cx = box["x"] + box["width"] / 2.0
                        cy = box["y"] + box["height"] / 2.0
                        logger.info(f"Cursor → ({int(cx)}, {int(cy)})")
                        page.mouse.move(cx, cy, steps=10)
                        page.wait_for_timeout(100)
                except Exception:
                    pass

                try:
                    target_to_click.hover(timeout=2000)
                    page.wait_for_timeout(100)
                except Exception:
                    pass

                logger.info("Clicking 'Period-wise Attendance'...")
                try:
                    with context.expect_page(timeout=5000) as new_page_info:
                        try:
                            target_to_click.click(delay=120, timeout=4000)
                        except Exception:
                            if box:
                                page.mouse.down()
                                page.wait_for_timeout(100)
                                page.mouse.up()
                            else:
                                target_to_click.dispatch_event("click")
                    page = new_page_info.value
                    page.wait_for_load_state("load")
                    dismiss_cookie_popups(page, cookie_sel)
                    clicked_in_iframe = False
                    logger.info(f"Opened in new tab: {page.url}")
                except PlaywrightTimeoutError:
                    logger.info("Stayed on current page.")
                    page.wait_for_load_state("domcontentloaded")
                    dismiss_cookie_popups(page, cookie_sel)

            # ── STEP 4: Wait for attendance table ─────────────────────────────
            current_step = "Waiting for Attendance Table"
            logger.info("Waiting for attendance content...")

            table_css = (
                "table, .attendance-table, .period-attendance, "
                ".attendanceTable, [class*='attend' i]"
            )
            found_content = False

            try:
                page.locator(table_css).first.wait_for(state="visible", timeout=15000)
                found_content = True
                logger.info("Attendance content on main page.")
            except PlaywrightTimeoutError:
                pass

            if not found_content:
                all_frames = page.frames
                logger.info(f"Scanning {len(all_frames)} frame(s)...")
                for idx, frame in enumerate(all_frames):
                    if frame == page.main_frame:
                        continue
                    try:
                        frame.locator(table_css).first.wait_for(state="visible", timeout=12000)
                        found_content = True
                        logger.info(f"Content in frame[{idx}]: {frame.url}")
                        break
                    except PlaywrightTimeoutError:
                        logger.debug(f"No content in frame[{idx}] ({frame.url})")
                    except Exception as e:
                        logger.debug(f"Frame[{idx}] error: {e}")

            if not found_content:
                try:
                    page.frame_locator("iframe").first.locator(table_css).first.wait_for(
                        state="visible", timeout=10000
                    )
                    found_content = True
                    logger.info("Content found via frame_locator.")
                except Exception as e:
                    logger.debug(f"frame_locator: {e}")

            if not found_content:
                diagnosis = _ai_diagnose_page(
                    page, shots_dir, "waiting_for_table", model_name=vision_model
                )
                if _ai_suggests_continue(diagnosis):
                    logger.info("AI confirms attendance data visible.")
                    found_content = True
                else:
                    logger.warning(
                        f"Table not confirmed. AI: {diagnosis or 'unavailable'}. "
                        "Proceeding anyway."
                    )
                    page.wait_for_timeout(3000)

            # ── STEP 5: DOM scrape while page is still live ───────────────────
            current_step = "Scraping Attendance Data"
            scrape_result = None
            if on_page_ready:
                try:
                    logger.info("Running DOM scraper on live page...")
                    scrape_result = on_page_ready(page)
                    if scrape_result:
                        logger.info("DOM scrape succeeded.")
                    else:
                        logger.warning("DOM scraper returned no data.")
                except Exception as e:
                    logger.warning(f"DOM scrape raised: {e}")

            # ── STEP 6: Scroll + screenshot ───────────────────────────────────
            current_step = "Scrolling Page"
            _scroll_page(page, clicked_in_iframe)

            current_step = "Capturing Screenshot"
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            screenshot_path = _capture_screenshot(
                page=page,
                shots_dir=shots_dir,
                timestamp=timestamp,
                clicked_in_iframe=clicked_in_iframe or not found_content,
            )
            logger.info(f"Screenshot: {screenshot_path.resolve()}")

            browser.close()
            return screenshot_path, scrape_result

        except Exception as exc:
            logger.error(f"Step '{current_step}' failed: {exc}")
            debug_shot = _save_debug_screenshot(page, shots_dir, current_step)
            msg = f"Automation failed at '{current_step}': {exc}"
            if debug_shot:
                msg += f"\nDebug screenshot: {debug_shot.resolve()}"
            raise BrowserAutomationError(
                step_name=current_step, message=msg, original_exc=exc
            ) from exc
