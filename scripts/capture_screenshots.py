import os
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
IMG_DIR = BASE_DIR / "docs" / "images"
IMG_DIR.mkdir(parents=True, exist_ok=True)

EDGE_EXE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
if not os.path.exists(EDGE_EXE):
    EDGE_EXE = r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"

print(f"Using Edge at: {EDGE_EXE}")
print(f"Saving images to: {IMG_DIR}")

# 1. Start web_app.py
proc = subprocess.Popen(
    [sys.executable, str(BASE_DIR / "web_app.py")],
    cwd=str(BASE_DIR),
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
)

print("Starting web server...")
time.sleep(3)

def capture(url: str, filename: str, delay_sec: int = 1):
    time.sleep(delay_sec)
    out_path = IMG_DIR / filename
    cmd = [
        EDGE_EXE,
        "--headless=new",
        "--disable-gpu",
        "--window-size=1280,950",
        f"--screenshot={out_path}",
        url,
    ]
    subprocess.run(cmd, capture_output=True, text=True)
    if out_path.exists():
        print(f"Captured {filename} ({out_path.stat().st_size} bytes)")
    else:
        print(f"Failed to capture {filename}")

try:
    print("\n1. Capturing Dashboard Overview...")
    capture("http://127.0.0.1:8080", "01_dashboard_overview.png", delay_sec=2)

    print("\n2. Capturing Triage Live Trace (Story 1)...")
    capture("http://127.0.0.1:8080/?run=1", "02_triage_trace.png", delay_sec=4)

    print("\n3. Capturing Review Queue...")
    capture("http://127.0.0.1:8080/?tab=queue", "03_review_queue.png", delay_sec=2)

    print("\n4. Capturing Security Audit Log...")
    capture("http://127.0.0.1:8080/?tab=audit", "04_audit_log.png", delay_sec=2)

    print("\n5. Capturing MCP Tool Registry...")
    capture("http://127.0.0.1:8080/?tab=tools", "05_mcp_tools.png", delay_sec=2)

finally:
    print("\nShutting down web server...")
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except Exception:
        proc.kill()
    print("Done!")

