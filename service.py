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


def open_log():
    path = os.path.join(LOGS, "server.log")
    if os.path.exists(path) and os.path.getsize(path) > 10 * 2**20:  # 超過 10MB 就換新檔
        os.replace(path, path + ".1")
    return open(path, "a", encoding="utf-8")


def main():
    os.makedirs(LOGS, exist_ok=True)
    log(f"N.O.V.A. 服務啟動（port {PORT}）")
    delay = 5
    while True:
        # 已經有核心在跑（例如手動開了 start.bat）就不重複啟動，定時再檢查
        if up(f"http://127.0.0.1:{PORT}/api/status"):
            time.sleep(30)
            continue
        ensure_ollama()
        started = time.time()
        with open_log() as out:
            p = subprocess.Popen([python_exe(), os.path.join(BASE, "server.py")], cwd=BASE, stdout=out,
                                 stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW)
            log(f"核心已啟動（PID {p.pid}）")
            code = p.wait()
        # 很快就結束代表持續出錯：等待時間逐步拉長，避免狂重啟
        delay = 5 if time.time() - started > 120 else min(delay * 2, 300)
        log(f"核心結束（代碼 {code}），{delay} 秒後重新啟動")
        time.sleep(delay)


if __name__ == "__main__":
    main()
