"""免金鑰的網路搜尋：DuckDuckGo（ddgs）+ 直接抓網頁內容。"""
import io
import os
import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, wait
from itertools import zip_longest

import httpx
from bs4 import BeautifulSoup
from PIL import Image

BASE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(BASE, "outputs", "assets")
os.makedirs(ASSETS, exist_ok=True)

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"}


def _ddg(method, query, **kw):
    """呼叫 ddgs；「找不到結果」在 ddgs 裡是例外，這裡統一改成空清單，真正的錯誤才往上丟。"""
    from ddgs import DDGS
    from ddgs.exceptions import DDGSException

    try:
        with DDGS() as d:
            return getattr(d, method)(query, **kw) or []
    except DDGSException as e:
        if str(e).strip() == "No results found.":
            return []
        raise


def search(query, kind="text", max_results=10):
    if kind == "images":
        rows = _ddg("images", query, max_results=max_results)
        return [{"title": r.get("title"), "image": r.get("image"), "thumbnail": r.get("thumbnail"),
                 "url": r.get("url"), "source": r.get("source")} for r in rows]
    if kind == "videos":
        return search_videos(query, max_results)
    if kind == "news":
        rows = _ddg("news", query, max_results=max_results)
        return [{"title": r.get("title"), "url": r.get("url"), "body": r.get("body"),
                 "thumbnail": r.get("image"), "source": r.get("source"), "date": r.get("date")} for r in rows]
    rows = _ddg("text", query, max_results=max_results)
    return [{"title": r.get("title"), "url": r.get("href"), "body": r.get("body")} for r in rows]


# ---------------------------------------------------------------- 影片素材
# DuckDuckGo 的影片端點常常直接回 403（ddgs 只會說 No results found）：
# 改用一般搜尋限定影片網站，從網址取出影片 ID 自己組縮圖，回傳格式與原本相同。
YT_ID = re.compile(r"(?:youtube\.com/(?:watch\?(?:[^#\s]*&)?v=|shorts/|embed/|live/)|youtu\.be/)([\w-]{11})")
PEXELS = re.compile(r"pexels\.com/(?:[\w-]+/)?video/(?:[^/?#]*-)?(\d+)|pexels\.com/(?:[\w-]+/)?search/videos/")
YT_THUMB = "https://i.ytimg.com/vi/{}/hqdefault.jpg"
# (查詢樣板, 搜尋引擎, 影片網址規則, 來源名稱, 縮圖網址)。實測只有部分引擎遵守 site:，而且常被限流，
# 所以分開各查一次（合在同一個 backend 參數裡，其中一個先失敗時另一個的結果會被 ddgs 丟掉）；
# Vimeo 對中文查詢常回不相關甚至成人內容，不用
VIDEO_SITES = [
    ("{q} site:youtube.com", "duckduckgo", YT_ID, "YouTube", YT_THUMB),
    ("{q} site:youtube.com", "yandex", YT_ID, "YouTube", YT_THUMB),
    ("{q} {v} site:youtube.com", "auto", YT_ID, "YouTube", YT_THUMB),
    ("{q} site:pexels.com", "auto", PEXELS, "Pexels",
     "https://images.pexels.com/videos/{0}/free-video-{0}.jpg?auto=compress&cs=tinysrgb&w=480"),
]
TITLE_TAIL = re.compile(r"\s*[-|·]\s*(YouTube|Pexels).*$", re.I | re.S)  # 搜尋引擎常把好幾個標題黏在一起
_DDG_VIDEO = {"off_until": 0.0}  # 影片端點壞掉時先跳過 10 分鐘，不要每次都白等


def _relevant(query):
    """搜尋引擎找不到時會塞隨機結果：標題 / 摘要 / 網址至少要含查詢裡的一個詞（中文看任兩個相連字）。"""
    terms = set()
    for w in re.findall(r"[a-z0-9]{3,}|[^\x00-\x7f]+", query.lower()):
        terms.update([w] if w.isascii() or len(w) < 3 else (w[i:i + 2] for i in range(len(w) - 1)))
    return lambda r: not terms or any(t in f"{r.get('title')} {r.get('body')} {r.get('url')}".lower() for t in terms)


def _ddg_videos(query, n):
    if time.time() < _DDG_VIDEO["off_until"]:
        return []
    try:
        rows = _ddg("videos", query, max_results=n)
    except Exception:
        rows = []
    if not rows:
        _DDG_VIDEO["off_until"] = time.time() + 600
    return [{"title": r.get("title"), "url": r.get("content"), "body": r.get("description"),
             "thumbnail": (r.get("images") or {}).get("medium"), "source": r.get("publisher"),
             "duration": r.get("duration")} for r in rows if r.get("content")]


def _site_videos(query, tpl, backend, pat, source, thumb, n):
    v = "video" if query.isascii() else "影片"
    out = []
    for r in _ddg("text", tpl.format(q=query, v=v), max_results=n, backend=backend):
        url = r.get("href") or ""
        m = pat.search(url)
        if m:
            out.append({"title": TITLE_TAIL.sub("", r.get("title") or "") or url, "url": url, "body": r.get("body"),
                        "thumbnail": thumb.format(m.group(1)) if m.group(1) else None,
                        "source": source, "duration": None})
    return out


def search_videos(query, max_results=12, deadline=12):
    """DDG 影片端點與各影片網站同時搜，最多等 deadline 秒，把拿到的結果交錯合併、去重、濾掉不相關的。"""
    jobs = [(_ddg_videos, (query, max_results))] + [(_site_videos, (query, *s, max_results)) for s in VIDEO_SITES]
    ex = ThreadPoolExecutor(max_workers=len(jobs))
    futs = [ex.submit(fn, *args) for fn, args in jobs]
    done, _ = wait(futs, timeout=deadline)
    ex.shutdown(wait=False, cancel_futures=True)
    lists, errors = [], []
    for f in futs:
        ok = f in done and f.exception() is None
        lists.append(f.result() if ok else [])
        if not ok and f is not futs[0]:
            errors.append(f.exception() if f in done else TimeoutError("video search timed out"))
    # DDG 結果（有片長、發布者）排最前，其餘各站交錯
    merged = lists[0] + [r for group in zip_longest(*lists[1:]) for r in group if r]
    keep, seen, out = _relevant(query), set(), []
    for r in merged:
        m = YT_ID.search(r["url"]) or PEXELS.search(r["url"])
        key = (m and m.group(1)) or r["url"]  # 同一支影片的不同網址（m.youtube / 不同語系的 Pexels）只留一筆
        if key not in seen and keep(r):
            seen.add(key)
            out.append(r)
    if not out and len(errors) == len(VIDEO_SITES):
        raise errors[0]  # 每個網站都真的出錯（不是單純沒結果），讓上層顯示錯誤
    return out[:max_results]


# ---------------------------------------------------------------- 網頁內容
def fetch_text(url, limit=2500):
    try:
        r = httpx.get(url, headers=UA, timeout=8, follow_redirects=True)
        if "html" not in r.headers.get("content-type", ""):
            return ""
        soup = BeautifulSoup(r.text, "html.parser")
        for t in soup(["script", "style", "nav", "footer", "header", "aside", "form", "noscript"]):
            t.decompose()
        text = re.sub(r"\s+", " ", soup.get_text(" ")).strip()
        return text[:limit]
    except Exception:
        return ""


def research(query, n=4, deadline=10):
    """搜尋並同時讀取前幾個網頁，回傳可以塞進 LLM 的上下文；整體最多等 deadline 秒。"""
    results = search(query, "text", max_results=n + 2)[:n]
    if not results:
        return []
    ex = ThreadPoolExecutor(max_workers=len(results))
    futs = {ex.submit(fetch_text, r["url"]): r for r in results}
    done, _ = wait(futs, timeout=deadline)
    ex.shutdown(wait=False, cancel_futures=True)  # 不用 with：它會等所有執行緒結束，deadline 就失效了
    for f, r in futs.items():
        r["content"] = (f.result() if f in done else "") or r.get("body") or ""
    return results


def download_image(url):
    if not str(url).startswith(("http://", "https://")):
        raise ValueError("unsupported image url")
    r = httpx.get(url, headers=UA, timeout=12, follow_redirects=True)
    r.raise_for_status()
    img = Image.open(io.BytesIO(r.content))
    img.load()
    return img.convert("RGB")


def find_reference_image(query, tries=6):
    """上網找一張可用的參考圖。"""
    for r in search(query, "images", max_results=tries):
        for u in (r.get("image"), r.get("thumbnail")):
            if not u:
                continue
            try:
                img = download_image(u)
                if min(img.size) >= 128:
                    return img, r
            except Exception:
                continue
    return None, None


def save_asset(url):
    img = download_image(url)
    name = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}.png"
    img.save(os.path.join(ASSETS, name))
    return f"/outputs/assets/{name}", img
