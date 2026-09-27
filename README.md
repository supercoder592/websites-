# N.O.V.A. — Neural Omni Virtual Assistant

一個在你自己電腦上運作的未來科技風 AI。主畫面是一顆像 J.A.R.V.I.S. 的神經網絡球，
所有功能都在 **同一個聊天室**：直接說出需求，N.O.V.A. 會自動判斷要聊天、寫程式、畫圖、
做影片、作曲、3D 建模，還是上網找素材。

**不接任何雲端 AI API、沒有 token 限制、不用付費。** 所有模型都在本機執行。

## 一個聊天室，全部搞定

| 你說 | N.O.V.A. 會 | 本機引擎 |
|---|---|---|
| 「量子電腦是什麼？」「今天有什麼科技新聞？」 | 對話，需要即時資訊時自動上網並附來源 | Ollama（預設 qwen3-vl:4b） |
| 「寫一個霓虹貪食蛇網頁遊戲」 | 寫程式，HTML / JS 可一鍵 **PREVIEW** | Ollama |
| 「畫一張賽博龐克城市夜景」 | 生成圖片 | SD-Turbo + TAESD |
| 「一個女孩在海邊奔跑」「把剛剛那張圖做成影片」 | 生成 **真動態影片**（文字生影片 / 圖片生影片，第一幀鎖定成你的圖） | LTX-Video 2B（備援：AnimateDiff-Lightning） |
| 「做一首 lofi 放鬆音樂」 | 作曲，播放時神經球跟著節奏脈動 | MusicGen-small |
| 「做一個 3D 太空船模型」 | 生成 3D 模型（GLB）＋全息檢視器 | Shap-E |
| 「找一些星空的圖片素材」 | 上網找圖片 / 影片素材，可存進素材庫或拿來生成 | DuckDuckGo（免金鑰） |

小技巧：
- 附上圖片（📎、貼上或拖進視窗）：「這是什麼？」會看圖回答；「改成動漫風」會以圖生圖；「讓它動起來」會做成影片。
- 說「剛剛那張」「這張圖」會自動拿上一張生成的圖當參考。
- 輸入框上方的快捷鍵（畫圖 / 影片 / 音樂 / 3D / 找素材 / 程式）或 `/畫`、`/影片`、`/音樂`、`/3d`、`/搜`、`/程式` 指令可以強制指定功能。
- 右上角 ⚙ 可以調整生成參數；ARCHIVE 可以瀏覽所有作品。

中文提示詞會先交給本機 LLM 翻成英文，再送給生成模型。

## 線上介面（GitHub Pages）

<https://supercoder592.github.io/websites-/>

GitHub Pages 只能放網頁，AI 模型仍在你自己的電腦上執行：先在電腦上啟動 N.O.V.A.（下方的 `start.bat` / `./start.sh`），
再打開上面的網址，介面會自動連到 `http://127.0.0.1:7860` 的本機 AI 核心。瀏覽器若詢問「存取本機網路裝置」，請按允許。
要連到其他電腦上的核心，可以在網址後面加 `?api=http://位址:7860`。

## 安裝與啟動

### Windows
1. 安裝 [Python 3.10+](https://www.python.org/) 和 [Ollama](https://ollama.com/download)
2. 雙擊 `setup.bat`
3. 雙擊 `start.bat`，瀏覽器會自動打開 <http://localhost:7860>

### macOS（Apple Silicon，例如 Mac mini）/ Linux
1. 安裝 [Ollama](https://ollama.com/download)（打開一次讓它在背景執行）
2. 需要 Python 3.10 以上：`brew install python@3.12`（macOS 內建的 3.9 太舊）
3. 在「終端機」執行：
```bash
git clone https://github.com/supercoder592/websites-.git nova
cd nova
chmod +x setup.sh start.sh start.command
./setup.sh
./start.sh
```
之後只要在 Finder 雙擊 `start.command` 就能啟動（第一次若被擋，按右鍵 →「打開」）。
Mac 會自動使用 Apple Silicon GPU（MPS），NVIDIA 顯卡會自動使用 CUDA，速度比純 CPU 快非常多。

各生成模型會在 **第一次使用時自動下載** 到 `models/`（全部約 13GB），之後可完全離線使用。

## 不需要訓練

這些都是已經訓練好的開源模型，下載後就能用。想要特定畫風，之後可以再加 LoRA 微調，但不是必要的。

## 速度參考（i5-1335U 筆電、無獨顯、16GB RAM）

| 任務 | 大約時間 |
|---|---|
| 聊天 | 每秒數個字 |
| 圖片 512×512 | 15–30 秒 |
| 真動態影片 LTX-Video（CPU 預設 384×256、2 秒 24fps，輸出 768×512） | 約 5–30 分鐘（視 CPU 負載；新提示詞另需 1–5 分鐘編碼） |
| 音樂 5–10 秒 | 1–3 分鐘 |
| 3D 模型 | 數分鐘以上 |

影片預設會依硬體自動調整：CPU 用「快速 / 2 秒」，Mac（MPS）與 NVIDIA 顯卡用「高畫質 / 4 秒」。
⚙ 設定裡可以改用 AnimateDiff（較快）；LTX-Video 失敗或記憶體不足時也會自動改用它。
LTX-Video 0.9.8 採用 LTXV Open Weights 授權（年營收 1,000 萬美元以下可免費使用）。

## 快捷鍵

- `Enter` 送出、`Shift+Enter` 換行、`/` 聚焦輸入框
- `Alt+0` 自動、`Alt+1` ~ `Alt+6` 強制指定功能
- `Esc` 收合 / 展開聊天面板

## 進階設定（環境變數）

| 變數 | 預設 | 用途 |
|---|---|---|
| `NOVA_MODEL` | 第一個 Ollama 模型 | 指定聊天模型，例如 `qwen2.5:14b` |
| `NOVA_IMAGE_MODEL` | `stabilityai/sd-turbo` | 換成其他 diffusers 圖片模型 |
| `NOVA_VIDEO_ENGINE` | `ltx` | 影片引擎：`ltx` 或 `animatediff` |
| `NOVA_VIDEO_BASE` | `emilianJR/epiCRealism` | AnimateDiff 用的 SD1.5 底模（可換動漫風格模型） |
| `NOVA_WEB_ORIGINS` | `https://supercoder592.github.io` | 允許連到本機核心的網頁來源（GitHub Pages） |
| `NOVA_THREADS` | 8 | CPU 執行緒數 |
| `NOVA_FAST_VAE` | CPU 上為 1 | 設為 0 改用原始 VAE（較慢、畫質稍好） |
| `NOVA_HOST` | `127.0.0.1` | 設為 `0.0.0.0` 讓同網路的手機 / 電腦連線 |
| `NOVA_PORT` | 7860 | 網頁埠號 |

## 專案結構

```
server.py       FastAPI 伺服器：單一聊天室入口、任務佇列、搜尋
router.py       意圖判斷（規則優先，衝突時交給本機 LLM 裁決）
engines.py      圖片 / 影片 / 音樂 / 3D 生成（自動偵測 CUDA / MPS / CPU）
websearch.py    DuckDuckGo 搜尋、網頁讀取、素材下載
static/         前端（Three.js 神經網絡球 + HUD 聊天室）
outputs/        生成結果與素材庫
models/         自動下載的模型權重
```
