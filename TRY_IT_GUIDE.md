# 🧪 Quickstart & Testing Guide: attendance-agent

This guide walks you through verifying and running your `attendance-agent` step-by-step.

---

## ⚡ 1. Instant Sanity Check (No Browser Needed)

Verify the CLI is installed and responsive:

```powershell
attendance --help
```

You should see the command usage and available flags (`--headless`, `--relogin`, `--last`).

---

## 🎨 2. Preview the Rich UI (Demo Test)

To see how the terminal table and summary look right now, run the included demo script:

```powershell
python test_demo.py
```

This will print the Rich attendance card, subject table, and low-attendance alerts to your terminal.

---

## 🤖 3. Vision Model in Ollama

Your Ollama service is active. To use the recommended vision model:

```powershell
ollama pull qwen2.5vl
```

*(Alternative: You also have `moondream` installed, which can be configured in [`config.yaml`](file:///d:/_3rd%20Year/Attendance_Monitor/attendance-agent/config.yaml) under `vision.model_name: "moondream"`).*

---

## 🔑 4. Set Your Credentials

Open [`.env`](file:///d:/_3rd%20Year/Attendance_Monitor/attendance-agent/.env) and enter your login details:

```env
PORTAL_URL=https://your-institution-tcs-url.com
PORTAL_USERNAME=your_username
PORTAL_PASSWORD=your_password
```

*(If credentials are missing or still placeholder values, `attendance` will show a friendly alert without printing any password).*

---

## 🌐 5. Run the Full Workflow

### Step A: First Run (Headed mode — Recommended)
```powershell
attendance
```
- A Chromium window opens so you can see it navigate to your portal.
- **Cookie Consent:** Any cookie popups or banners covering the submit button are automatically dismissed and cleared.
- It enters your username and password and logs in.
- **OTP / CAPTCHA:** If your portal shows a verification or OTP modal, the terminal will pause with:
  ```text
  Complete the verification in the browser, then press Enter
  ```
  Simply complete it in the browser, switch back to the terminal, and hit `Enter`!
- The session is saved to `auth_state.json`. Future runs will skip login automatically!
- The agent clicks to `Period wise attendance`, scrolls down to lazy-load attendance rows, and captures a full-page screenshot into `shots/`.
- Ollama parses the statistics and prints the summary.
- The structured report is saved to `out/attendance_<timestamp>.json`.

### Step B: View Previous Report
At any time, display your last saved summary instantly:
```powershell
attendance --last
```

### Step C: Background / Headless Run
Once your selectors in [`config.yaml`](file:///d:/_3rd%20Year/Attendance_Monitor/attendance-agent/config.yaml) are confirmed:
```powershell
attendance --headless
```

---

## 🛠️ Troubleshooting & Logs

- **Failed step?** If any selector fails to load, the script captures an error screenshot to `shots/error_<step>_<timestamp>.png` so you can see exactly where it paused.
- **Detailed logs:** Check [`logs/attendance.log`](file:///d:/_3rd%20Year/Attendance_Monitor/attendance-agent/logs/attendance.log) for debug timestamps and step traces.
