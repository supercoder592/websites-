"""N.O.V.A. 常駐服務：登入後自動在背景啟動 AI 核心，當掉會自動重啟。

由 install-autostart.bat（Windows）或 install-autostart.sh（macOS / Linux）設定開機自動執行。
也可以手動執行：venv\\Scripts\\pythonw.exe service.py（Windows，不會開視窗）
紀錄檔在 logs/ 資料夾。
"""
import os
import shutil
import subprocess
import sys
import time
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
LOGS = os.path.join(BASE, "logs")
PORT = int(os.environ.get("NOVA_PORT", "7860"))
WIN = os.name == "nt"
NO_WINDOW = 0x08000000 if WIN else 0  # CREATE_NO_WINDOW：背景執行不跳出黑色視窗


def python_exe():
    # pythonw 沒有 stdout，伺服器改用同目錄的 python（輸出導到紀錄檔）
    exe = sys.executable
    if WIN and exe.lower().endswith("pythonw.exe"):
        exe = exe[:-len("pythonw.exe")] + "python.exe"
    return exe


def log(msg):
    os.makedirs(LOGS, exist_ok=True)
    with open(os.path.join(LOGS, "service.log"), "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}\n")


def up(url, timeout=3):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def ensure_ollama():
    """Ollama 沒在跑就叫起來（Windows / Mac 的 Ollama 應用程式通常本來就會開機啟動）。"""
    if up("http://127.0.0.1:11434/api/tags") or not shutil.which("ollama"):
        return
    log("Ollama 未執行，啟動 ollama serve")
    subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     stdin=subprocess.DEVNULL, creationflags=NO_WINDOW, start_new_session=not WIN)


# ---------------------------------------------------------------- 遠端通道（Cloudflare quick tunnel）
def cloudflared_exe():
    local = os.path.join(BASE, "tools", "cloudflared.exe" if WIN else "cloudflared")
    return local if os.path.exists(local) else shutil.which("cloudflared")


def gh(*args):
    r = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8",
                       creationflags=NO_WINDOW, timeout=60)
    return r.returncode, r.stdout.strip()


def publish_url(url):
    """把最新的通道網址寫到 GitHub repo 的 core 分支（core.json），GitHub Pages 上的介面會自動讀取。
    網址本身公開沒關係：沒有存取金鑰什麼都做不了。"""
    import base64
    import json
    if not shutil.which("gh"):
        log("找不到 gh（GitHub CLI），無法自動發布通道網址")
        return
    try:
        origin = subprocess.run(["git", "remote", "get-url", "origin"], cwd=BASE, capture_output=True, text=True,
                                creationflags=NO_WINDOW).stdout.strip()
        repo = origin.rstrip("/").removesuffix(".git").split("github.com")[-1].strip(":/")
        if gh("api", f"repos/{repo}/git/ref/heads/core")[0] != 0:
            code, sha = gh("api", f"repos/{repo}/git/ref/heads/main", "--jq", ".object.sha")
            gh("api", "-X", "POST", f"repos/{repo}/git/refs", "-f", "ref=refs/heads/core", "-f", f"sha={sha}")
        code, old = gh("api", f"repos/{repo}/contents/core.json?ref=core", "--jq", ".sha")
        body = json.dumps({"url": url, "updated": time.strftime("%Y-%m-%dT%H:%M:%S%z")}, ensure_ascii=False)
        args = ["api", "-X", "PUT", f"repos/{repo}/contents/core.json", "-f", "message=更新 AI 核心通道網址",
                "-f", f"content={base64.b64encode(body.encode()).decode()}", "-f", "branch=core"]
        if code == 0 and old:
            args += ["-f", f"sha={old}"]
        code, out = gh(*args)
        log(f"已發布通道網址到 GitHub（{repo}@core）" if code == 0 else f"發布通道網址失敗：{out[:200]}")
    except Exception as e:
        log(f"發布通道網址失敗：{e}")


def tunnel_loop():
    """讓 cloudflared 一直開著；斷掉就重開，網址變了就重新發布。設 NOVA_TUNNEL=0 可關閉。"""
    import re
    import threading
    exe = cloudflared_exe()
    if not exe or os.environ.get("NOVA_TUNNEL", "1") == "0":
        return
    pat = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

    def run():
        last = None
        while True:
            p = subprocess.Popen([exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{PORT}"],
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                 text=True, encoding="utf-8", errors="ignore", creationflags=NO_WINDOW)
            log(f"遠端通道啟動（PID {p.pid}）")
            for line in p.stdout:
                m = pat.search(line)
                if m and m.group(0) != last:
                    last = m.group(0)
                    with open(os.path.join(LOGS, "tunnel_url.txt"), "w", encoding="utf-8") as f:
                        f.write(last)
                    log(f"遠端通道網址：{last}")
                    publish_url(last)
            log(f"遠端通道結束（代碼 {p.wait()}），30 秒後重新連線")
            time.sleep(30)

    threading.Thread(target=run, daemon=True).start()


def open_log():
    path = os.path.join(LOGS, "server.log")
    if os.path.exists(path) and os.path.getsize(path) > 10 * 2**20:  # 超過 10MB 就換新檔
        os.replace(path, path + ".1")
    return open(path, "a", encoding="utf-8")


def main():
    os.makedirs(LOGS, exist_ok=True)
    log(f"N.O.V.A. 服務啟動（port {PORT}）")
    tunnel_loop()
    delay = 5
    while True:
        # 已經有核心在跑（例如手動開了 start.bat）就不重複啟動，定時再檢查
        if up(f"http://127.0.0.1:{PORT}/api/status"):
            time.sleep(30)
            continue
        ensure_ollama()
        started = time.time()
        # 常駐模式預設開放同一個 Wi-Fi 的手機 / 平板連線（http://電腦IP:7860）；只想本機用可設 NOVA_HOST=127.0.0.1
        env = dict(os.environ)
        env.setdefault("NOVA_HOST", "0.0.0.0")
        with open_log() as out:
            p = subprocess.Popen([python_exe(), os.path.join(BASE, "server.py")], cwd=BASE, stdout=out, env=env,
                                 stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
            log(f"核心已啟動（PID {p.pid}）")
            code = p.wait()
        # 很快就結束代表持續出錯：等待時間逐步拉長，避免狂重啟
        delay = 5 if time.time() - started > 120 else min(delay * 2, 300)
        log(f"核心結束（代碼 {code}），{delay} 秒後重新啟動")
        time.sleep(delay)


if __name__ == "__main__":
    main()
