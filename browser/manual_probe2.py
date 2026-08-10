import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

MSEDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PROFILE = r"C:\Users\z\Desktop\project\qiongguicode\edge_manual_profile2"
PORT = 9224
OUT = Path(__file__).resolve().parent / "manual_jd_profile2.png"

# 1. 全新 profile + 真实 Edge + 远程调试端口（无任何自动化伪装）
args = [
    MSEDGE,
    f"--user-data-dir={PROFILE}",
    f"--remote-debugging-port={PORT}",
    "--start-maximized",
    "https://www.jd.com/",
]
proc = subprocess.Popen(args)

# 2. 等待调试端点就绪并找到 jd 目标
target = None
targets = []
for _ in range(30):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json", timeout=2) as r:
            targets = json.load(r)
        for t in targets:
            if "jd.com" in t.get("url", ""):
                target = t
                break
        if target:
            break
    except Exception:
        pass
    time.sleep(1)

print("found jd target:", bool(target))
if not target:
    print("targets:", [t.get("url") for t in targets] if targets else "none")
    proc.terminate()
    raise SystemExit(1)

print("URL:", target["url"])
time.sleep(8)

# 3. 通过 CDP 读取正文 + 截图（只读，不触发页面重检）
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    conn = p.chromium.connect_over_cdp(f"http://127.0.0.1:{PORT}")
    ctx = conn.contexts[0]
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    page.bring_to_front()
    page.wait_for_timeout(2000)
    body = page.evaluate("() => document.body ? document.body.innerText.slice(0,300) : '(no body)'")
    print("BODY:", body)
    page.screenshot(path=str(OUT), full_page=False)
    print("SCREENSHOT:", OUT, OUT.stat().st_size, "bytes")
    conn.close()

proc.terminate()
