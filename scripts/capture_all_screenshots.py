import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.edge.options import Options
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

BASE_DIR = Path(__file__).parent.parent
IMG_DIR = BASE_DIR / "docs" / "images"
IMG_DIR.mkdir(parents=True, exist_ok=True)

ARTIFACT_IMG_DIR = Path(
    r"C:\Users\GANGARI DHRUVAVEER\.gemini\antigravity\brain\6ae323a1-2e0a-4bd8-b854-9f56beb06fdc\images"
)
ARTIFACT_IMG_DIR.mkdir(parents=True, exist_ok=True)

print("Starting web_app.py server...")
proc = subprocess.Popen(
    [sys.executable, str(BASE_DIR / "web_app.py")],
    cwd=str(BASE_DIR),
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)

time.sleep(3)

options = Options()
options.add_argument("--headless=new")
options.add_argument("--window-size=1440,1100")
options.add_argument("--disable-gpu")
options.add_argument("--no-sandbox")

driver = webdriver.Edge(options=options)
wait = WebDriverWait(driver, 45)

try:
    print("Navigating to dashboard...")
    driver.get("http://127.0.0.1:8080")
    
    # Wait until scenario buttons are loaded
    wait.until(EC.presence_of_element_located((By.XPATH, "//button[contains(., 'Story 1')]")))
    time.sleep(1)

    # 1. Dashboard Overview
    p1 = IMG_DIR / "01_dashboard_overview.png"
    driver.save_screenshot(str(p1))
    print(f"Captured: {p1.name} ({p1.stat().st_size} bytes)")

    # 2. While a process is being done (Live spinner & querying MCP)
    btn_run = driver.find_element(By.ID, "btn-run")
    btn_run.click()
    
    # Capture immediately while running!
    time.sleep(0.5)
    p2 = IMG_DIR / "02_process_in_progress.png"
    driver.save_screenshot(str(p2))
    print(f"Captured: {p2.name} (while process running!) ({p2.stat().st_size} bytes)")

    # 3. Wait for investigation to finish (button re-enables and contains 'Investigate')
    print("Waiting for triage investigation to complete...")
    wait.until(lambda d: d.find_element(By.ID, "btn-run").is_enabled() and "Investigate" in d.find_element(By.ID, "btn-run").text)
    time.sleep(2)
    p3 = IMG_DIR / "03_triage_completed_trace.png"
    driver.save_screenshot(str(p3))
    print(f"Captured: {p3.name} ({p3.stat().st_size} bytes)")

    # 4. Run Story 5 (Prompt Injection Cyberattack)
    print("Running Story 5 (Cyberattack)...")
    story5_btn = driver.find_element(By.XPATH, "//button[contains(., 'Story 5')]")
    story5_btn.click()
    time.sleep(1)
    btn_run = driver.find_element(By.ID, "btn-run")
    btn_run.click()
    time.sleep(1)
    wait.until(lambda d: d.find_element(By.ID, "btn-run").is_enabled() and "Investigate" in d.find_element(By.ID, "btn-run").text)
    time.sleep(2)
    p4 = IMG_DIR / "04_hacker_attack_quarantined.png"
    driver.save_screenshot(str(p4))
    print(f"Captured: {p4.name} ({p4.stat().st_size} bytes)")

    # 5. Manager Review Queue Tab
    print("Switching to Manager Review Queue Tab...")
    driver.execute_script("switchTab('queue');")
    time.sleep(2)
    p5 = IMG_DIR / "05_review_queue.png"
    driver.save_screenshot(str(p5))
    print(f"Captured: {p5.name} ({p5.stat().st_size} bytes)")

    # 6. Security Diary (Audit Log) Tab
    print("Switching to Security Audit Diary Tab...")
    driver.execute_script("switchTab('audit');")
    time.sleep(2)
    p6 = IMG_DIR / "06_audit_log.png"
    driver.save_screenshot(str(p6))
    print(f"Captured: {p6.name} ({p6.stat().st_size} bytes)")

    # 7. MCP Tools Registry Tab
    print("Switching to MCP Tools Registry Tab...")
    driver.execute_script("switchTab('tools');")
    time.sleep(2)
    p7 = IMG_DIR / "07_mcp_tools.png"
    driver.save_screenshot(str(p7))
    print(f"Captured: {p7.name} ({p7.stat().st_size} bytes)")

    # Copy all images to artifact dir
    for f in IMG_DIR.glob("*.png"):
        shutil.copy(f, ARTIFACT_IMG_DIR / f.name)
    print(f"Copied all screenshots to {ARTIFACT_IMG_DIR}")

finally:
    driver.quit()
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except Exception:
        proc.kill()
    print("Cleanup complete!")
