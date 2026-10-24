# attendance-agent 📊

An automated Python CLI agent that logs into your TCS iON portal, navigates to the **Period wise attendance** page, captures a full-page screenshot, and uses a local Ollama vision model (such as `qwen2.5vl` or `llava`) to extract and summarize your attendance statistics with a clean terminal UI powered by Rich.

---

## 📁 Project Structure

```text
attendance-agent/
├── .env                 # Your portal credentials (NEVER commit this)
├── .env.example         # Template with variable names
├── .gitignore           # Ignores credentials, screenshots, logs, and session files
├── config.yaml          # Portal URL, CSS selectors, model name, screenshot settings
├── pyproject.toml       # Defines the `attendance` global console command
├── agent/
│   ├── __init__.py
│   ├── cli.py           # Typer CLI application & Rich output formatter
│   ├── browser.py       # Playwright browser automation (session persistence, lazy scroll, screenshot)
│   ├── vision.py        # Pillow image shrinking & Ollama vision model API integration
│   └── models.py        # Pydantic AttendanceSummary and SubjectAttendance schemas
├── shots/               # Full-page screenshots and debug error captures
├── logs/                # Loguru log files (attendance.log)
└── out/                 # Saved attendance summary JSON outputs
```

---

## 🚀 Quick Setup & Installation

### 1. Install the CLI in Editable Mode

In your terminal, navigate into the `attendance-agent` folder and run:

```bash
cd "d:\_3rd Year\Attendance_Monitor\attendance-agent"
pip install -e .
```

This installs the package in editable mode and registers the `attendance` command globally in your Python environment.

### 2. Install Playwright Browsers (if not already installed)

```bash
playwright install chromium
```

### 3. Ensure Ollama & Vision Model are Ready

Ensure Ollama is running and download your preferred vision model:

```bash
# Recommended model (fast and strong document/table OCR):
ollama pull qwen2.5vl

# Or alternative vision model:
ollama pull llava
```

Ensure the model name in [`config.yaml`](file:///d:/_3rd%20Year/Attendance_Monitor/attendance-agent/config.yaml) matches your downloaded model:
```yaml
vision:
  model_name: "qwen2.5vl"
```

---

## 🔐 Credentials Configuration (.env)

Open `.env` and fill in your actual TCS portal login details:

```env
PORTAL_URL=https://www.tcsion.com/dotcom/TCSSecure/login.html
PORTAL_USERNAME=your_tcs_username
PORTAL_PASSWORD=your_tcs_password
```

> **Security Note:** `.env` and `auth_state.json` are listed in `.gitignore` so your login credentials and saved browser cookies will never be committed to git.

---

## 🔍 How to Find Your CSS Selectors for `config.yaml`

Because different universities and companies use customized TCS iON portal templates, the default selectors in `config.yaml` can be easily tailored to your specific portal:

1. Open your browser (Chrome or Edge) and navigate to your TCS portal login page.
2. Press `F12` or right-click on the element you want to inspect and select **Inspect**.
3. **Username field:** Right-click the `<input>` element → **Copy** → **Copy selector** (e.g., `#userId`, `input[name="userId"]`).
4. **Password field:** Repeat for the password `<input>` element (e.g., `#password`, `input[type="password"]`).
5. **Login button:** Repeat for the sign-in button (e.g., `#loginSubmit`, `button[type="submit"]`).
6. **Navigation / Menu path:**
   - Log in manually once and inspect the menu tab for **Attendance** or **Academic**.
   - Inspect the submenu link for **Period wise attendance**.
   - Playwright allows text matching: e.g. `text="Period wise attendance"` or standard CSS selectors.
7. Paste these selectors into [`config.yaml`](file:///d:/_3rd%20Year/Attendance_Monitor/attendance-agent/config.yaml).

---

## 💻 Commands & Usage

### 1. Standard Run (Default Headed Mode)
```bash
attendance
```
- Opens Chromium in a visible window.
- If a valid session is already saved in `auth_state.json`, it skips login!
- If the session expired or is missing, it logs in and saves the session.
- Handles OTP / CAPTCHA interactively if your institution prompts for one.
- Navigates to Period wise attendance, scrolls down to load lazy tables, and takes a full-page screenshot.
- Resizes the screenshot with Pillow and queries Ollama.
- Prints a Rich terminal summary and saves JSON to `out/attendance_<timestamp>.json`.

### 2. Headless Run (Background)
```bash
attendance --headless
```
Runs without opening a browser window (useful once your selectors are confirmed working).

### 3. Force Re-Login
```bash
attendance --relogin
```
Ignores any saved `auth_state.json` and performs a fresh login.

### 4. View Last Saved Summary
```bash
attendance --last
```
Instantly displays the most recently saved attendance summary in your terminal without opening a browser or calling Ollama.

---

## 🛡️ Error Handling & Troubleshooting

- **Step Failures:** If any step fails during browser navigation, an error screenshot is automatically captured into `shots/error_<step>_<timestamp>.png` to help you identify what went wrong.
- **Logs:** Full execution traces and debug details are recorded in `logs/attendance.log`.
- **Parsing Fallback:** If the vision model produces conversational text instead of strict JSON, the agent retries once with a correction prompt. If that still fails, it outputs the raw response safely so no information is lost.
