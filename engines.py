"""N.O.V.A. 本機生成引擎：圖片 / 影片 / 音樂 / 3D。

所有模型都在本機執行，第一次使用時會自動從 Hugging Face 下載權重到 ./models，
之後完全離線可用，沒有任何 API / token 限制。
"""
import gc
import os
import threading
import time
import uuid

os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")  # Mac 上少數不支援的運算自動退回 CPU

import numpy as np
import torch
from PIL import Image

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "outputs")
os.makedirs(OUT, exist_ok=True)

# NVIDIA 顯卡 → CUDA；Apple Silicon（Mac mini 等）→ MPS；都沒有 → CPU
if torch.cuda.is_available():
    DEVICE = "cuda"
elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
    DEVICE = "mps"
else:
    DEVICE = "cpu"
DTYPE = torch.float16 if DEVICE in ("cuda", "mps") else torch.float32
if DEVICE == "cpu":
    # 實測筆電 CPU 開 8 條以上反而變慢（大小核 + 過熱降頻），也留點資源給 Ollama 和網頁
    torch.set_num_threads(int(os.environ.get("NOVA_THREADS", min(8, max(1, (os.cpu_count() or 4) - 2)))))
# CPU 上改用 TAESD 小型解碼器：畫質幾乎一樣，速度快約 3 倍
FAST_VAE = os.environ.get("NOVA_FAST_VAE", "1" if DEVICE == "cpu" else "0") == "1"

IMAGE_MODEL = os.environ.get("NOVA_IMAGE_MODEL", "stabilityai/sd-turbo")
VIDEO_BASE = os.environ.get("NOVA_VIDEO_BASE", "emilianJR/epiCRealism")
VIDEO_MOTION = ("ByteDance/AnimateDiff-Lightning", "animatediff_lightning_4step_diffusers.safetensors")
MUSIC_MODEL = os.environ.get("NOVA_MUSIC_MODEL", "facebook/musicgen-small")
SHAPE_MODEL = os.environ.get("NOVA_3D_MODEL", "openai/shap-e")


def new_path(ext):
    name = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}.{ext}"
    return os.path.join(OUT, name), f"/outputs/{name}"


# ---------------------------------------------------------------- model slot
class ModelSlot:
    """同一時間只留一個大型模型在記憶體裡，16GB RAM 才撐得住。"""

    def __init__(self):
        self.name = None
        self.obj = None
        self.lock = threading.Lock()

    def get(self, name, loader, job=None):
        with self.lock:
            if self.name != name:
                self.unload()
                if job:
                    job["message"] = f"載入模型 {name}（第一次會下載，請耐心等候）…"
                self.obj = loader()
                self.name = name
            return self.obj

    def unload(self):
        self.obj = None
        self.name = None
        gc.collect()
        if DEVICE == "cuda":
            torch.cuda.empty_cache()


SLOT = ModelSlot()


def hook_progress(pipe, job, start, end, label):
    """把 diffusers 的 progress_bar 換成更新 job 進度；使用者取消時在這裡中斷生成。"""

    def check_cancel():
        if job.get("cancel"):
            raise RuntimeError("已取消")

    class Bar:
        # diffusers 會把它當 iterable 用，也會 `with ... as bar: bar.update()`
        def __init__(self, iterable=None, total=None):
            check_cancel()  # 載入模型期間就按了取消 → 開始擴散前直接停
            self.items = list(iterable) if iterable is not None else None
            self.n = max(1, len(self.items) if self.items is not None else (total or 1))
            self.i = 0

        def update(self, k=1):
            check_cancel()
            self.i += k
            job["progress"] = start + (end - start) * min(1, self.i / self.n)
            warn = job.get("warning")
            job["message"] = f"{label} {min(self.i, self.n)}/{self.n}" + (f" · ⚠ {warn}" if warn else "")

        def __iter__(self):
            for x in self.items or []:
                yield x
                self.update()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    pipe.progress_bar = Bar


# ---------------------------------------------------------------- image
def _load_sd():
    from diffusers import AutoPipelineForImage2Image, AutoPipelineForText2Image

    t2i = AutoPipelineForText2Image.from_pretrained(IMAGE_MODEL, torch_dtype=DTYPE, variant="fp16")
    if FAST_VAE:
        from diffusers import AutoencoderTiny
        t2i.vae = AutoencoderTiny.from_pretrained("madebyollin/taesd", torch_dtype=DTYPE)
    t2i.to(DEVICE)
    t2i.set_progress_bar_config(disable=True)
    i2i = AutoPipelineForImage2Image.from_pipe(t2i)
    return {"t2i": t2i, "i2i": i2i}


def fit_image(img, w, h):
    img = img.convert("RGB")
    scale = max(w / img.width, h / img.height)
    img = img.resize((max(w, round(img.width * scale)), max(h, round(img.height * scale))), Image.LANCZOS)
    left, top = (img.width - w) // 2, (img.height - h) // 2
    return img.crop((left, top, left + w, top + h))


def gen_image(job, prompt, width=512, height=512, steps=1, seed=None, reference=None, strength=0.55):
    pipes = SLOT.get("sd", _load_sd, job)
    width, height = int(width) // 64 * 64, int(height) // 64 * 64
    g = torch.Generator("cpu").manual_seed(int(seed) if seed not in (None, "") else int(time.time()) % 2**31)
    if reference is not None:
        pipe = pipes["i2i"]
        hook_progress(pipe, job, 0.1, 0.95, "繪製中")
        # sd-turbo 的 img2img 需要 steps * strength >= 1
        s = max(int(steps), int(np.ceil(1 / float(strength))))
        img = pipe(prompt, image=fit_image(reference, width, height), num_inference_steps=s,
                   strength=float(strength), guidance_scale=0.0, generator=g).images[0]
    else:
        pipe = pipes["t2i"]
        hook_progress(pipe, job, 0.1, 0.95, "繪製中")
        img = pipe(prompt, num_inference_steps=int(steps), guidance_scale=0.0,
                   width=width, height=height, generator=g).images[0]
    path, url = new_path("png")
    img.save(path)
    return {"type": "image", "url": url}


# ---------------------------------------------------------------- video（真動態：AnimateDiff-Lightning）
# 依硬體自動選預設：筆電 CPU 求能跑完，Mac（MPS）/ NVIDIA 顯卡直接上 512
VIDEO_DEFAULTS = {"cpu": (256, 12), "mps": (512, 16), "cuda": (512, 16)}


def _load_animatediff():
    from diffusers import AnimateDiffPipeline, AnimateDiffVideoToVideoPipeline, EulerDiscreteScheduler, MotionAdapter
    from huggingface_hub import hf_hub_download
    from safetensors.torch import load_file

    adapter = MotionAdapter()
    adapter.load_state_dict(load_file(hf_hub_download(*VIDEO_MOTION)))
    t2v = AnimateDiffPipeline.from_pretrained(VIDEO_BASE, motion_adapter=adapter.to(DTYPE), torch_dtype=DTYPE)
    t2v.scheduler = EulerDiscreteScheduler.from_config(t2v.scheduler.config, timestep_spacing="trailing",
                                                       beta_schedule="linear")
    if FAST_VAE:
        from diffusers import AutoencoderTiny
        t2v.vae = AutoencoderTiny.from_pretrained("madebyollin/taesd", torch_dtype=DTYPE)
    t2v.to(DEVICE)
    t2v.set_progress_bar_config(disable=True)
    # from_pipe 會因為 TAESD 的類型不同而漏掉 vae，直接用同一組元件建構（共用權重、不多佔記憶體）
    v2v = AnimateDiffVideoToVideoPipeline(**t2v.components)
    v2v.set_progress_bar_config(disable=True)
    # 運動模組已經合併進 UNetMotionModel，原本那份 1.8GB 的 adapter 可以釋放
    for p in (t2v, v2v):
        p.register_modules(motion_adapter=None)
    del adapter
    gc.collect()
    return {"t2v": t2v, "v2v": v2v}


def _drift_frames(img, n, size):
    """把靜態參考圖做成緩慢推近的「種子影片」，AnimateDiff 會在上面長出真正的動作。"""
    img = fit_image(img, size, size)
    frames = []
    for i in range(n):
        z = 1 + 0.08 * i / max(1, n - 1)
        c = size / z
        o = (size - c) / 2
        frames.append(img.crop((o, o, o + c, o + c)).resize((size, size), Image.LANCZOS))
    return frames


VIDEO_ENGINE = os.environ.get("NOVA_VIDEO_ENGINE", "ltx")  # ltx（高品質）| animatediff（較快）


def _free_ollama():
    """記憶體不到 24GB 時，先請 Ollama 把聊天模型移出記憶體（下次聊天會自動再載入）。"""
    import httpx
    import psutil

    if psutil.virtual_memory().total > 24 * 2**30:
        return
    host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
    host = host if host.startswith("http") else f"http://{host}"
    try:
        for m in httpx.get(f"{host}/api/ps", timeout=3).json().get("models", []):
            httpx.post(f"{host}/api/generate", json={"model": m["name"], "keep_alive": 0}, timeout=10)
    except Exception:
        pass


def _gen_video_ltx(job, prompt, quality, length, reference):
    import video_ltx

    def loader():
        video_ltx._ensure_converted(job)
        return video_ltx.load(DEVICE)

    import psutil

    _free_ollama()
    # T5（約 10GB）必須在影片模型載入「之前」算完：16GB 的電腦兩個同時在記憶體裡會不夠
    if not video_ltx.is_cached(prompt):
        if psutil.virtual_memory().total < 24 * 2**30:
            SLOT.unload()
        video_ltx.prompt_embeds(prompt, job, torch.get_num_threads())
    pipe = SLOT.get("ltx", loader, job)
    hook_progress(pipe, job, 0.15, 0.85, "生成動態")
    job["progress"] = max(job.get("progress", 0), 0.02)
    path, url = new_path("mp4")
    first = video_ltx.generate(pipe, job, prompt, DEVICE, torch.get_num_threads(), quality, length, reference, path)
    poster_path, poster_url = path[:-4] + ".poster.jpg", url[:-4] + ".poster.jpg"
    first.save(poster_path, quality=85)
    job["progress"] = 1.0
    return {"type": "video", "url": url, "poster": poster_url, "engine": "LTX-Video"}


def gen_video(job, prompt, quality="auto", length="auto", reference=None, engine="auto", **_):
    """真動態影片。預設 LTX-Video（畫質好、圖片生影片會鎖定第一幀）；
    選「animatediff」或 LTX 失敗（例如記憶體不足）時改用 AnimateDiff-Lightning。"""
    engine = VIDEO_ENGINE if engine in (None, "", "auto") else engine
    if engine == "ltx":
        try:
            return _gen_video_ltx(job, prompt, quality, length, reference)
        except Exception as e:
            if job.get("cancel"):
                raise
            import traceback
            traceback.print_exc()
            SLOT.unload()
            job["warning"] = f"LTX-Video 失敗（{type(e).__name__}），改用 AnimateDiff"
    # AnimateDiff：畫質 → 邊長，長度 → 幀數（8 fps）
    size = {"fast": 256, "standard": 384, "high": 512}.get(quality, "auto")
    frames = {"2s": 16, "4s": 24}.get(length, "auto")
    out = _gen_video_animatediff(job, prompt, frames, size, reference)
    out["engine"] = "AnimateDiff"
    return out


def _gen_video_animatediff(job, prompt, frames="auto", size="auto", reference=None, strength=0.75):
    """AnimateDiff-Lightning 4 步生成 8~24 幀，放大後補幀成 MP4。
    有參考圖時走 video-to-video，讓那張圖動起來。"""
    import imageio

    dsize, dframes = VIDEO_DEFAULTS[DEVICE]
    size = dsize if size in (None, "", "auto") else int(size) // 64 * 64
    n = dframes if frames in (None, "", "auto") else int(frames)
    size, n = max(256, min(768, size)), max(8, min(24, n))
    pipes = SLOT.get("animatediff", _load_animatediff, job)
    g = torch.Generator("cpu").manual_seed(int(time.time()) % 2**31)
    # 蒸餾模型用 guidance 1.0（不做 CFG，速度快一倍），所以不需要負向提示詞

    if reference is not None:
        pipe = pipes["v2v"]
        hook_progress(pipe, job, 0.05, 0.85, "生成動態")
        # 一定要指定寬高，否則會被放大到 512 再算，CPU 上慢 4 倍
        out = pipe(prompt=prompt, video=_drift_frames(reference, n, size), height=size, width=size,
                   strength=float(strength), num_inference_steps=4, guidance_scale=1.0, generator=g)
    else:
        pipe = pipes["t2v"]
        hook_progress(pipe, job, 0.05, 0.85, "生成動態")
        out = pipe(prompt=prompt, num_frames=n, width=size, height=size,
                   num_inference_steps=4, guidance_scale=1.0, generator=g)
    raw = out.frames[0]

    job["message"] = "放大與補幀"
    out_size = max(512, size)
    big = [f.convert("RGB").resize((out_size, out_size), Image.LANCZOS) if f.width != out_size else f.convert("RGB")
           for f in raw]
    # 8 fps → 16 fps：在相鄰兩幀之間插入混合幀，動作更順
    smooth = []
    for i, f in enumerate(big):
        smooth.append(np.asarray(f))
        if i + 1 < len(big):
            smooth.append(np.asarray(Image.blend(f, big[i + 1], 0.5)))
    path, url = new_path("mp4")
    imageio.mimsave(path, smooth, fps=16, codec="libx264", quality=8, macro_block_size=16)
    poster_path, poster_url = path[:-4] + ".poster.jpg", url[:-4] + ".poster.jpg"
    big[0].save(poster_path, quality=85)
    job["progress"] = 1.0
    return {"type": "video", "url": url, "poster": poster_url}


# ---------------------------------------------------------------- music
def _load_music():
    from transformers import AutoProcessor, MusicgenForConditionalGeneration

    proc = AutoProcessor.from_pretrained(MUSIC_MODEL)
    # MusicGen 在 Mac 上用半精度容易出現雜音，改用 float32（統一記憶體夠大）
    dtype = torch.float32 if DEVICE == "mps" else DTYPE
    model = MusicgenForConditionalGeneration.from_pretrained(MUSIC_MODEL, torch_dtype=dtype).to(DEVICE)
    return {"proc": proc, "model": model}


def gen_music(job, prompt, duration=8):
    import scipy.io.wavfile
    from transformers import LogitsProcessor, LogitsProcessorList

    m = SLOT.get("music", _load_music, job)
    duration = max(2, min(30, float(duration)))
    max_tokens = int(duration * 50)

    class Progress(LogitsProcessor):
        def __init__(self):
            self.n = 0

        def __call__(self, input_ids, scores):
            if job.get("cancel"):  # 使用者取消：每產生一個 token 都會經過這裡
                raise RuntimeError("已取消")
            self.n += 1
            job["progress"] = min(0.97, self.n / max_tokens)
            warn = job.get("warning")
            job["message"] = f"作曲中 {self.n * 100 // max_tokens}%" + (f" · ⚠ {warn}" if warn else "")
            return scores

    inputs = m["proc"](text=[prompt], padding=True, return_tensors="pt").to(DEVICE)
    with torch.no_grad():
        audio = m["model"].generate(**inputs, do_sample=True, guidance_scale=3.0, max_new_tokens=max_tokens,
                                    logits_processor=LogitsProcessorList([Progress()]))
    sr = m["model"].config.audio_encoder.sampling_rate
    data = audio[0, 0].float().cpu().numpy()
    data = data / max(1e-6, np.abs(data).max()) * 0.95
    path, url = new_path("wav")
    scipy.io.wavfile.write(path, rate=sr, data=(data * 32767).astype(np.int16))
    return {"type": "audio", "url": url}


# ---------------------------------------------------------------- 3D
def _load_shape():
    from diffusers import ShapEPipeline

    pipe = ShapEPipeline.from_pretrained(SHAPE_MODEL, torch_dtype=torch.float32 if DEVICE == "mps" else DTYPE).to(DEVICE)
    return pipe


def gen_3d(job, prompt, steps=32, guidance=15.0):
    import trimesh
    from diffusers.utils import export_to_ply

    pipe = SLOT.get("shape", _load_shape, job)
    hook_progress(pipe, job, 0.05, 0.85, "建模中")
    out = pipe(prompt, guidance_scale=float(guidance), num_inference_steps=int(steps),
               frame_size=256, output_type="mesh")
    job["message"] = "輸出網格"
    ply_path, _ = new_path("ply")
    export_to_ply(out.images[0], ply_path)
    mesh = trimesh.load(ply_path)
    # Shap-E 是 Z 軸朝上，轉成網頁 3D 慣用的 Y 軸朝上
    mesh.apply_transform(trimesh.transformations.rotation_matrix(-np.pi / 2, [1, 0, 0]))
    glb_path, glb_url = new_path("glb")
    mesh.export(glb_path)
    os.remove(ply_path)
    return {"type": "model3d", "url": glb_url}
