"""N.O.V.A. — Neural Omni Virtual Assistant
本機 AI 伺服器：聊天 / 寫程式 (Ollama)、圖片 / 影片 / 音樂 / 3D (本機模型)、網路搜尋 (DuckDuckGo)。
啟動：python server.py  然後打開 http://localhost:7860
"""
import asyncio
import ipaddress
import json
import os
import queue
import re
import sys
import threading
import time
import traceback
import uuid
from urllib.parse import unquote, urlsplit

BASE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault("HF_HOME", os.path.join(BASE, "models"))
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

import httpx
import psutil
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

import router
import websearch

OLLAMA = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
PORT = int(os.environ.get("NOVA_PORT", "7860"))
DEFAULT_MODEL = os.environ.get("NOVA_MODEL", "")
MAX_QUEUED = int(os.environ.get("NOVA_MAX_QUEUE", "10"))  # 排隊中的生成任務上限
KEEP_JOBS = 200  # 記憶體裡最多保留幾個已結束的任務
MAX_BODY = 32 * 2**20  # 請求大小上限（附圖是 base64，給寬一點）
ROUTE_CHARS = 8000  # 路由 / 搜尋只看訊息前段，超長貼文不會卡住整個伺服器
# 允許的網域（例如反向代理或 Tailscale 的名稱），逗號分隔；設成 * 代表不檢查 Host
EXTRA_HOSTS = {h.strip().lower() for h in os.environ.get("NOVA_ALLOWED_HOSTS", "").split(",") if h.strip()}
LOCAL_ORIGINS = {f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"}
# 放在 GitHub Pages 的介面會從這些網址連回本機的 AI 核心（逗號分隔，可用 NOVA_WEB_ORIGINS 覆寫）
WEB_ORIGINS = {o.strip().lower().rstrip("/") for o in
               os.environ.get("NOVA_WEB_ORIGINS", "https://supercoder592.github.io").split(",") if o.strip()}

app = FastAPI(title="N.O.V.A.")


@app.middleware("http")
async def no_cache_static(request: Request, call_next):
    # 前端檔案更新後瀏覽器要立刻拿到新版
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


def host_allowed(host):
    """擋 DNS rebinding：攻擊者只能用自己註冊的公開網域，所以只放行 IP、localhost / 電腦名稱這類單字主機名、
    .local，以及 NOVA_ALLOWED_HOSTS 列出的網域。"""
    try:
        name = (urlsplit(f"//{host}").hostname or "").rstrip(".")
    except ValueError:
        return False
    if "*" in EXTRA_HOSTS or name in EXTRA_HOSTS or "." not in name or name.endswith((".local", ".localhost")):
        return True
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        return False


def cors_headers(origin):
    """GitHub Pages 上的介面連到本機：CORS + Chrome「存取本機網路」需要的標頭。"""
    return {"Access-Control-Allow-Origin": origin, "Vary": "Origin",
            "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Allow-Private-Network": "true", "Access-Control-Max-Age": "600"}


@app.middleware("http")
async def guard(request: Request, call_next):
    """本機服務的基本防護：DNS rebinding、跨站請求偽造（CSRF）、過大的請求。"""
    host = request.headers.get("host", "")
    if not host_allowed(host):
        return JSONResponse({"error": "forbidden host（可用環境變數 NOVA_ALLOWED_HOSTS 加入允許的網域）"},
                            status_code=403)
    origin = request.headers.get("origin")
    web = origin is not None and origin.lower().rstrip("/") in WEB_ORIGINS
    if web and request.method == "OPTIONS":
        return JSONResponse(None, status_code=204, headers=cors_headers(origin))
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        # 瀏覽器的跨站 POST（包括不需要 preflight 的 text/plain）一定帶 Origin；
        # 同源或用區網 IP 連進來時 Origin 的主機就是 Host。curl / 腳本沒有 Origin，放行。
        if origin is not None and not web:
            try:
                netloc = urlsplit(origin).netloc.lower()
            except ValueError:
                netloc = ""
            if not netloc or (netloc != host.lower() and origin.lower().rstrip("/") not in LOCAL_ORIGINS):
                return JSONResponse({"error": "cross-origin request blocked"}, status_code=403)
        elif not web and request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"error": "cross-origin request blocked"}, status_code=403)
        size = request.headers.get("content-length", "")
        if size.isdigit() and int(size) > MAX_BODY:
            return JSONResponse({"error": "request body too large"}, status_code=413,
                                headers=cors_headers(origin) if web else None)
    response = await call_next(request)
    if web:
        response.headers.update(cors_headers(origin))
    return response


@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, exc: StarletteHTTPException):
    # 錯誤一律回 JSON，同時給 error 與 detail 兩個欄位
    return JSONResponse({"error": exc.detail, "detail": exc.detail}, status_code=exc.status_code,
                        headers=getattr(exc, "headers", None))


async def json_body(req):
    """讀取 JSON 物件；格式錯誤回 400，不要變成 500。"""
    try:
        data = await req.json()
    except ValueError:  # JSONDecodeError 與 UnicodeDecodeError 都是 ValueError
        raise HTTPException(400, "invalid JSON body")
    if not isinstance(data, dict):
        raise HTTPException(400, "body must be a JSON object")
    return data


SYSTEM_PROMPTS = {
    "chat": "你是 N.O.V.A.（Neural Omni Virtual Assistant），一個完全運行在使用者電腦上的未來科技 AI 助理，"
            "風格冷靜、聰明、專業，偶爾帶一點英式幽默，像鋼鐵人的 J.A.R.V.I.S.。預設使用繁體中文回答，除非使用者用別的語言。"
            "回答精準、有條理，適當使用 Markdown。"
            "你所在的聊天室同時具備這些能力（由系統自動處理，使用者直接說出需求即可）："
            "畫圖（例：「畫一隻太空貓」）、做影片（「把剛剛那張圖做成影片」）、作曲（「做一首 lofi 音樂」）、"
            "3D 建模（「做一個 3D 太空船模型」）、上網找素材（「找一些星空的圖片素材」）、寫程式與網頁、即時上網查資料。"
            "對話紀錄中以全形括號開頭、寫著已生成或已搜尋的訊息，是系統自動寫入的作品紀錄，不是你說的話；"
            "回覆時絕對不要輸出這種紀錄標記。",
    "code": "你是 N.O.V.A. 的程式核心，一位頂尖的全端軟體工程師。給出完整、可直接執行的程式碼，"
            "並用 ```語言 標記程式碼區塊。如果使用者要網頁、小遊戲或互動效果，請輸出單一完整的 HTML 檔"
            "（CSS 與 JS 內嵌），讓它可以直接預覽。先簡短說明思路，再給程式碼，最後說明如何使用。"
            "預設用繁體中文解說。",
}

SEP = r"[^，。,.!?！？\n]"  # 同一句內
# 「現在 / 目前」只有接著時事名詞才上網，「我現在好無聊」這種閒聊不用花 20 秒搜尋
WEB_HINTS = re.compile(
    r"(最新|今日|昨天|新聞|搜尋|查一下|查詢|上網|找一下|幫我找|價格|股價|天氣|匯率|比分|發布|"
    r"(現在|目前|今天|最近)" + SEP + r"{0,6}(價格|股價|天氣|匯率|新聞|比分|狀況|情況|排名|趨勢|版本|賽程|疫情)|"
    r"\b(latest|today|news|search|price|weather|current)\b)", re.I)


# ---------------------------------------------------------------- ollama
async def ollama_models():
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            r = await c.get(f"{OLLAMA}/api/tags")
            return [m["name"] for m in r.json().get("models", [])]
    except Exception:
        return None


async def pick_model(requested=None):
    models = await ollama_models() or []
    if requested and requested in models:
        return requested
    if DEFAULT_MODEL and DEFAULT_MODEL in models:
        return DEFAULT_MODEL
    return models[0] if models else None


def ollama_once(prompt, model, system=None, timeout=120):
    body = {"model": model, "prompt": prompt, "stream": False, "options": {"temperature": 0.2}}
    if system:
        body["system"] = system
    r = httpx.post(f"{OLLAMA}/api/generate", json=body, timeout=timeout)
    r.raise_for_status()
    text = r.json().get("response", "")
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


# Ollama 無法翻譯時的備援：常見中文詞直接查表換成英文（底線代表空白），總比把中文丟給只懂英文的模型好
ZH_EN = dict(p.split("=") for p in """
小貓=kitten 貓咪=cat 貓=cat 小狗=puppy 狗=dog 鳥=bird 魚=fish 馬=horse 龍=dragon 老虎=tiger 獅子=lion 熊=bear
兔子=rabbit 狐狸=fox 狼=wolf 鯨魚=whale 海豚=dolphin 蝴蝶=butterfly 恐龍=dinosaur 企鵝=penguin 貓頭鷹=owl 熊貓=panda
女孩=girl 男孩=boy 女人=woman 男人=man 小孩=child 老人=old_man 機器人=robot 太空人=astronaut 武士=samurai 騎士=knight
公主=princess 魔法師=wizard 忍者=ninja 天使=angel 惡魔=demon 精靈=elf 怪物=monster 外星人=alien
海浪=ocean_waves 海灘=beach 沙灘=beach 海邊=seaside 海=sea 山=mountain 森林=forest 樹=tree 花=flowers 櫻花=cherry_blossoms
玫瑰=rose 草原=meadow 沙漠=desert 雪=snow 雨=rain 河=river 湖=lake 瀑布=waterfall 天空=sky 雲=clouds 星空=starry_sky
星星=stars 月亮=moon 太陽=sun 夕陽=sunset 日落=sunset 日出=sunrise 夜景=night_view 夜晚=night 城市=city 街道=street
城堡=castle 房子=house 太空=outer_space 宇宙=universe 星球=planet 火山=volcano 島=island 極光=aurora 彩虹=rainbow
汽車=car 跑車=sports_car 車=car 飛機=airplane 太空船=spaceship 船=ship 火車=train 劍=sword 椅子=chair 杯子=cup
咖啡=coffee 蛋糕=cake 書=book 燈塔=lighthouse 花園=garden 教堂=church 寺廟=temple 橋=bridge
賽博龐克=cyberpunk 蒸汽龐克=steampunk 水彩=watercolor 油畫=oil_painting 素描=pencil_sketch 動漫=anime 卡通=cartoon
像素=pixel_art 寫實=photorealistic 可愛=cute 奇幻=fantasy 科幻=sci-fi 復古=retro 未來=futuristic 黑白=black_and_white
夢幻=dreamy 霓虹=neon 極簡=minimalist 史詩=epic
紅色=red 藍色=blue 綠色=green 黃色=yellow 紫色=purple 粉紅色=pink 黑色=black 白色=white 金色=golden 橘色=orange
鋼琴=piano 吉他=guitar 小提琴=violin 大提琴=cello 鼓=drums 爵士=jazz 搖滾=rock 古典=classical 電子=electronic
嘻哈=hip_hop 輕快=upbeat 悲傷=sad 放鬆=relaxing 快樂=happy 浪漫=romantic 抒情=ballad 緊張=tense 療癒=healing
跳舞=dancing 飛翔=flying 奔跑=running 游泳=swimming 睡覺=sleeping 下雨=raining 下雪=snowing
""".split())
ZH_MAX = max(map(len, ZH_EN))


def glossary_prompt(text):
    """查表把中文提示詞粗略轉成英文關鍵字，英數字原樣保留；一個詞都對不到就回傳原文。"""
    words = []
    for run in re.findall(r"[A-Za-z0-9][A-Za-z0-9 '.-]*|[^\x00-\x7f]+", text):
        if run.isascii():
            words.append(run.strip())
            continue
        i = 0
        while i < len(run):
            for n in range(min(ZH_MAX, len(run) - i), 0, -1):
                if run[i:i + n] in ZH_EN:
                    words.append(ZH_EN[run[i:i + n]].replace("_", " "))
                    i += n
                    break
            else:
                i += 1
    return ", ".join(dict.fromkeys(w for w in words if w)) or text


def to_english_prompt(text, kind, model=None):
    """生成模型只懂英文：中文提示詞先交給本機 LLM 翻譯並擴寫。回傳 (英文提示詞, 是否翻譯成功)。"""
    if text.isascii():
        return text, True
    try:
        names = [m["name"] for m in httpx.get(f"{OLLAMA}/api/tags", timeout=3).json().get("models", [])]
        model = model if model in names else (DEFAULT_MODEL if DEFAULT_MODEL in names else (names[0] if names else None))
        if not model:
            return glossary_prompt(text), False
        guide = {
            "image": "an English prompt for an image generator, under 50 words (subject, style, lighting, quality words)",
            # LTX-Video 喜歡長而具體的描述：主要動作 → 細節動作 → 外觀 → 背景 → 鏡頭運動 → 光線
            "video": ("ONE English paragraph of 60-100 words for a text-to-video model: start with the main action, "
                      "then specific movements, appearance, background, camera angle and camera movement, lighting; "
                      "literal and chronological"),
            "music": "a short English music description (genre, mood, instruments, tempo)",
            "model3d": "a short English description of a single 3D object (e.g. 'a red sports car')",
            "search": "a concise English web image search query",
        }.get(kind, "a short English prompt")
        out = ollama_once(f"Convert the following request into {guide}. Reply with ONLY the English text, "
                          f"no quotes, no explanation.\n\nRequest: {text}", model)
        lines = [ln.strip().strip('"') for ln in out.strip().splitlines() if ln.strip()]
        en = lines[0][:400] if lines else ""
        # 模型偶爾不聽話、回了中文：那也不能直接丟給生成模型
        if en and sum(ch.isascii() for ch in en) >= 0.8 * len(en):
            return en, True
    except Exception:
        pass
    return glossary_prompt(text), False


async def llm_classify(text, model):
    """規則判斷有衝突時，請本機 LLM 裁決意圖。"""
    try:
        async with httpx.AsyncClient(timeout=45) as c:
            r = await c.post(f"{OLLAMA}/api/generate", json={
                "model": model, "prompt": router.classify_prompt(text), "stream": False,
                "options": {"temperature": 0, "num_predict": 6}})
            return router.parse_label(r.json().get("response", ""), None)
    except Exception:
        return None


ACK = {
    "image": "收到，正在為你**繪製圖像**。完成後會直接顯示在這裡。",
    "video": "收到，正在**合成影片**，需要一兩分鐘，請稍候。",
    "music": "收到，正在**作曲**。CPU 作曲需要一點時間，完成後可以直接播放。",
    "model3d": "收到，正在**建構 3D 模型**，完成後可以直接旋轉檢視。",
}
COSTLY = ("video", "music", "model3d")  # CPU 上要跑好幾分鐘的生成
CMD = {"image": "/畫", "video": "/影片", "music": "/音樂", "model3d": "/3d", "search": "/找"}
ASK = {  # 只打了指令沒寫內容時的回覆
    "image": ("要畫什麼呢？", "一隻太空貓"), "video": ("要做什麼樣的影片呢？", "海浪拍打礁石"),
    "music": ("要做什麼樣的音樂呢？", "輕快的 lofi 鋼琴"), "model3d": ("要建什麼 3D 模型呢？", "一艘太空船"),
    "search": ("要找什麼素材呢？", "星空 圖片"),
}


def job_params(intent, prompt, data):
    """把聊天室的設定轉成生成任務參數。"""
    opts = data.get("opts")
    opts = opts.get(intent) if isinstance(opts, dict) else None
    opts = opts if isinstance(opts, dict) else {}
    params = {"prompt": prompt}
    if intent == "image":
        params.update({k: opts[k] for k in ("width", "height", "steps", "strength", "web_ref") if k in opts})
    elif intent == "video":
        params.update({k: opts[k] for k in ("quality", "length", "engine", "web_ref") if k in opts})
        # 舊版介面送的是 size / frames：換算成畫質與長度
        if "quality" not in params and str(opts.get("size", "")).isdigit():
            params["quality"] = {256: "fast", 384: "standard"}.get(int(opts["size"]), "high")
        if "length" not in params and str(opts.get("frames", "")).isdigit():
            params["length"] = "2s" if int(opts["frames"]) <= 16 else "4s"
    elif intent == "music":
        params.update({k: opts[k] for k in ("duration",) if k in opts})
    elif intent == "model3d":
        params.update({k: opts[k] for k in ("steps",) if k in opts})
    return params


def norm_message(m):
    """容忍各種格式的訊息：content 可能是 null、數字或 [{type,text}] 片段。"""
    c = m.get("content")
    if isinstance(c, list):
        c = " ".join(str(p.get("text") or "") for p in c if isinstance(p, dict))
    imgs = m.get("images")
    return {"role": m.get("role") if m.get("role") in ("user", "assistant", "system") else "user",
            "content": c if isinstance(c, str) else ("" if c is None else str(c)),
            "images": [i for i in imgs if isinstance(i, str)] if isinstance(imgs, list) else None}


def emit(obj):
    return json.dumps(obj, ensure_ascii=False) + "\n"


async def arbitrate(text, first, model, has_image, has_prev):
    """規則有衝突時請 LLM 裁決，但只能在規則候選裡選。搜尋 / 對話便宜又可逆，長時間生成會佔住唯一的工作佇列，
    所以 LLM 的猜測不能把規則的首選升級成生成任務。回傳 (意圖, 被否決的生成意圖)。"""
    cands = router.candidates(text, has_image, has_prev=has_prev) or [first]
    label = await llm_classify(text[:1000], model)
    if label not in cands or label == first:
        return first, None
    if label in COSTLY or (label in ACK and first not in ACK):
        return first, label
    return label, None


async def chat_events(data, messages, context, model, web):
    last_msg = next((m for m in reversed(messages) if m["role"] == "user"), None)
    last = last_msg["content"] if last_msg else ""
    attachments = (last_msg or {}).get("images") or []
    if not last.strip() and not attachments:
        yield emit({"type": "error", "content": "沒有收到任何訊息內容。"})
        return
    route_text = last[:ROUTE_CHARS]
    ref = context.get("reference") if isinstance(context.get("reference"), str) else None
    last_image = context.get("last_image") if isinstance(context.get("last_image"), str) else None

    cmd, rest = router.parse_command(route_text)
    forced, vetoed = data.get("force"), None
    if isinstance(forced, str) and forced in router.INTENTS:
        intent, prompt = forced, (rest if cmd else route_text)
    elif cmd:
        intent, prompt = cmd, rest  # 指令後面沒寫東西就是空字串，不能把「/畫」本身當提示詞
    else:
        intent, prompt, sure = router.detect(route_text, bool(attachments), has_prev=bool(last_image))
        if not sure and model:
            yield emit({"type": "status", "content": "判斷指令中…"})
            intent, vetoed = await arbitrate(route_text, intent, model, bool(attachments), bool(last_image))
    yield emit({"type": "intent", "content": intent, "label": router.LABELS[intent]})
    hint = (f"\n\n（如果你其實是想生成{router.LABELS[vetoed]}，可以輸入「{CMD[vetoed]} …」或用上方的意圖按鈕指定。）"
            if vetoed else "")

    # ---------------- 只打指令沒寫內容：先問清楚，不要拿空白提示詞跑好幾分鐘
    if intent in ACK or intent == "search":
        use_prev = bool(last_image) and router.wants_previous(route_text)
        has_ref = intent in ("image", "video") and bool(attachments or ref or use_prev)
        clean = router.clean_prompt(prompt).strip() if prompt.strip() else ""
        if not clean and not has_ref:
            q, example = ASK[intent]
            yield emit({"type": "token", "content": f"{q}請在指令後面描述內容，例如「{CMD[intent]} {example}」。"})
            yield emit({"type": "done"})
            return

    # ---------------- 生成任務：圖片 / 影片 / 音樂 / 3D
    if intent in ACK:
        params = job_params(intent, clean, data)
        params["llm"] = model  # 翻譯提示詞用使用者選的模型
        note = ""
        if intent in ("image", "video"):
            if attachments:
                params["ref_data"] = attachments[0]
                note = "（以你附上的圖片為基礎）"
            elif ref:
                params["ref_url"] = ref
                if isinstance(context.get("reference_thumb"), str):
                    params["ref_thumb"] = context["reference_thumb"]  # 原圖抓不到時改用縮圖
                note = "（使用你選的參考素材）"
            elif use_prev:
                params["ref_url"] = last_image
                note = "（以剛剛那張圖為基礎）"
            elif params.get("web_ref"):
                note = "（會先上網找參考素材）"
        try:
            job = enqueue_job(intent, params)
        except QueueFull as e:
            yield emit({"type": "error", "content": f"目前已有 {e} 個生成任務在排隊，請等前面的完成或取消後再試。"})
            return
        yield emit({"type": "token", "content": ACK[intent] + note + hint})
        yield emit({"type": "job", "content": job})
        yield emit({"type": "done"})
        return

    # ---------------- 素材搜尋
    if intent == "search":
        q, kind = router.search_query(prompt)[:500], router.search_kind(prompt)
        what = "影片" if kind == "videos" else "圖片"
        yield emit({"type": "token", "content": f"正在網路上搜尋「{q}」的{what}素材…"})
        n = 30 if kind == "images" else 12
        # 中文關鍵字常撞到同名品牌或廣告（例如「星空」→ 星空體育）：先請本機 LLM 翻成英文來搜，
        # 英文結果太少時才補上中文結果
        queries = [q]
        if not q.isascii() and model:
            en, ok = await asyncio.to_thread(to_english_prompt, q, "search", model)
            if ok and en.lower() != q.lower():
                queries = [en, q]
        results, seen, errors = [], set(), []
        for x in queries:
            try:
                rows = await asyncio.to_thread(websearch.search, x, kind, n)
            except Exception as e:
                if str(e).strip() != "No results found.":
                    errors.append(e)
                rows = []
            for r in rows:
                key = r.get("image") or r.get("url")
                if key not in seen:
                    seen.add(key)
                    results.append(r)
            if len(results) >= min(8, n):
                break
        if not results and errors:
            yield emit({"type": "error", "content": f"搜尋失敗：{str(errors[0])[:300]}"})
            return
        results = results[:n]
        if len(queries) > 1:
            q = f"{q}（{queries[0]}）"
        yield emit({"type": "search", "content": {"kind": kind, "query": q, "results": results}})
        if hint:
            yield emit({"type": "token", "content": hint})
        yield emit({"type": "done"})
        return

    # ---------------- 對話 / 程式
    if not model:
        yield emit({"type": "error", "content": "找不到本機 Ollama 模型。請確認 Ollama 已啟動，並執行 "
                                                "`ollama pull qwen3-vl:4b-instruct` 下載模型。"})
        return
    yield emit({"type": "model", "content": model})

    system = SYSTEM_PROMPTS["code" if intent == "code" else "chat"]
    system += f"\n\n現在時間：{time.strftime('%Y-%m-%d %H:%M')}。"
    use_web = web is True or (web == "auto" and bool(WEB_HINTS.search(route_text)))
    if use_web and last.strip():
        yield emit({"type": "status", "content": "正在上網搜尋資料…"})
        try:
            results = await asyncio.wait_for(asyncio.to_thread(websearch.research, route_text[:500], 4), 25)
        except Exception as e:
            results = []
            yield emit({"type": "status", "content": f"搜尋失敗：{str(e)[:200] or type(e).__name__}"})
        if results:
            yield emit({"type": "sources", "content": [{"title": r["title"], "url": r["url"]} for r in results]})
            ctx = "\n\n".join(f"[{i + 1}] {r['title']} ({r['url']})\n{r['content']}"
                              for i, r in enumerate(results))
            system += f"以下是剛剛即時上網搜尋到的資料，請根據它回答並用 [1]、[2] 標註引用來源：\n\n{ctx}"

    msgs = [{"role": "system", "content": system}]
    for m in messages:
        item = {"role": m["role"], "content": m["content"]}
        if m["images"] and m is last_msg:
            item["images"] = [i.split(",", 1)[-1] for i in m["images"]]
        msgs.append(item)

    yield emit({"type": "status", "content": "思考中…"})
    try:
        # CPU 上第一個 token 可能要好幾分鐘，read 逾時給寬；但不能無限等
        async with httpx.AsyncClient(timeout=httpx.Timeout(connect=5, read=300, write=30, pool=5)) as c:
            async with c.stream("POST", f"{OLLAMA}/api/chat",
                                json={"model": model, "messages": msgs, "stream": True,
                                      "options": {"num_ctx": 8192}}) as r:
                if r.status_code != 200:
                    body = (await r.aread()).decode(errors="ignore")
                    yield emit({"type": "error", "content": f"模型錯誤：{body[:300]}"})
                    return
                async for line in r.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line)
                    except ValueError:
                        continue
                    if chunk.get("error"):  # 模型中途失敗時 Ollama 會在串流裡送 {"error": ...}
                        yield emit({"type": "error", "content": f"模型錯誤：{str(chunk['error'])[:300]}"})
                        return
                    msg = chunk.get("message") or {}
                    if msg.get("content"):
                        yield emit({"type": "token", "content": msg["content"]})
                    if chunk.get("done"):
                        break
    except httpx.TimeoutException:
        yield emit({"type": "error", "content": "模型回應逾時（超過 5 分鐘沒有任何輸出），請稍後再試。"})
        return
    except Exception as e:
        yield emit({"type": "error", "content": f"連線 Ollama 失敗：{str(e)[:300]}"})
        return
    if hint:
        yield emit({"type": "token", "content": hint})
    yield emit({"type": "done"})


@app.post("/api/chat")
async def chat(req: Request):
    """單一聊天室入口：自動判斷要聊天、寫程式、生成圖片/影片/音樂/3D，還是上網找素材。"""
    data = await json_body(req)
    raw = data.get("messages")
    messages = [norm_message(m) for m in (raw if isinstance(raw, list) else []) if isinstance(m, dict)][-16:]
    context = data.get("context") if isinstance(data.get("context"), dict) else {}
    requested = data.get("model")
    model = await pick_model(requested if isinstance(requested, str) else None)
    web = data.get("web", "auto")

    async def stream():
        # 串流開始後任何意外都要變成 error 事件，前端才不會一直轉圈
        try:
            async for ev in chat_events(data, messages, context, model, web):
                yield ev
        except Exception as e:
            traceback.print_exc()
            yield emit({"type": "error", "content": f"內部錯誤：{type(e).__name__}"})

    return StreamingResponse(stream(), media_type="application/x-ndjson")


# ---------------------------------------------------------------- search
@app.post("/api/search")
async def search(req: Request):
    data = await json_body(req)
    q = str(data.get("q") or "").strip()[:500]
    kind = data.get("kind", "text")
    if not q:
        raise HTTPException(400, "empty query")
    if kind not in ("text", "images", "videos", "news"):
        raise HTTPException(400, "unknown kind")
    try:
        n = max(1, min(50, int(data.get("n", 20))))
    except (TypeError, ValueError):
        raise HTTPException(400, "n must be an integer")
    try:
        results = await asyncio.to_thread(websearch.search, q, kind, n)
    except Exception as e:
        if str(e).strip() == "No results found.":
            return {"results": []}
        return JSONResponse({"error": str(e)[:300], "results": []}, status_code=502)
    return {"results": results}


@app.post("/api/assets/save")
async def save_asset(req: Request):
    data = await json_body(req)
    url = data.get("url")
    if not isinstance(url, str) or not url.startswith(("http://", "https://")):
        raise HTTPException(400, "url must be http(s)")
    try:
        url, _ = await asyncio.to_thread(websearch.save_asset, url)
    except Exception as e:
        return JSONResponse({"error": str(e)[:300]}, status_code=502)
    return {"url": url}


# ---------------------------------------------------------------- jobs
JOBS = {}
JOB_QUEUE = queue.Queue()
JOB_LOCK = threading.Lock()
FINAL = ("done", "error", "cancelled")
ENGINE = {"state": "idle"}  # idle → loading → ready / error


class QueueFull(Exception):
    pass


def load_engines():
    """匯入生成引擎（torch / diffusers 很大，第一次要十幾秒）。/api/status 會看 ENGINE 顯示狀態。"""
    if ENGINE["state"] != "ready":
        ENGINE["state"] = "loading"
    try:
        import engines
    except Exception:
        ENGINE["state"] = "error"
        raise
    ENGINE["state"] = "ready"
    return engines


def check_cancel(job):
    if job.get("cancel"):
        raise RuntimeError("已取消")


def decode_image(data):
    import base64
    import io
    from PIL import Image

    return Image.open(io.BytesIO(base64.b64decode(data.split(",", 1)[-1]))).convert("RGB")


def load_reference(u):
    """參考素材：本機 /outputs/ 檔案（不能跳出 outputs 資料夾）、data: URL，或 http(s) 網址。"""
    if u.startswith("/outputs/"):
        from PIL import Image

        out = os.path.realpath(os.path.join(BASE, "outputs"))
        p = os.path.realpath(os.path.join(BASE, unquote(u.split("?", 1)[0]).lstrip("/").replace("/", os.sep)))
        try:
            inside = os.path.commonpath([p, out]) == out
        except ValueError:  # 不同磁碟機
            inside = False
        if not inside:
            raise ValueError("reference outside outputs/")
        return Image.open(p).convert("RGB")
    if u.startswith("data:image"):
        return decode_image(u)
    return websearch.download_image(u)  # 只接受 http(s)


def run_job(job, params):
    job["message"] = "理解指令中…" if ENGINE["state"] == "ready" else "載入生成引擎…"
    engines = load_engines()
    check_cancel(job)

    kind = job["kind"]
    prompt = str(params.get("prompt") or "").strip()
    job["message"] = "理解指令中…"
    en, ok = to_english_prompt(prompt, kind, params.get("llm"))
    job["prompt_en"] = en
    warnings = [] if ok else ["翻譯模型無法使用，已用內建詞彙轉換提示詞，結果可能不準"]
    check_cancel(job)

    # 參考素材載入失敗不該讓整個任務失敗：改成直接依文字生成，並提醒使用者
    reference, err = None, None
    ref_url = params.get("ref_url")
    if params.get("ref_data"):
        try:
            reference = decode_image(str(params["ref_data"]))
            job["reference"] = {"title": "上傳的圖片"}
        except Exception as e:
            err = e
    elif ref_url:
        job["message"] = "下載參考素材…"
        for u in (ref_url, params.get("ref_thumb")):
            if not isinstance(u, str) or not u:
                continue
            try:
                reference = load_reference(u)
                job["reference"] = {"url": u}
                break
            except Exception as e:
                err = e
    elif params.get("web_ref") and kind in ("image", "video"):
        job["message"] = "上網尋找參考素材…"
        try:
            reference, info = websearch.find_reference_image(" ".join(en.replace(",", " ").split()[:10]))
            if info:
                job["reference"] = {"url": info.get("thumbnail") or info.get("image"), "source": info.get("url"),
                                    "title": info.get("title")}
            else:
                warnings.append("網路上找不到可用的參考素材，改為直接依文字生成")
        except Exception as e:
            err = e
    if reference is None and err is not None:
        warnings.append(f"參考素材無法載入（{type(err).__name__}），改為直接依文字生成")
    if warnings:
        job["warning"] = "；".join(warnings)
        job["message"] = "⚠ " + job["warning"]
    check_cancel(job)

    if kind == "image":
        return engines.gen_image(job, en, params.get("width", 512), params.get("height", 512),
                                 params.get("steps", 1), params.get("seed"), reference,
                                 params.get("strength", 0.55))
    if kind == "video":
        return engines.gen_video(job, en, str(params.get("quality", "auto")), str(params.get("length", "auto")),
                                 reference, str(params.get("engine", "auto")))
    if kind == "music":
        return engines.gen_music(job, en, params.get("duration", 8))
    if kind == "model3d":
        return engines.gen_3d(job, en, params.get("steps", 32))
    raise ValueError(f"unknown job kind {kind}")


def worker():
    while True:
        job, params = JOB_QUEUE.get()
        try:
            with JOB_LOCK:
                if job["status"] != "queued":  # 排隊時就被取消了
                    continue
                job["status"] = "running"
            t0 = time.time()
            try:
                job["result"] = run_job(job, params)
                check_cancel(job)  # 取消時剛好在最後輸出階段：檔案留著，但照使用者的意思標成已取消
                warn = f" · ⚠ {job['warning']}" if job["warning"] else ""
                job.update(progress=1.0, message=f"完成（{time.time() - t0:.0f} 秒）{warn}", status="done")
            except Exception as e:
                if job.get("cancel"):
                    job.update(status="cancelled", message="已取消")
                else:
                    traceback.print_exc()
                    job.update(status="error", error=f"{type(e).__name__}: {str(e)[:300]}")
        finally:
            job["finished"] = time.time()
            JOB_QUEUE.task_done()


threading.Thread(target=worker, daemon=True).start()


def preload():
    try:
        load_engines()
    except Exception:
        traceback.print_exc()


# 預設不預載（torch 會多吃幾百 MB 記憶體）；設 NOVA_PRELOAD=1 可省掉第一次生成時的十幾秒匯入
if os.environ.get("NOVA_PRELOAD") == "1":
    threading.Thread(target=preload, daemon=True).start()


@app.post("/api/jobs/{kind}")
async def create_job(kind: str, req: Request):
    if kind not in ("image", "video", "music", "model3d"):
        raise HTTPException(404)
    params = await json_body(req)
    if not str(params.get("prompt") or "").strip():
        raise HTTPException(400, "empty prompt")
    try:
        return enqueue_job(kind, params)
    except QueueFull as e:
        raise HTTPException(429, f"目前已有 {e} 個生成任務在排隊，請稍後再試")


def enqueue_job(kind, params):
    with JOB_LOCK:
        active = [j for j in JOBS.values() if j["status"] in ("queued", "running")]
        queued = sum(j["status"] == "queued" for j in active)
        if queued >= MAX_QUEUED:
            raise QueueFull(queued)
        # 所有欄位先建好：工作執行緒只改值不加欄位，/api/jobs 序列化時才不會撞到 dict 變大小
        job = {"id": uuid.uuid4().hex, "kind": kind, "status": "queued", "progress": 0.0,
               "message": f"排隊中（前面還有 {len(active)} 個任務）" if active else "準備中…",
               "result": None, "error": None, "warning": None, "prompt": params.get("prompt"), "prompt_en": None,
               "reference": None, "cancel": False, "created": time.time(), "finished": None}
        JOBS[job["id"]] = job
        # 只留最近 KEEP_JOBS 個已結束的任務，伺服器開再久也不會一直長大
        ended = sorted((j for j in JOBS.values() if j["status"] in FINAL), key=lambda j: j["created"])
        for j in ended[:-KEEP_JOBS]:
            JOBS.pop(j["id"], None)
    JOB_QUEUE.put((job, params))
    return dict(job)


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404)
    return dict(job)


@app.post("/api/jobs/{job_id}/cancel")
@app.delete("/api/jobs/{job_id}")
async def cancel_job(job_id: str):
    """取消任務：排隊中的直接標成 cancelled（工作執行緒會跳過）；執行中的在下一個生成步驟中斷。"""
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404)
    with JOB_LOCK:
        if job["status"] in ("queued", "running"):
            job["cancel"] = True
            if job["status"] == "queued":
                job.update(status="cancelled", message="已取消", finished=time.time())
            else:
                job["message"] = "取消中…"
    return {"ok": True, "status": job["status"]}


# ---------------------------------------------------------------- status
@app.get("/api/status")
async def status():
    # engines 可能還在匯入中（半初始化的模組沒有 SLOT），屬性一律防禦性讀取，不能 500
    engine = sys.modules.get("engines")
    slot = getattr(engine, "SLOT", None)
    vm = psutil.virtual_memory()
    jobs = list(JOBS.values())
    return {
        "ollama": await ollama_models(),
        "cpu": psutil.cpu_percent(interval=None),
        "ram": vm.percent,
        "ram_total": round(vm.total / 2**30, 1),
        "device": getattr(engine, "DEVICE", "cpu"),
        "loaded": getattr(slot, "name", None),
        "engine": ENGINE["state"],
        "queue": sum(j["status"] == "queued" for j in jobs),
        "running": sum(j["status"] == "running" for j in jobs),
        "time": time.time(),
    }


@app.get("/api/gallery")
async def gallery():
    out = os.path.join(BASE, "outputs")
    kinds = {".png": "image", ".mp4": "video", ".wav": "audio", ".glb": "model3d"}
    items = []
    for name in sorted(os.listdir(out), reverse=True):
        ext = os.path.splitext(name)[1].lower()
        if ext in kinds and not name.endswith(".poster.jpg"):
            item = {"type": kinds[ext], "url": f"/outputs/{name}"}
            poster = name[:-4] + ".poster.jpg"
            if ext == ".mp4" and os.path.exists(os.path.join(out, poster)):
                item["poster"] = f"/outputs/{poster}"
            items.append(item)
    return {"items": items[:200]}


app.mount("/outputs", StaticFiles(directory=os.path.join(BASE, "outputs")), name="outputs")
app.mount("/static", StaticFiles(directory=os.path.join(BASE, "static")), name="static")


@app.get("/")
async def index():
    # index.html 放在專案根目錄：同一個檔案也直接當 GitHub Pages 的首頁
    return FileResponse(os.path.join(BASE, "index.html"))


if __name__ == "__main__":
    # 預設只給本機用；設 NOVA_HOST=0.0.0.0 就能讓同一個網路的手機 / 其他電腦連進來
    host = os.environ.get("NOVA_HOST", "127.0.0.1")
    print(f"\n  N.O.V.A. online  →  http://localhost:{PORT}\n")
    uvicorn.run(app, host=host, port=PORT, log_level="warning")
