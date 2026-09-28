"""N.O.V.A. 專案模式：一句話 → 規劃 → 生成素材 → 寫遊戲程式 → 打包成單一離線 HTML。

整個流程是工作佇列裡的一個 kind="project" 任務（server.run_job 呼叫 run）。
輸出 outputs/<stamp>.html（手機 / 筆電都能直接離線開啟）與 outputs/<stamp>.project.json（之後「修正」用）。
LLM 提示詞一律用英文（4B 小模型比較聽得懂），遊戲裡的文字跟著使用者的語言。
"""
import base64
import html as htmllib
import json
import os
import pathlib
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
import traceback
import uuid

import httpx

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "outputs")
STATIC = os.path.join(BASE, "static")
VENDOR = os.path.join(STATIC, "vendor")
OLLAMA = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
OLLAMA = OLLAMA if OLLAMA.startswith("http") else f"http://{OLLAMA}"
DEFAULT_MODEL = os.environ.get("NOVA_MODEL", "")
NUM_CTX = 8192  # 和聊天用同一個 context 長度，Ollama 才不用重新載入模型
CODE_BUDGET = int(os.environ.get("NOVA_PROJECT_CODE_BUDGET", "2400"))  # 寫程式 + 修正最多花幾秒，超過就用範本
REPAIRS = 2  # 程式檢查失敗時最多請 LLM 修幾次
SMOKE = os.environ.get("NOVA_PROJECT_SMOKE", "1") != "0"  # 用無頭瀏覽器實際跑幾秒，抓執行期錯誤
NO_WINDOW = 0x08000000 if os.name == "nt" else 0  # 背景服務呼叫 node / 瀏覽器時不要跳出黑色視窗

CAPS = {"model3d": 2, "image": 3, "music": 1}  # 每種素材最多幾個（CPU 上 3D 一個就要好幾分鐘）
TYPE_ZH = {"model3d": "3D 模型", "image": "圖片", "music": "音樂"}
MIME = {"model3d": "model/gltf-binary", "image": "image/png", "music": "audio/wav"}
ROLES = ("player", "enemy", "background", "music", "prop")
WEIGHT = {"plan": 1.0, "model3d": 4.0, "image": 0.6, "music": 2.0, "code": 6.0, "assemble": 0.3}  # 進度條權重
ID_RE = re.compile(r"[0-9a-z-]{1,64}")
JS_RESERVED = set("""break case catch class const continue debugger default delete do else enum export extends false
finally for function if import in instanceof new null return super switch this throw true try typeof var void while
with yield let static await implements package protected interface private public arguments eval undefined NaN
Infinity THREE NovaKit window document""".split())

# 遊戲模組可以匯入的東西（打包時 importmap 會對應到內嵌的檔案）
MODULES = {
    "three": "three.module.js",
    "three/addons/loaders/GLTFLoader.js": "addons/loaders/GLTFLoader.js",
    "three/addons/utils/BufferGeometryUtils.js": "addons/utils/BufferGeometryUtils.js",
    "three/addons/controls/OrbitControls.js": "addons/controls/OrbitControls.js",
}
# static/novakit.js 實際提供的成員（契約 C3 + 額外的小工具）；不在清單裡的就是 LLM 瞎掰的
NOVAKIT_API = {"init", "mode", "onModeChange", "input", "setButtons", "assets", "meta", "loadModel", "loadTexture",
               "createRenderer", "fit", "loop", "hud", "bar", "toast", "gameOver", "sfx", "playMusic", "random", "clamp",
               "version", "THREE", "storage", "sensitivity", "fps", "dialogOpen", "renderer", "scene", "camera",
               "setTitle", "setMode", "showHelp", "lockPointer", "stop", "dialog", "victory", "closeDialog", "findAsset",
               "assetNames", "hasAsset", "stopMusic", "setMuted", "muted", "randomInt", "pick", "lerp", "reportError",
               "pixelRatio", "autoRender", "gpu", "adaptive"}
INPUT_API = {"move", "look", "wheel", "buttons", "pressed", "released", "held", "key", "any", "pointerLocked"}


class Cancelled(RuntimeError):
    pass


def check_cancel(job):
    if job.get("cancel"):
        raise Cancelled("已取消")


# ---------------------------------------------------------------- 任務狀態
class Steps:
    """job["steps"] 即時清單。每次更新都換成新的 list（舊的不再改動），API 執行緒序列化時才不會撞到變動中的資料。"""

    def __init__(self, job):
        self.job, self.items = job, []

    def add(self, sid, label, before=None, **kw):
        item = {"id": sid, "label": label, "status": "pending", **kw}
        idx = next((i for i, s in enumerate(self.items) if s["id"] == before), len(self.items))
        self.items.insert(idx, item)
        self.publish()

    def set(self, sid, **kw):
        for s in self.items:
            if s["id"] == sid:
                for k, v in kw.items():
                    if v is None:
                        s.pop(k, None)
                    else:
                        s[k] = str(v)[:300] if k == "detail" else v
        self.publish()

    def publish(self):
        self.job["steps"] = [dict(s) for s in self.items]


class Progress:
    """整體進度 0..1：每個階段依預估耗時分一段區間。"""

    def __init__(self, job, phases):
        self.job, self.span, total, acc = job, {}, sum(w for _, w in phases) or 1, 0.0
        for pid, w in phases:
            self.span[pid] = (acc / total, (acc + w) / total)
            acc += w

    def at(self, pid, frac=0.0):
        lo, hi = self.span.get(pid, (0, 0))
        value = lo + (hi - lo) * max(0.0, min(1.0, float(frac)))
        self.job["progress"] = round(max(self.job.get("progress") or 0.0, min(0.99, value)), 4)


class SubJob(dict):
    """交給 engines 的任務物件：進度換算成整體進度、訊息顯示在步驟上、取消看主任務。"""

    def __init__(self, job, on_progress, on_message):
        super().__init__(id=job.get("id"), kind=job.get("kind"), progress=0.0, message="", warning=None, cancel=False)
        self._job, self._on_progress, self._on_message = job, on_progress, on_message

    def __setitem__(self, k, v):
        super().__setitem__(k, v)
        if k == "progress":
            self._on_progress(v)
        elif k == "message":
            self._on_message(v)

    def __getitem__(self, k):
        return self._job.get("cancel") if k == "cancel" else super().__getitem__(k)

    def get(self, k, default=None):
        return self._job.get("cancel") if k == "cancel" else super().get(k, default)


# ---------------------------------------------------------------- LLM
def pick_llm(requested=None):
    try:
        names = [m["name"] for m in httpx.get(f"{OLLAMA}/api/tags", timeout=3).json().get("models", [])]
    except Exception:
        return None
    for m in (requested, DEFAULT_MODEL):
        if m and m in names:
            return m
    return names[0] if names else None


def llm(job, model, prompt, system=None, fmt=None, num_predict=1024, temperature=0.3, deadline=600,
        on_text=None, stop=None):
    """串流呼叫 Ollama：隨時可以取消（連線直接關掉，Ollama 會停止生成）、有總時間上限；
    stop(text) 回傳 True 時提早結束（例如程式碼區塊已經寫完，後面的廢話不用等）。"""
    body = {"model": model, "prompt": prompt, "stream": True,
            "options": {"temperature": temperature, "num_ctx": NUM_CTX, "num_predict": num_predict}}
    if system:
        body["system"] = system
    if fmt:
        body["format"] = fmt
    q = queue.Queue()
    client = httpx.Client(timeout=httpx.Timeout(connect=5, read=300, write=60, pool=5))

    def reader():
        try:
            with client.stream("POST", f"{OLLAMA}/api/generate", json=body) as r:
                if r.status_code != 200:
                    q.put(("error", f"Ollama HTTP {r.status_code}: {r.read().decode(errors='ignore')[:200]}"))
                    return
                for line in r.iter_lines():
                    if line.strip():
                        q.put(("line", line))
            q.put(("end", None))
        except Exception as e:
            q.put(("error", f"{type(e).__name__}: {str(e)[:200]}"))

    threading.Thread(target=reader, daemon=True).start()
    text, t0 = "", time.time()
    try:
        while True:
            check_cancel(job)
            if time.time() - t0 > deadline:
                raise TimeoutError(f"LLM 超過 {deadline:.0f} 秒沒有完成")
            try:
                kind, val = q.get(timeout=0.5)
            except queue.Empty:
                continue
            if kind == "error":
                raise RuntimeError(val)
            if kind == "end":
                break
            try:
                chunk = json.loads(val)
            except ValueError:
                continue
            if chunk.get("error"):
                raise RuntimeError(f"Ollama: {str(chunk['error'])[:200]}")
            if chunk.get("response"):
                text += chunk["response"]
                if on_text:
                    on_text(text)
                if stop and stop(text):
                    break
            if chunk.get("done"):
                break
    finally:
        client.close()
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


# ---------------------------------------------------------------- 規劃
ASK_RE = {
    "model3d": re.compile(r"(建模|模型|3d\s*(模|建|角色|人物|物件|機體|機甲|素材)|\bglb\b|\b3d\s*(models?|assets?)\b|"
                          r"\bmodel(l)?ing\b)", re.I),
    "image": re.compile(r"(插圖|插畫|貼圖|圖片|圖像|背景圖|海報|封面|桌布|[一兩二三四五\d]\s*[張幅]|"
                        r"(?<![計規企動刻策漫])[畫繪](?![面質家風])|"
                        r"\b(images?|pictures?|textures?|illustrations?|artworks?|wallpapers?)\b|background (art|image))",
                        re.I),
    "music": re.compile(r"(音樂|配樂|背景音|bgm|主題曲|歌曲|旋律|\bmusic\b|soundtrack)", re.I),
}
ZH_NUM = {"一": 1, "單": 1, "兩": 2, "二": 2, "三": 3, "四": 4, "五": 5}
SEQ_WORDS = re.compile(r"(首先|第一步|先|再來|再|然後|接著|最後|之後|匯出|輸出|打包|寫遊戲程式|寫程式|程式|html|網頁版?|"
                       r"要有|手機跟筆電模式|手機|筆電|模式|幫我|請|做一個|做一款|製作|一個|一款|3d\s*建模|建模)", re.I)


# 翻譯模型不在時也要認得的幾個題材（其他交給 LLM / server 的詞彙表）
STYLE_EN = {"福音戰士": "Evangelion anime style", "鋼彈": "Gundam anime style",
            "賽博龐克": "cyberpunk", "太空": "outer space", "星空": "starry sky", "機器人": "robot", "機甲": "mecha"}


def lang_of(text):
    return "zh-TW" if re.search(r"[\u3400-\u9fff]", text or "") else "en"


def asked_kinds(text):
    """使用者明確點名的素材種類 → 數量（0 代表沒指定數量）。有點名時只做點名的種類，不自作主張多做（CPU 很慢）。"""
    found = {}
    for kind, rx in ASK_RE.items():
        if not rx.search(text):
            continue
        n = 0
        if kind == "image":
            m = re.search(r"([一兩二三四五\d])\s*[張幅]", text)
        elif kind == "model3d":
            m = re.search(r"([一兩二三四五\d])\s*[個隻架台款座艘名位種][^，。,.!?！？\n]{0,10}(模型|3d|建模)", text, re.I)
        else:
            m = None
        if m:
            n = ZH_NUM.get(m.group(1)) or (int(m.group(1)) if m.group(1).isdigit() else 0)
        found[kind] = max(0, min(CAPS[kind], n))
    return found


PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "genre": {"type": "string"},
        "colors": {"type": "object", "properties": {"primary": {"type": "string"}, "accent": {"type": "string"}},
                   "required": ["primary", "accent"]},
        "game": {"type": "object", "properties": {
            "description": {"type": "string"}, "controls": {"type": "string"}, "goal": {"type": "string"},
            "player_name": {"type": "string"}, "enemy_name": {"type": "string"}},
            "required": ["description", "controls", "goal", "player_name", "enemy_name"]},
        "assets": {"type": "array", "maxItems": 6, "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "type": {"type": "string", "enum": ["model3d", "image", "music"]},
            "role": {"type": "string", "enum": list(ROLES)}, "prompt": {"type": "string"},
            "purpose": {"type": "string"}}, "required": ["name", "type", "role", "prompt", "purpose"]}},
    },
    "required": ["title", "summary", "genre", "colors", "game", "assets"],
}


def plan_prompt(prompt, asked, lang):
    if asked:
        want = ", ".join(f"{'exactly ' + str(n) if n else 'at least 1'} {k}" for k, n in asked.items())
        rule = f"The user explicitly asked for these assets: {want}. Use ONLY these asset types."
    else:
        rule = "Choose the few assets that matter most (1-3 is ideal)."
    language = "Traditional Chinese (zh-TW)" if lang == "zh-TW" else "English"
    return f"""You plan small browser game projects for N.O.V.A., a local AI studio.

User request: {prompt}

Plan ONE small, fun 3D browser game (three.js) that fulfils the request. It must be playable in a single HTML file
on phones (virtual joystick + buttons) and on laptops (keyboard + mouse).
Local AI models will generate the assets:
- model3d: ONE simple 3D object from text (describe a single object, e.g. "a purple giant humanoid robot").
- image: a 512x512 picture (background, texture or poster).
- music: a short looping background track.
Rules:
- At most 2 model3d, 3 image and 1 music assets. {rule}
- asset name: short camelCase JavaScript identifier (hero, enemy, sky, bgm ...).
- asset prompt: concrete English description for the generator.
- role: player | enemy | background | music | prop.
- Write title, summary, genre, game.description, game.controls, game.goal, game.player_name, game.enemy_name
  and asset purpose in {language}. Keep them short.
- colors: two hex colors like #7b3fe4 that match the requested style.
Answer with JSON only."""


def default_texts(prompt, lang):
    zh = lang == "zh-TW"
    head = re.split(r"[，,。.!！？?\n]|(?:先|首先|第一步)", prompt, maxsplit=1)[0]
    head = re.sub(r"^(請|幫我|麻煩|我想要|我要|想要|給我)?\s*(做|製作|設計|打造|開發|寫)?\s*(一個|一款|個|款)?\s*", "", head).strip()
    title = (head or prompt).strip()[:24] or ("N.O.V.A. 專案" if zh else "N.O.V.A. Project")
    return {
        "title": title,
        "summary": (f"依照你的描述生成的網頁遊戲：{title}" if zh else f"A browser game generated from your request: {title}"),
        "description": ("操控機體在競技場中擊退一波波來襲的敵人。" if zh else "Pilot your mecha and repel waves of enemies."),
        "controls": ("筆電：WASD 移動、滑鼠拖曳轉視角、空白鍵攻擊、Shift 衝刺；手機：左下搖桿移動、右側拖曳轉視角、按鈕攻擊與衝刺。"
                     if zh else "Laptop: WASD move, drag to look, Space attack, Shift dash. Phone: joystick + buttons."),
        "goal": ("撐過越多波敵人越好，拿到最高分！" if zh else "Survive as many waves as you can!"),
        "player_name": "機體" if zh else "Mecha",
        "enemy_name": "敵人" if zh else "Enemy",
    }


def guess_genre(prompt):
    p = prompt.lower()
    for words, genre in ((("機器人", "機甲", "福音戰士", "eva", "鋼彈", "mecha", "robot", "gundam"), "mecha"),
                         (("射擊", "shoot", "槍"), "shooter"), (("賽車", "racing", "賽道"), "racing"),
                         (("跑酷", "runner", "跑"), "runner"), (("太空", "space", "星際"), "space")):
        if any(w in p for w in words):
            return genre
    return "action"


def default_colors(prompt):
    p = prompt.lower()
    if any(w in p for w in ("福音戰士", "eva", "evangelion")):
        return "#7b3fe4", "#7dff5a"
    if any(w in p for w in ("太空", "星", "space")):
        return "#2f7bff", "#ff4fd8"
    if any(w in p for w in ("火", "熔岩", "fire", "lava")):
        return "#ff5a1f", "#ffd23f"
    return "#00e5ff", "#ff3d7f"


def default_asset(kind, i, genre, subject):
    subj = f", {subject}" if subject else ""
    if kind == "model3d":
        if i == 0:
            obj = {"mecha": "a giant humanoid battle robot mecha, full body", "space": "a sleek space fighter ship",
                   "shooter": "a sleek space fighter ship", "racing": "a futuristic race car"}.get(
                genre, "a heroic game character, full body")
            return {"name": "hero", "type": kind, "role": "player", "prompt": obj + subj, "purpose": "玩家角色"}
        return {"name": "enemy", "type": kind, "role": "enemy", "prompt": "a menacing alien monster creature" + subj,
                "purpose": "敵人"}
    if kind == "image":
        name, role, what, purpose = [("sky", "background", "game background, wide scenic view, highly detailed, no text", "背景"),
                                     ("texture", "prop", "seamless game texture pattern", "材質貼圖"),
                                     ("poster", "prop", "game key art poster, dramatic lighting, no text", "宣傳圖")][min(i, 2)]
        return {"name": name, "type": kind, "role": role, "prompt": (subject + ", " if subject else "") + what,
                "purpose": purpose}
    return {"name": "bgm", "type": "music", "role": "music",
            "prompt": "energetic electronic game soundtrack, driving drums, loop" + subj, "purpose": "背景音樂"}


def subject_text(prompt):
    """去掉「先…再…匯出 html」這類流程用語，只留下題材，拿去翻成英文當素材提示詞。"""
    s = SEQ_WORDS.sub(" ", prompt)
    s = re.sub(r"(遊戲|小遊戲|體驗|風格的?|的)", " ", s)
    return re.sub(r"[\s，,。.、]+", " ", s).strip()[:200]


def js_name(name, used):
    n = re.sub(r"[^A-Za-z0-9_]", "", str(name or ""))[:24]
    if not n or not n[0].isalpha():
        n = "asset" + n
    n = n[0].lower() + n[1:]
    if n in JS_RESERVED:
        n += "Asset"
    base, k = n, 2
    while n in used:
        n, k = f"{base}{k}", k + 1
    used.add(n)
    return n


def hex_color(v, default):
    v = str(v or "").strip()
    return v.lower() if re.fullmatch(r"#[0-9a-fA-F]{6}", v) else default


def text_or(v, default, limit):
    v = re.sub(r"\s+", " ", str(v or "")).strip()
    return v[:limit] if v else default


def normalize_plan(raw, prompt, asked, lang, english=None):
    """把 LLM 給的計畫整理成安全、完整的格式；缺什麼就用規則補（raw 為空時就是完整的規則式計畫）。
    english(text, kind) → 英文提示詞，只有素材提示詞不是英文時才會呼叫。"""
    raw = raw if isinstance(raw, dict) else {}
    game = raw.get("game") if isinstance(raw.get("game"), dict) else {}
    colors = raw.get("colors") if isinstance(raw.get("colors"), dict) else {}
    d = default_texts(prompt, lang)
    genre = guess_genre(prompt)
    c1, c2 = default_colors(prompt)
    plan = {
        "title": text_or(raw.get("title"), d["title"], 40),
        "summary": text_or(raw.get("summary"), d["summary"], 240),
        "genre": text_or(raw.get("genre"), genre, 40),
        "colors": {"primary": hex_color(colors.get("primary"), c1), "accent": hex_color(colors.get("accent"), c2)},
        "game": {k: text_or(game.get(k), d[k], 300 if k in ("description", "controls") else 60)
                 for k in ("description", "controls", "goal", "player_name", "enemy_name")},
        "lang": lang,
    }
    # ---- 素材：型別 / 上限 / 使用者點名的種類
    items = [a for a in (raw.get("assets") if isinstance(raw.get("assets"), list) else []) if isinstance(a, dict)]
    count, assets, used = {k: 0 for k in CAPS}, [], set()
    subject = [None]

    def subj():
        if subject[0] is None:
            text = subject_text(prompt)
            en = (english(text, "image") if english and text and not text.isascii() else text) or ""
            if not en.isascii():  # 翻譯不了：只留英數字（例如 EVA）再補上認得的題材
                known = [v for k, v in STYLE_EN.items() if k in prompt]
                en = ", ".join(dict.fromkeys([" ".join(re.findall(r"[A-Za-z0-9][A-Za-z0-9'-]*", en))] + known))
            subject[0] = en.strip(", ")
        return subject[0]

    for a in items:
        kind = a.get("type") if a.get("type") in CAPS else None
        limit = CAPS.get(kind, 0)
        if asked:
            limit = (asked.get(kind) or limit) if kind in asked else 0
        if not kind or count[kind] >= limit:
            continue
        role = a.get("role") if a.get("role") in ROLES else (
            "music" if kind == "music" else "background" if kind == "image" else "player" if not count[kind] else "enemy")
        p = text_or(a.get("prompt"), "", 300)
        if p and not p.isascii() and english:
            p = english(p, kind)
        if not p or not p.isascii():
            p = default_asset(kind, count[kind], genre, subj())["prompt"]
        assets.append({"name": js_name(a.get("name"), used), "type": kind, "role": role, "prompt": p,
                       "purpose": text_or(a.get("purpose"), default_asset(kind, count[kind], genre, "")["purpose"], 80)})
        count[kind] += 1
    # 使用者點名的種類一定要有（數量有指定就補到指定數量）；什麼都沒點名又沒有計畫時，至少做一張背景圖
    need = dict(asked) if asked else ({} if assets else {"image": 1})
    for kind, n in need.items():
        while count[kind] < max(1, n):
            a = default_asset(kind, count[kind], genre, subj())
            a["name"] = js_name(a["name"], used)
            assets.append(a)
            count[kind] += 1
    plan["assets"] = assets
    return plan


def make_plan(job, prompt, asked, lang, model, english, log):
    """LLM 結構化輸出（Ollama format = JSON schema）；失敗就改用規則式計畫。回傳 (plan, 是否由 LLM 規劃)。"""
    if model:
        t0 = time.time()
        try:
            out = llm(job, model, plan_prompt(prompt, asked, lang), fmt=PLAN_SCHEMA, num_predict=900,
                      temperature=0.4, deadline=360,
                      on_text=lambda t: job.__setitem__("message", f"規劃專案中…（{len(t)} 字）"))
            m = re.search(r"\{.*\}", out, re.S)
            raw = json.loads(m.group(0) if m else out)
            log(f"plan: LLM {time.time() - t0:.0f}s")
            return normalize_plan(raw, prompt, asked, lang, english), True
        except Cancelled:
            raise
        except Exception as e:
            check_cancel(job)
            log(f"plan: LLM failed ({type(e).__name__}: {str(e)[:120]}) → rules")
    return normalize_plan({}, prompt, asked, lang, english), False


# ---------------------------------------------------------------- 素材
def generate_assets(job, plan, steps, prog, load_engines, log, warnings):
    assets = plan["assets"]
    if not assets:
        return []
    job["message"] = "載入生成引擎…"
    try:
        engines = load_engines()
    except Exception as e:
        log(f"engines: import failed {type(e).__name__}: {e}")
        for a in assets:
            steps.set(f"asset:{a['name']}", status="error", detail="生成引擎無法載入")
        warnings.append("生成引擎無法載入，遊戲改用內建幾何圖形")
        return []
    low_ram = _low_ram()
    if low_ram:
        free = getattr(engines, "_free_ollama", None)  # 16GB 的電腦：先請 Ollama 讓出記憶體給生成模型
        if free:
            free()
    done = []
    for i, a in enumerate(assets):
        check_cancel(job)
        sid, t0 = f"asset:{a['name']}", time.time()
        label = f"生成素材 {i + 1}/{len(assets)}：{a['name']}（{TYPE_ZH[a['type']]}）"
        job["message"] = label + "…"
        steps.set(sid, status="running", detail=a["prompt"][:120])

        def on_msg(m, label=label, sid=sid):
            m = str(m or "")
            job["message"] = f"{label} · {m}" if m else label
            steps.set(sid, detail=m)

        sub = SubJob(job, lambda v, pid=sid: prog.at(pid, v), on_msg)
        try:
            if a["type"] == "model3d":
                res = engines.gen_3d(sub, a["prompt"], 16)
            elif a["type"] == "image":
                res = engines.gen_image(sub, a["prompt"], 512, 512, 1, None, None)
            else:
                res = engines.gen_music(sub, a["prompt"], 10)
            check_cancel(job)
            a["url"] = res["url"]
            done.append(a)
            steps.set(sid, status="done", url=res["url"], detail=a.get("purpose") or None)
            log(f"asset {a['name']} ({a['type']}): {time.time() - t0:.0f}s {res['url']}")
        except Exception as e:
            if job.get("cancel"):
                raise Cancelled("已取消")
            traceback.print_exc()
            steps.set(sid, status="error", detail=f"{type(e).__name__}: {str(e)[:120]}")
            warnings.append(f"素材 {a['name']} 生成失敗，遊戲改用內建幾何圖形")
            log(f"asset {a['name']} failed: {type(e).__name__}: {str(e)[:200]}")
        prog.at(sid, 1)
    if low_ram:
        engines.SLOT.unload()  # 接下來要讓 LLM 寫程式，把生成模型移出記憶體
    return done


def _low_ram():
    try:
        import psutil
        return psutil.virtual_memory().total < 24 * 2**30
    except Exception:
        return True


def asset_path(url):
    """/outputs/xxx → 實際檔案路徑（只接受 outputs 資料夾裡、存在的檔案）。"""
    if not isinstance(url, str) or not url.startswith("/outputs/"):
        return None
    out = os.path.realpath(OUT)
    p = os.path.realpath(os.path.join(OUT, url[len("/outputs/"):].replace("/", os.sep)))
    try:
        inside = os.path.commonpath([p, out]) == out
    except ValueError:
        inside = False
    return p if inside and os.path.isfile(p) else None


# ---------------------------------------------------------------- 寫程式
API_DOC = """NovaKit API (import { NovaKit } from 'novakit'):
- NovaKit.init({ title }) -> NovaKit. Call once first. Builds the overlay UI (title, phone/laptop toggle, fullscreen,
  and on phones a virtual joystick + round action buttons).
- NovaKit.setButtons({ a: 'Attack', b: 'Dash' }) : labels of the action buttons a/b/x/y (only listed ones show on phones).
- const { renderer, scene, camera } = NovaKit.createRenderer({ antialias: true }) : full-window THREE.WebGLRenderer,
  THREE.Scene (already has a hemisphere light + sun) and THREE.PerspectiveCamera; window resize is handled for you.
- NovaKit.loop((dt, t) => { ...; renderer.render(scene, camera); }) : call ONCE. dt = seconds since last frame (max 0.05),
  t = total seconds. You must call renderer.render yourself.
- NovaKit.input.move.x / NovaKit.input.move.y : -1..1 (x = right, y = forward). Keys WASD / arrows, phone joystick.
- NovaKit.input.look.x / NovaKit.input.look.y : camera look delta for this frame (mouse drag, phone right-side drag).
- NovaKit.input.buttons.a (b, x, y) : true while held. NovaKit.input.pressed('a') : true only on the frame it was pressed.
  Keys: Space = a, Shift = b, E = x, Q = y, mouse click = a. NovaKit.input.any() : any button pressed this frame.
  NovaKit.input.move / look / buttons are objects: read them every frame, never assign to them.
- NovaKit.mode : 'mobile' or 'desktop'. NovaKit.onModeChange(mode => {}).
- NovaKit.loadModel(name, { height: 2 }) -> Promise<THREE.Group> : a generated 3D asset, standing on y = 0, scaled to height.
  Rejects if missing, so always use try/catch and fall back to simple meshes.
- NovaKit.loadTexture(name) -> Promise<THREE.Texture> : a generated image (e.g. scene.background = texture).
- NovaKit.playMusic(name, { volume: 0.5 }) : loop a music asset (starts after the first tap/click).
- NovaKit.sfx(kind) : synth sound, kind = 'shoot' | 'hit' | 'explode' | 'jump' | 'pickup' | 'alarm' | 'power'.
- NovaKit.hud(html) : set the HUD text (score, wave ...). NovaKit.bar(name, value0to1, { label, color }) : health/energy bar.
- NovaKit.toast(text) : short message. NovaKit.gameOver({ title, text, button }) -> Promise : game-over screen,
  resolves when the player presses the button (then reset your game state yourself; do not reload the page).
  NovaKit.victory({ title, text, button }) -> Promise : same for winning.
- NovaKit.random(min, max), NovaKit.randomInt(min, max), NovaKit.clamp(v, min, max), NovaKit.lerp(a, b, t)."""


def manifest(assets):
    rows = []
    for a in assets:
        n = a["name"]
        use = {"model3d": f"await NovaKit.loadModel('{n}', {{ height: 2 }})",
               "image": f"await NovaKit.loadTexture('{n}')",
               "music": f"NovaKit.playMusic('{n}', {{ volume: 0.5 }})"}[a["type"]]
        rows.append(f"- '{n}' ({a['type']}, role {a.get('role', 'prop')}): {a.get('purpose', '')} -> {use}")
    return "\n".join(rows) or "- (none: build everything from three.js primitives)"


def starter(plan, assets):
    """依照實際的素材名稱產生範例骨架，小模型照著改最不容易出錯。"""
    zh = plan.get("lang") == "zh-TW"
    t = (lambda a, b: a if zh else b)
    player = next((a for a in assets if a["type"] == "model3d" and a.get("role") != "enemy"), None) or \
        next((a for a in assets if a["type"] == "model3d"), None)
    bg = next((a for a in assets if a["type"] == "image"), None)
    music = next((a for a in assets if a["type"] == "music"), None)
    lines = ["import * as THREE from 'three';", "import { NovaKit } from 'novakit';", "",
             f"NovaKit.init({{ title: {json.dumps(plan['title'], ensure_ascii=False)} }});",
             f"NovaKit.setButtons({{ a: '{t('攻擊', 'Attack')}', b: '{t('衝刺', 'Dash')}' }});",
             "const { renderer, scene, camera } = NovaKit.createRenderer({ antialias: true });",
             "const ground = new THREE.Mesh(new THREE.PlaneGeometry(60, 60), "
             "new THREE.MeshStandardMaterial({ color: 0x1a2233 }));",
             "ground.rotation.x = -Math.PI / 2;", "scene.add(ground);"]
    if bg:
        lines += [f"try {{ scene.background = await NovaKit.loadTexture('{bg['name']}'); }}",
                  "catch (e) { scene.background = new THREE.Color(0x070812); }"]
    else:
        lines += ["scene.background = new THREE.Color(0x070812);"]
    color = plan["colors"]["primary"].replace("#", "0x")
    if player:
        lines += ["let player;", f"try {{ player = await NovaKit.loadModel('{player['name']}', {{ height: 2 }}); }}",
                  f"catch (e) {{ player = new THREE.Mesh(new THREE.BoxGeometry(1, 2, 1), "
                  f"new THREE.MeshStandardMaterial({{ color: {color} }})); player.position.y = 1; }}"]
    else:
        lines += [f"const player = new THREE.Mesh(new THREE.BoxGeometry(1, 2, 1), "
                  f"new THREE.MeshStandardMaterial({{ color: {color} }}));", "player.position.y = 1;"]
    lines += ["scene.add(player);"]
    if music:
        lines += [f"NovaKit.playMusic('{music['name']}', {{ volume: 0.4 }});"]
    lines += ["", "let score = 0, hp = 1, over = false;",
              "function reset() { score = 0; hp = 1; over = false; player.position.set(0, player.position.y, 0); }",
              "", "NovaKit.loop((dt, t) => {",
              "  const input = NovaKit.input;",
              "  if (!over) {",
              "    player.position.x += input.move.x * 6 * dt;",
              "    player.position.z -= input.move.y * 6 * dt;",
              f"    if (input.pressed('a')) {{ NovaKit.sfx('shoot'); /* {t('發射子彈', 'fire a bullet')} */ }}",
              "    if (hp <= 0) {",
              "      over = true;",
              "      NovaKit.sfx('explode');",
              f"      NovaKit.gameOver({{ title: '{t('任務失敗', 'Game Over')}', text: `{t('分數', 'Score')} ${{score}}`, "
              f"button: '{t('重新開始', 'Restart')}' }}).then(reset);",
              "    }", "  }",
              "  camera.position.set(player.position.x, 6, player.position.z + 9);",
              "  camera.lookAt(player.position);",
              f"  NovaKit.hud(`{t('分數', 'Score')} ${{score}}`);",
              f"  NovaKit.bar('hp', hp, {{ label: '{t('生命', 'HP')}', color: '#4f4' }});",
              "  renderer.render(scene, camera);", "});"]
    return "\n".join(lines)


def code_rules(lang):
    language = "Traditional Chinese (zh-TW)" if lang == "zh-TW" else "English"
    return f"""RULES:
1. Answer with ONE ```js code block that contains the complete ES module, and nothing else.
2. Allowed imports only: 'three', 'novakit', 'three/addons/loaders/GLTFLoader.js', 'three/addons/controls/OrbitControls.js'.
   No other libraries, no URLs, no fetch, no HTML, no require.
3. Call NovaKit.init, NovaKit.setButtons and NovaKit.createRenderer first. Use ONE NovaKit.loop and call
   renderer.render(scene, camera) at the end of every frame.
4. Read controls only from NovaKit.input so the game works on phones and laptops.
5. Every text the player sees (HUD, toast, buttons, game over) must be in {language}.
6. Load every asset inside try/catch and fall back to simple meshes when loading fails.
7. Clear goal, score, a way to lose, and restart with NovaKit.gameOver(...).then(reset).
8. Keep it small and fast: under 200 lines, at most about 60 meshes, reuse geometries and materials, no shadows.
9. Only use three.js r160 classes (THREE.BoxGeometry, THREE.SphereGeometry, THREE.MeshStandardMaterial ...)."""


def project_brief(plan, prompt, assets):
    g = plan["game"]
    return f"""PROJECT
Title: {plan['title']}
Summary: {plan['summary']}
Genre: {plan['genre']}
Game: {g['description']}
Controls: {g['controls']}
Goal: {g['goal']}
Player: {g['player_name']} / Enemies: {g['enemy_name']}
Colors: {plan['colors']['primary']} (primary), {plan['colors']['accent']} (accent)
Original request: {prompt}

ASSETS (already generated and embedded in the page, use these exact names):
{manifest(assets)}"""


def codegen_prompt(plan, prompt, assets, extra=""):
    return f"""Write the complete JavaScript game module (ES module, three.js + NovaKit) for this project.

{project_brief(plan, prompt, assets)}

{API_DOC}

{code_rules(plan.get('lang'))}
{extra}
STARTER (extend it into the full game):
```js
{starter(plan, assets)}
```"""


def repair_prompt(plan, prompt, assets, code, error, feedback=""):
    ask = ""
    if feedback:
        ask += f"\nThe player asks for these changes (apply them): {feedback}\n"
    if error:
        ask += f"\nThe module fails with this error (fix it):\n{error}\n"
    return f"""You maintain a small JavaScript browser game module (three.js + NovaKit).
{ask}
Return the COMPLETE updated module. Keep the same game unless the player asked for changes.

{project_brief(plan, prompt, assets)}

{API_DOC}

{code_rules(plan.get('lang'))}

CURRENT MODULE:
```js
{code}
```"""


FENCE_RE = re.compile(r"```[ \t]*([A-Za-z0-9_+-]*)[^\n]*\n(.*?)(?:\n[ \t]*```|$)", re.S)


def extract_code(text):
    """從 LLM 回覆取出程式碼：挑最長的 ``` 區塊（沒有收尾也收），整頁 HTML 就取出 module script。"""
    text = (text or "").replace("\r\n", "\n")
    blocks = [(lang.lower(), body) for lang, body in FENCE_RE.findall(text)]
    blocks = [b for b in blocks if b[0] in ("", "js", "javascript", "mjs", "jsx", "es6", "html", "ts", "typescript")]
    code = max((b for _, b in blocks), key=len, default="")
    if not code.strip():
        m = re.search(r"^\s*(import|const|let|NovaKit)\b", text, re.M)
        code = text[m.start():] if m else ""
    if re.search(r"<script\b", code, re.I):
        scripts = re.findall(r"<script\b[^>]*>(.*?)</script>", code, re.S | re.I)
        code = max(scripts, key=len, default="")
    return code.strip("\n").rstrip() + "\n" if code.strip() else ""


IMPORT_RE = re.compile(r"""^([ \t]*import\s+(?:([\w$*{}\s,]+?)\s+from\s+)?)(['"])([^'"\n]+)\3""", re.M)
DYN_IMPORT_RE = re.compile(r"""\bimport\(\s*(['"])([^'"\n]+)\1\s*\)""")


def map_spec(spec):
    s = spec.strip().lower()
    if "novakit" in s:
        return "novakit"
    for key in ("gltfloader", "orbitcontrols", "buffergeometryutils"):
        if key in s:
            return next(k for k in MODULES if key in k.lower())
    if re.search(r"(^|/)three(@[^/]*)?(/build/three(\.module)?(\.min)?\.js)?$", s) or s.endswith("three.module.js") \
            or s.endswith("three.module.min.js"):
        return "three"
    return None


def sanitize_code(code):
    """便宜的自動修正：匯入路徑改成打包後的名稱、預設匯入改成正確寫法、補上漏掉的 import。回傳 (程式碼, 問題清單)。"""
    problems = []

    def fix(m):
        head, binding, q, spec = m.group(1), (m.group(2) or "").strip(), m.group(3), m.group(4)
        target = map_spec(spec)
        if not target:
            problems.append(f"Import '{spec}' is not available (allowed: 'three', 'novakit', "
                            f"'three/addons/loaders/GLTFLoader.js', 'three/addons/controls/OrbitControls.js').")
            return m.group(0)
        if target == "three" and re.fullmatch(r"[\w$]+", binding or ""):
            return f"import * as {binding} from 'three'"
        if target == "novakit" and binding and not binding.startswith("{"):
            return "import { NovaKit } from 'novakit'"
        return f"{head}{q}{target}{q}"

    code = IMPORT_RE.sub(fix, code)
    code = DYN_IMPORT_RE.sub(lambda m: f"import('{map_spec(m.group(2)) or m.group(2)}')", code)
    imports = [m.group(4) for m in IMPORT_RE.finditer(code)]
    head = []
    if re.search(r"\bTHREE\.", code) and not re.search(r"\*\s+as\s+THREE\b", code):
        head.append("import * as THREE from 'three';")
    if re.search(r"\bNovaKit\b", code) and "novakit" not in imports:
        head.append("import { NovaKit } from 'novakit';")
    if re.search(r"\brequire\s*\(", code):
        problems.append("require() does not exist in the browser: use ES module imports.")
    if head:
        code = "\n".join(head) + "\n" + code
    return code, problems


_THREE_NAMES = {}


def three_names():
    path = os.path.join(VENDOR, "three.module.js")
    key = os.path.getmtime(path)
    if _THREE_NAMES.get("key") != key:
        with open(path, encoding="utf-8") as f:
            tail = f.read()[-20000:]
        m = re.search(r"export\s*\{([^}]*)\}\s*;?\s*$", tail)
        names = {n.strip().split(" as ")[-1].strip() for n in (m.group(1) if m else "").split(",")}
        _THREE_NAMES.update(key=key, names={n for n in names if n})
    return _THREE_NAMES["names"]


def api_problems(code):
    problems = []
    # 註解裡的「THREE.js」之類不算：先粗略去掉註解再找
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
    code = re.sub(r"(?<![:'\"\\])//[^\n]*", "", code)
    bad = sorted({n for n in re.findall(r"\bNovaKit\.([A-Za-z_$][\w$]*)", code) if n not in NOVAKIT_API})
    if bad:
        problems.append(f"NovaKit has no {', '.join('NovaKit.' + b for b in bad)}. Use only: "
                        + ", ".join(sorted(NOVAKIT_API)) + ".")
    bad = sorted({n for n in re.findall(r"\bNovaKit\.input\.([A-Za-z_$][\w$]*)", code) if n not in INPUT_API})
    if bad:
        problems.append(f"NovaKit.input has no {', '.join(bad)}. It has: move, look, buttons, pressed(name), any().")
    try:
        names = three_names()
    except OSError:
        names = set()
    bad = sorted({n for n in re.findall(r"\bTHREE\.([A-Za-z_$][\w$]*)", code) if names and n not in names})
    if bad:
        problems.append(f"three.js r160 has no {', '.join('THREE.' + b for b in bad)} (e.g. use THREE.BufferGeometry, "
                        "THREE.MathUtils, THREE.BoxGeometry).")
    return problems


def find_node():
    exe = os.environ.get("NOVA_NODE") or shutil.which("node")
    if not exe and os.name == "nt":
        for p in (r"C:\Program Files\nodejs\node.exe", r"C:\Program Files (x86)\nodejs\node.exe"):
            if os.path.exists(p):
                return p
    return exe


def node_check(code):
    """node --check 只檢查語法（不會執行、不會解析 import）。沒有 node 就回傳 None（略過）。"""
    node = find_node()
    if not node:
        return None
    fd, path = tempfile.mkstemp(suffix=".mjs", prefix="nova-game-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(code)
        p = subprocess.run([node, "--check", path], capture_output=True, timeout=60, creationflags=NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired):
        return None
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
    if p.returncode == 0:
        return ""
    err = p.stderr.decode("utf-8", "replace").replace("\r\n", "\n")
    line_no = None
    m = re.search(re.escape(os.path.basename(path)) + r":(\d+)", err)
    if m:
        line_no = int(m.group(1))
    msg = next((ln.strip() for ln in err.splitlines() if re.match(r"\s*\w*Error\b", ln)), "SyntaxError")
    return describe(msg, code, line_no)


def describe(msg, code, line_no):
    lines = code.split("\n")
    if line_no and 1 <= line_no <= len(lines):
        return f"{msg}\n  at line {line_no}: {lines[line_no - 1].strip()[:160]}"
    return msg


def balance(code):
    """程式被截斷（Unexpected end of input）時試著補上缺的括號；判斷不了就回傳 None。"""
    stack, i, n, pairs = [], 0, len(code), {"(": ")", "[": "]", "{": "}"}
    tmpl = []  # 樣板字串 ${ } 的巢狀深度
    while i < n:
        c = code[i]
        if c in "\"'":
            j = i + 1
            while j < n and code[j] != c and code[j] != "\n":
                j += 2 if code[j] == "\\" else 1
            i = j + 1
            continue
        if c == "`" or (c == "}" and tmpl and tmpl[-1] == len(stack)):
            if c == "}":
                tmpl.pop()
            j = i + 1
            while j < n and code[j] != "`":
                if code[j] == "\\":
                    j += 2
                    continue
                if code.startswith("${", j):
                    tmpl.append(len(stack))
                    break
                j += 1
            i = j + (2 if j < n and code.startswith("${", j) else 1)
            continue
        if code.startswith("//", i):
            i = code.find("\n", i) if code.find("\n", i) >= 0 else n
            continue
        if code.startswith("/*", i):
            j = code.find("*/", i + 2)
            if j < 0:
                return None
            i = j + 2
            continue
        if c in pairs:
            stack.append(pairs[c])
        elif c in ")]}":
            if not stack or stack[-1] != c:
                return None
            stack.pop()
        i += 1
    return code.rstrip() + "\n" + "".join(reversed(stack)) + ";\n" if stack and not tmpl else None


def check_code(code):
    """靜態檢查：語法（node --check）＋ API 名稱。回傳錯誤說明，空字串代表通過。"""
    problems = api_problems(code)
    syntax = node_check(code)
    if syntax:
        problems.insert(0, syntax)
    return "\n".join(problems)


def find_browser():
    exe = os.environ.get("NOVA_BROWSER")
    if exe and os.path.exists(exe):
        return exe
    cands = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
             r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
             r"C:\Program Files\Google\Chrome\Application\chrome.exe",
             r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
             "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
             "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
             "/Applications/Chromium.app/Contents/MacOS/Chromium"]
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        cands.append(os.path.join(os.environ["LOCALAPPDATA"], r"Google\Chrome\Application\chrome.exe"))
    for p in cands:
        if os.path.exists(p):
            return p
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge"):
        if shutil.which(name):
            return shutil.which(name)
    return None


def smoke_test(page_html, budget_ms=10000, timeout=150, cancelled=None):
    """用無頭瀏覽器實際開啟遊戲幾秒（含模擬按鍵），收集執行期錯誤。
    回傳 (錯誤清單, 狀態說明)；錯誤清單為 None 代表沒辦法測（沒有瀏覽器 / 逾時 / 取消），當作通過。"""
    exe = find_browser() if SMOKE else None
    if not exe:
        return None, "skipped"
    tmp = tempfile.mkdtemp(prefix="nova-smoke-")
    try:
        page = os.path.join(tmp, "game.html")
        with open(page, "w", encoding="utf-8") as f:
            f.write(page_html)
        url = pathlib.Path(page).as_uri()
        args = [exe, "--headless=new", "--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--no-first-run",
                "--no-default-browser-check", "--disable-extensions", "--mute-audio",
                "--autoplay-policy=no-user-gesture-required", f"--user-data-dir={os.path.join(tmp, 'profile')}",
                # 小視窗 + 不限幀率：軟體繪圖下虛擬時間裡才跑得到足夠的幀數
                "--window-size=480,320", "--disable-gpu-vsync", "--disable-frame-rate-limit",
                f"--virtual-time-budget={budget_ms}", "--dump-dom", url]
        dom = os.path.join(tmp, "dom.html")  # 輸出寫到檔案：邊等邊檢查取消，不怕管線塞滿
        with open(dom, "wb") as out:
            p = subprocess.Popen(args, stdout=out, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                 creationflags=NO_WINDOW)
            t0 = time.time()
            while p.poll() is None:
                if time.time() - t0 > timeout or (cancelled and cancelled()):
                    _kill_tree(p)
                    return None, "cancelled" if cancelled and cancelled() else "timeout"
                time.sleep(0.25)
        with open(dom, "rb") as f:
            m = re.search(rb'data-nova-smoke="([^"]*)"', f.read())
        if not m:
            return None, "no result"
        data = json.loads(htmllib.unescape(m.group(1).decode("utf-8", "replace")))
        state = "started={started} canvas={canvas} frames={frames} inputs={inputs}".format(
            **{k: data.get(k) for k in ("started", "canvas", "frames", "inputs")})
        return [e for e in data.get("errors") or [] if isinstance(e, dict)], state
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _kill_tree(p):
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True, timeout=20,
                           creationflags=NO_WINDOW)
        else:
            p.kill()
        p.wait(timeout=10)
    except Exception:
        pass


def runtime_error(errors, code):
    e = errors[0]
    msg = str(e.get("message") or "Error").replace("Uncaught ", "")
    stack = str(e.get("stack") or "")
    m = re.search(r"game\.js:(\d+)", stack) or re.search(r"game\.js:(\d+)", msg)
    return "Runtime error in the browser: " + describe(msg[:300], code, int(m.group(1)) if m else None)


def clean_error(text, limit=2000):
    """前端回報的錯誤：去掉超長的 data: URL，限制長度。"""
    text = re.sub(r"data:[\w/+.-]+;base64,[A-Za-z0-9+/=]{16,}", "data:…", str(text or ""))
    text = re.sub(r"data:[^\s)'\"]{200,}", "data:…", text)
    return text.strip()[:limit]


# ---------------------------------------------------------------- 範本
def template_config(plan, assets):
    zh = plan.get("lang") == "zh-TW"
    by = lambda kind, role=None: next((a["name"] for a in assets if a["type"] == kind and
                                       (role is None or a.get("role") == role)), None)
    models = [a["name"] for a in assets if a["type"] == "model3d"]
    player = by("model3d", "player") or (models[0] if models else None)
    enemy = by("model3d", "enemy") or next((n for n in models if n != player), None)
    cfg = {"title": plan["title"], "subtitle": plan["summary"], "primaryColor": plan["colors"]["primary"],
           "accentColor": plan["colors"]["accent"], "playerName": plan["game"]["player_name"],
           "enemyName": plan["game"]["enemy_name"], "goalText": plan["game"]["goal"],
           "buttons": {"a": "攻擊", "b": "衝刺"} if zh else {"a": "Attack", "b": "Dash"}}
    for key, val in (("playerAsset", player), ("enemyAsset", enemy),
                     ("backgroundAsset", by("image", "background") or by("image")), ("musicAsset", by("music"))):
        if val:
            cfg[key] = val
    return cfg


EMERGENCY = """import * as THREE from 'three';
import { NovaKit } from 'novakit';
// 範本檔不存在時的最小可玩版本：收集能量球
const cfg = window.NOVA_TEMPLATE_CONFIG || {};
NovaKit.init({ title: cfg.title || 'N.O.V.A.' });
NovaKit.setButtons({ a: (cfg.buttons && cfg.buttons.a) || 'A' });
const { renderer, scene, camera } = NovaKit.createRenderer({ antialias: true });
scene.background = new THREE.Color(0x05060f);
scene.add(new THREE.HemisphereLight(0xffffff, 0x223344, 1.5));
scene.add(new THREE.GridHelper(40, 40, 0x335577, 0x1a2a3a));
const player = new THREE.Mesh(new THREE.BoxGeometry(1, 2, 1), new THREE.MeshStandardMaterial({ color: cfg.primaryColor || '#00e5ff' }));
player.position.y = 1;
scene.add(player);
const orb = new THREE.Mesh(new THREE.SphereGeometry(0.5, 16, 12), new THREE.MeshStandardMaterial({ color: cfg.accentColor || '#ff3d7f', emissive: 0x331122 }));
scene.add(orb);
let score = 0;
function moveOrb() { orb.position.set(NovaKit.random(-15, 15), 0.6, NovaKit.random(-15, 15)); }
moveOrb();
NovaKit.loop((dt) => {
  player.position.x = NovaKit.clamp(player.position.x + NovaKit.input.move.x * 8 * dt, -19, 19);
  player.position.z = NovaKit.clamp(player.position.z - NovaKit.input.move.y * 8 * dt, -19, 19);
  if (player.position.distanceTo(orb.position) < 1.4) { score++; NovaKit.sfx('pickup'); moveOrb(); }
  camera.position.set(player.position.x, 10, player.position.z + 12);
  camera.lookAt(player.position);
  NovaKit.hud('★ ' + score);
  renderer.render(scene, camera);
});
"""


def template_code():
    path = os.path.join(STATIC, "project_template.js")
    try:
        with open(path, encoding="utf-8") as f:
            return f.read(), True
    except OSError:
        return EMERGENCY, False


# ---------------------------------------------------------------- 打包
EARLY_JS = r"""(function () {
  // 遊戲模組開始執行前（語法錯誤、匯入失敗）NovaKit 還沒載入：由這裡回報給上層視窗並顯示在畫面上
  var errs = window.__novaErrors = [];
  function clean(s) { return String(s == null ? '' : s).replace(/data:[\w\/+.-]+;base64,[A-Za-z0-9+\/=]{16,}/g, 'data:…').replace(/data:[^\s)'"]{200,}/g, 'data:…').slice(0, 2000); }
  function show(msg) {
    try {
      var d = document.getElementById('nova-early-error');
      if (!d) {
        d = document.createElement('pre');
        d.id = 'nova-early-error';
        d.style.cssText = 'position:fixed;left:8px;right:8px;bottom:8px;z-index:2147483647;margin:0;padding:10px 12px;' +
          'max-height:40vh;overflow:auto;white-space:pre-wrap;font:12px/1.5 ui-monospace,Consolas,monospace;' +
          'color:#fff;background:rgba(160,20,40,.92);border-radius:8px;pointer-events:auto';
        (document.body || document.documentElement).appendChild(d);
      }
      d.textContent = '\u26a0 ' + msg;
    } catch (e) {}
  }
  function report(message, stack) {
    message = clean(message); stack = clean(stack);
    errs.push({ message: message, stack: stack });
    if (errs.length > 20) errs.shift();
    if (window.__novaGameStarted) return;  // 遊戲開始之後由 NovaKit 回報
    try { if (window.parent && window.parent !== window) window.parent.postMessage({ type: 'nova-error', message: message, stack: stack }, '*'); } catch (e) {}
    show(message);
  }
  window.addEventListener('error', function (e) {
    if (e && typeof e.message === 'string') report(e.message, (e.error && e.error.stack) || (e.filename ? e.filename + ':' + e.lineno + ':' + e.colno : ''));
  });
  window.addEventListener('unhandledrejection', function (e) {
    var r = e && e.reason;
    report('Unhandled rejection: ' + (r && r.message || r), r && r.stack);
  });
  // 沙盒 iframe / 部分瀏覽器的 file:// 讀 localStorage 會直接丟例外：換成記憶體版本，遊戲程式照常可用
  try { window.localStorage.getItem('nova'); } catch (e) {
    var mem = {};
    var store = {
      getItem: function (k) { return Object.prototype.hasOwnProperty.call(mem, k) ? mem[k] : null; },
      setItem: function (k, v) { mem[k] = String(v); },
      removeItem: function (k) { delete mem[k]; },
      clear: function () { mem = {}; },
      key: function (i) { return Object.keys(mem)[i] || null; }
    };
    Object.defineProperty(store, 'length', { get: function () { return Object.keys(mem).length; } });
    try { Object.defineProperty(window, 'localStorage', { value: store, configurable: true }); } catch (e2) {}
    try { Object.defineProperty(window, 'sessionStorage', { value: store, configurable: true }); } catch (e2) {}
  }
})();"""

SMOKE_JS = r"""(function () {
  // 只在打包前的自動測試使用：記錄錯誤、模擬玩家操作
  Element.prototype.requestPointerLock = function () { return Promise.resolve(); };
  Element.prototype.requestFullscreen = function () { return Promise.resolve(); };
  var frames = 0, played = 0;
  // NovaKit 自己接住遊戲迴圈裡的錯誤，用 postMessage 回報（最上層視窗時是傳給自己）
  window.addEventListener('message', function (e) {
    var d = e && e.data;
    if (d && d.type === 'nova-error') (window.__novaErrors = window.__novaErrors || []).push({ message: String(d.message || ''), stack: String(d.stack || '').slice(0, 2000) });
  });
  function flush() {
    try {
      document.documentElement.setAttribute('data-nova-smoke', JSON.stringify({
        errors: (window.__novaErrors || []).slice(0, 6), started: !!window.__novaGameStarted,
        canvas: document.getElementsByTagName('canvas').length, frames: frames, inputs: played }));
    } catch (e) {}
  }
  setInterval(flush, 100);
  flush();
  function key(type, code, k) {
    (document.body || document.documentElement).dispatchEvent(new KeyboardEvent(type, { code: code, key: k, bubbles: true }));
  }
  function mouse(type) {
    var c = document.querySelector('canvas');
    if (!c) return;
    var r = c.getBoundingClientRect();
    var o = { bubbles: true, cancelable: true, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2, button: 0,
      buttons: type === 'down' ? 1 : 0, pointerId: 1, pointerType: 'mouse', isPrimary: true };
    c.dispatchEvent(new PointerEvent('pointer' + type, o));
    c.dispatchEvent(new MouseEvent('mouse' + type, o));
  }
  // 無頭瀏覽器的虛擬時間裡畫面很少，所以按「幀」而不是按時間送操作，而且每一幀讓遊戲時間前進 50ms（NovaKit 的 dt 上限）：
  // 大約 70 幀就等於玩了 3 秒多（第一波敵人出現、碰撞、計時器都跑得到）。做完就凍結 requestAnimationFrame，
  // 頁面閒下來，虛擬時間很快用完（軟體繪圖每幀都很貴）
  var steps = [
    function () { key('keydown', 'KeyW', 'w'); key('keydown', 'KeyD', 'd'); },
    function () { key('keydown', 'Space', ' '); },
    function () { key('keyup', 'Space', ' '); mouse('down'); },
    function () { mouse('up'); key('keydown', 'ShiftLeft', 'Shift'); },
    function () { key('keyup', 'ShiftLeft', 'Shift'); key('keydown', 'KeyE', 'e'); },
    function () { key('keyup', 'KeyE', 'e'); key('keydown', 'KeyQ', 'q'); },
    function () { key('keyup', 'KeyQ', 'q'); key('keyup', 'KeyW', 'w'); key('keyup', 'KeyD', 'd'); key('keydown', 'KeyS', 's'); },
    function () { key('keydown', 'Space', ' '); },
    function () { key('keyup', 'Space', ' '); key('keyup', 'KeyS', 's'); key('keydown', 'KeyA', 'a'); },
    function () { key('keyup', 'KeyA', 'a'); }
  ];
  var EVERY = 6, RUN = 70, start = -1, raf = window.requestAnimationFrame.bind(window), realNow = performance.now.bind(performance);
  // 無頭模式的 rAF 時間戳常常不前進：改用自己的時鐘，每一幀（下面的 frame 每幀跑一次）固定前進 50ms
  var fake = -1;
  function now() { return fake < 0 ? realNow() : fake; }
  window.requestAnimationFrame = function (cb) { return raf(function () { cb(now()); }); };
  performance.now = now;
  (function frame() {
    fake = fake < 0 ? realNow() : fake + 50;
    frames++;
    if (window.__novaGameStarted && document.querySelector('canvas')) {
      if (start < 0) start = frames;
      var i = frames - start - 2;
      if (i >= 0 && i % EVERY === 0 && i / EVERY < steps.length) {
        try { steps[i / EVERY](); played++; } catch (e) {}
      }
      if (i > Math.max(RUN, steps.length * EVERY + 4)) {
        window.requestAnimationFrame = function () { return 0; };
        flush();
        return;
      }
    }
    raf(frame);
  })();
})();"""

PAGE_CSS = """*,*::before,*::after{box-sizing:border-box}
html,body{margin:0;padding:0;width:100%;height:100%;overflow:hidden;background:#000;color:#fff;
font-family:system-ui,-apple-system,"Segoe UI","Noto Sans TC","PingFang TC","Microsoft JhengHei",sans-serif;
-webkit-user-select:none;user-select:none;-webkit-touch-callout:none;-webkit-tap-highlight-color:transparent;
touch-action:none;overscroll-behavior:none;-webkit-text-size-adjust:100%}
canvas{display:block;touch-action:none;outline:none}
button{font:inherit}"""

_VENDOR_CACHE = {}


def data_js(src, name):
    # sourceURL 讓錯誤堆疊顯示 nova://three.module.js:123，而不是好幾 MB 的 data: 網址
    body = src.rstrip() + f"\n//# sourceURL=nova://{name}\n"
    return "data:text/javascript;base64," + base64.b64encode(body.encode("utf-8")).decode("ascii")


def module_urls():
    """importmap：three / addons（快取）+ novakit（每次重新讀，方便更新）。"""
    urls = {}
    for spec, rel in MODULES.items():
        path = os.path.join(VENDOR, rel.replace("/", os.sep))
        key = (path, os.path.getmtime(path))
        if _VENDOR_CACHE.get(spec, (None,))[0] != key:
            with open(path, encoding="utf-8") as f:
                src = f.read()
            # data: URL 模組沒有目錄可言，相對路徑的 import 要改成 importmap 裡的名稱
            src = re.sub(r"""(['"])\.\./utils/BufferGeometryUtils\.js\1""", "'three/addons/utils/BufferGeometryUtils.js'", src)
            _VENDOR_CACHE[spec] = (key, data_js(src, os.path.basename(rel)))
        urls[spec] = _VENDOR_CACHE[spec][1]
    kit = os.path.join(STATIC, "novakit.js")
    if not os.path.isfile(kit):
        raise RuntimeError("找不到 static/novakit.js")
    with open(kit, encoding="utf-8") as f:
        urls["novakit"] = data_js(f.read(), "novakit.js")
    return urls


def js_json(obj):
    """可以安全放進 <script> 的 JSON（< > & 與 U+2028 都轉義，不會提早結束 script 標籤）。"""
    return json.dumps(obj, ensure_ascii=True).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def script_safe(code):
    code = re.sub(r"</(script)", r"<\\/\1", code, flags=re.I)
    return code.replace("<!--", "<\\x21--")


def embed_assets(assets):
    out = {}
    for a in assets:
        p = asset_path(a.get("url"))
        if p:
            with open(p, "rb") as f:
                out[a["name"]] = f"data:{MIME[a['type']]};base64," + base64.b64encode(f.read()).decode("ascii")
    return out


def assemble(plan, assets, code, cfg=None, smoke=False, embedded=None):
    """組成單一離線 HTML：importmap（data: URL）→ 素材與設定 → 遊戲模組。"""
    lang = "zh-Hant" if plan.get("lang") == "zh-TW" else "en"
    globals_js = (f"window.NOVA_ASSETS = {js_json(embedded if embedded is not None else embed_assets(assets))};\n"
                  f"window.NOVA_META = {js_json({'title': plan['title'], 'summary': plan['summary']})};")
    if cfg is not None:
        globals_js += f"\nwindow.NOVA_TEMPLATE_CONFIG = {js_json(cfg)};"
    parts = [
        "<!doctype html>", f'<html lang="{lang}">', "<head>", '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no, '
        'viewport-fit=cover">',
        '<meta name="generator" content="N.O.V.A. project mode">',
        f"<title>{htmllib.escape(plan['title'])}</title>", f"<style>\n{PAGE_CSS}\n</style>",
        f"<script>\n{EARLY_JS}\n</script>",
    ]
    if smoke:
        parts.append(f"<script>\n{SMOKE_JS}\n</script>")
    parts += [
        f'<script type="importmap">{js_json({"imports": module_urls()})}</script>',
        f"<script>\n{globals_js}\n</script>", "</head>", "<body>",
        # 第一行開頭設旗標（import 會被提升，這行在所有模組載入後、遊戲程式之前執行），行號不變
        f'<script type="module">window.__novaGameStarted=1;{script_safe(code.rstrip())}\n//# sourceURL=game.js\n</script>',
        "</body>", "</html>", "",
    ]
    return "\n".join(parts)


# ---------------------------------------------------------------- 寫程式流程
def write_code(job, plan, prompt, assets, model, steps, prog, log, warnings, prior=None, feedback="", error=""):
    """LLM 寫程式 → 檢查（語法 / API / 瀏覽器實測）→ 最多修 REPAIRS 次。
    回傳 (code, "llm"|"template"|"keep")；失敗時 code 為 None。"""
    t_start = time.time()
    embedded = embed_assets(assets)
    code, err = None, ""
    attempts = 1 + REPAIRS
    for attempt in range(attempts):
        check_cancel(job)
        remain = CODE_BUDGET - (time.time() - t_start)
        if not model or remain < 120:
            log(f"code: stop (model={bool(model)}, remain={remain:.0f}s)")
            break
        fixing = attempt > 0
        if attempt == 0 and prior is not None:
            text, what = repair_prompt(plan, prompt, assets, prior, error, feedback), "修改遊戲程式"
        elif attempt == 0:
            extra = f"\nThe player also asks: {feedback}\n" if feedback else ""
            if error:  # 修正範本版本：重寫時提醒上一版遇到的錯誤
                extra += f"\nThe previous version crashed with this error, avoid it:\n{error[:600]}\n"
            text, what = codegen_prompt(plan, prompt, assets, extra), "AI 撰寫遊戲程式"
        elif code:
            text, what = repair_prompt(plan, prompt, assets, code, err), f"修正程式錯誤（第 {attempt} 次）"
        else:
            text = codegen_prompt(plan, prompt, assets, "\nIMPORTANT: the previous answer had no code. Reply with one "
                                                        "```js code block only.\n")
            what = f"重新撰寫（第 {attempt} 次）"
        steps.set("code", status="running", detail=what + "…")
        base = attempt / attempts

        def on_text(t, what=what, base=base):
            job["message"] = f"{what}…（{len(t)} 字）"
            prog.at("code", base + min(1, len(t) / 6000) / attempts * 0.9)

        t0 = time.time()
        try:
            out = llm(job, model, text, num_predict=4000, temperature=0.2 if fixing else 0.35,
                      deadline=min(1500, remain), on_text=on_text,
                      stop=lambda t: bool(re.search(r"```[ \t]*[A-Za-z]*[^\n]*\n.*?\n[ \t]*```", t, re.S)))
        except Cancelled:
            raise
        except Exception as e:
            check_cancel(job)
            log(f"code attempt {attempt}: LLM error {type(e).__name__}: {str(e)[:160]}")
            if isinstance(e, TimeoutError):
                break
            continue
        new = extract_code(out)
        log(f"code attempt {attempt}: {time.time() - t0:.0f}s, {len(out)} chars, code {len(new)} chars")
        if not new.strip():
            code, err = None, "no code block"
            continue
        code, problems = sanitize_code(new)
        job["message"] = "檢查程式碼…"
        steps.set("code", detail="檢查程式碼…")
        err = "\n".join(p for p in problems + [check_code(code)] if p)
        if err and "end of input" in err:
            fixed = balance(code)
            if fixed and not check_code(fixed):
                code, err = fixed, ""
                log("code: auto-balanced brackets")
        if not err:
            job["message"] = "在瀏覽器裡試玩檢查…"
            steps.set("code", detail="在瀏覽器裡試玩檢查…")
            errors, state = smoke_test(assemble(plan, assets, code, smoke=True, embedded=embedded),
                                       cancelled=lambda: bool(job.get("cancel")))
            check_cancel(job)
            log(f"code attempt {attempt}: smoke {state}, errors={len(errors or [])}")
            if errors:
                err = runtime_error(errors, code)
        if not err:
            log(f"code: ok after {attempt} repair(s)")
            return code, "llm"
        log(f"code attempt {attempt}: problem: {err[:300]}")
    # 修不好：修改既有專案時，原本的版本沒有回報錯誤就保留原本的；否則用範本
    if prior is not None and not error:
        warnings.append("這次的修改沒有通過檢查，保留原本的版本")
        return prior, "keep"
    return None, "template"


# ---------------------------------------------------------------- 主流程
def valid_id(pid):
    return isinstance(pid, str) and bool(ID_RE.fullmatch(pid)) and \
        os.path.isfile(os.path.join(OUT, f"{pid}.project.json"))


def load_project(pid):
    if not valid_id(pid):
        raise ValueError("找不到要修正的專案")
    with open(os.path.join(OUT, f"{pid}.project.json"), encoding="utf-8") as f:
        return json.load(f)


def new_stamp():
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


def run(job, params, load_engines, english=None):
    """kind="project" 任務入口。english(text, kind, model) → (英文, ok)，用來把中文素材描述翻成英文。"""
    if params.get("fix_of"):
        return run_fix(job, params)
    t_start, logs, warnings = time.time(), [], []

    def log(msg):
        logs.append(f"[{time.time() - t_start:6.1f}s] {msg}")

    prompt = str(params.get("prompt") or "").strip()[:4000]
    if not prompt:
        raise ValueError("沒有專案描述")
    lang, asked = lang_of(prompt), asked_kinds(prompt)
    model = pick_llm(params.get("llm"))
    log(f"start: model={model} lang={lang} asked={asked}")
    steps = Steps(job)
    steps.add("plan", "規劃專案")
    steps.add("code", "撰寫遊戲程式")
    steps.add("assemble", "打包 HTML")
    prog = Progress(job, [("plan", WEIGHT["plan"]), ("code", WEIGHT["code"]), ("assemble", WEIGHT["assemble"])])

    def eng(text, kind):
        if not english:
            return text
        en, _ = english(text, kind, model)
        return en

    # 1) 規劃
    steps.set("plan", status="running")
    job["message"] = "規劃專案中…"
    plan, by_llm = make_plan(job, prompt, asked, lang, model, eng, log)
    if not by_llm:
        warnings.append("規劃模型沒有回應，改用內建規則規劃")
    steps.set("plan", status="done", detail=f"{plan['title']}（{'AI 規劃' if by_llm else '規則規劃'}）")
    for a in plan["assets"]:
        steps.add(f"asset:{a['name']}", f"生成{TYPE_ZH[a['type']]}：{a['name']}", before="code",
                  detail=a.get("purpose") or None)
    prog = Progress(job, [("plan", WEIGHT["plan"])] +
                    [(f"asset:{a['name']}", WEIGHT[a["type"]]) for a in plan["assets"]] +
                    [("code", WEIGHT["code"]), ("assemble", WEIGHT["assemble"])])
    prog.at("plan", 1)

    # 2) 素材
    assets = generate_assets(job, plan, steps, prog, load_engines, log, warnings)
    check_cancel(job)

    # 3) 程式 + 4) 打包
    return finish(job, plan, prompt, assets, model, steps, prog, log, logs, warnings, t_start)


def run_fix(job, params):
    t_start, logs, warnings = time.time(), [], []

    def log(msg):
        logs.append(f"[{time.time() - t_start:6.1f}s] {msg}")

    pid = params.get("fix_of")
    proj = load_project(pid)
    plan, prompt = proj["plan"], proj.get("prompt") or ""
    feedback = str(params.get("feedback") or "").strip()[:2000]
    error = clean_error(params.get("error"))
    model = pick_llm(params.get("llm"))
    log(f"fix of {pid}: model={model} feedback={feedback[:80]!r} error={error[:80]!r}")
    steps = Steps(job)
    steps.add("plan", "讀取原專案", status="done", detail=plan.get("title"))
    assets = []
    for a in proj.get("assets") or []:
        if isinstance(a, dict) and a.get("type") in CAPS and asset_path(a.get("url")):
            assets.append(a)
            steps.add(f"asset:{a['name']}", f"沿用{TYPE_ZH[a['type']]}：{a['name']}", status="done", url=a["url"],
                      detail="沿用原專案")
    steps.add("code", "修正遊戲程式")
    steps.add("assemble", "打包 HTML")
    prog = Progress(job, [("plan", 0.2), ("code", WEIGHT["code"]), ("assemble", WEIGHT["assemble"])])
    prog.at("plan", 1)
    prior = proj.get("code") if proj.get("code_path") == "llm" and proj.get("code") else None
    if prior and len(prior) > 24000:  # 太長的程式小模型改不動：改成依計畫重寫
        prior = None
    return finish(job, plan, prompt, assets, model, steps, prog, log, logs, warnings, t_start,
                  prior=prior, feedback=feedback, error=error, parent=pid)


def finish(job, plan, prompt, assets, model, steps, prog, log, logs, warnings, t_start,
           prior=None, feedback="", error="", parent=None):
    code, path = write_code(job, plan, prompt, assets, model, steps, prog, log, warnings,
                            prior=prior, feedback=feedback, error=error)
    check_cancel(job)
    cfg = None
    if code is None:
        code, found = template_code()
        cfg = template_config(plan, assets)
        path = "template"
        if not found:
            log("template: static/project_template.js missing → emergency game")
        warnings.append("AI 寫的程式沒有通過檢查，改用內建範本遊戲" if model else "本機 LLM 無法使用，改用內建範本遊戲")
        steps.set("code", status="done", detail="使用內建範本")
    else:
        steps.set("code", status="done", detail="保留原本的版本" if path == "keep" else "AI 撰寫（已通過檢查）")
        path = "llm"
    prog.at("code", 1)

    job["message"] = "打包 HTML…"
    steps.set("assemble", status="running")
    stamp = new_stamp()
    page = assemble(plan, assets, code, cfg)
    with open(os.path.join(OUT, f"{stamp}.html"), "w", encoding="utf-8", newline="\n") as f:
        f.write(page)
    url = f"/outputs/{stamp}.html"
    log(f"assemble: {len(page) / 2**20:.1f} MB → {url} ({path})")
    record = {
        "version": 1, "id": stamp, "created": time.strftime("%Y-%m-%d %H:%M:%S"), "prompt": prompt,
        "parent": parent, "feedback": feedback or None, "error": error or None, "model": model,
        "plan": plan, "code_path": path, "code": code if path == "llm" else None, "template_config": cfg,
        "assets": [{k: a.get(k) for k in ("name", "type", "role", "prompt", "purpose", "url")} for a in assets],
        "url": url, "warnings": warnings, "log": logs, "seconds": round(time.time() - t_start, 1),
    }
    with open(os.path.join(OUT, f"{stamp}.project.json"), "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=1)
    steps.set("assemble", status="done", url=url, detail=f"{len(page) / 2**20:.1f} MB")
    prog.at("assemble", 1)
    if warnings:
        job["warning"] = "；".join(dict.fromkeys(warnings))
    result = {"type": "project", "url": url, "project_id": stamp, "title": plan["title"], "summary": plan["summary"],
              "assets": [{k: a.get(k) for k in ("name", "type", "url", "purpose")} for a in assets],
              "code_path": path, "warnings": list(dict.fromkeys(warnings))}
    if parent:
        result["fix_of"] = parent
    return result
