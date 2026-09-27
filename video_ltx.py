"""LTX-Video 2B（0.9.8 distilled）真動態影片引擎：文字生影片 + 圖片生影片（第一幀鎖定成你的圖）。

記憶體策略（16GB 筆電也跑得動）：
- T5-XXL 文字編碼器（bf16 約 10GB）放在獨立子程序執行，算完就把記憶體還給系統；同一句提示詞會快取。
- 影片 DiT 以 bf16 儲存、fp32 計算（約 3.9GB），VAE 用 fp32 並開啟分塊解碼。
第一次使用會把官方單檔權重轉成 diffusers 格式存到 models/ltxv-2b-0.9.8-distilled（只做一次）。
"""
import gc
import hashlib
import os
import subprocess
import sys
import tempfile
import time

import numpy as np
import torch
from PIL import Image

BASE = os.path.dirname(os.path.abspath(__file__))
MODELS = os.path.join(BASE, "models")
ROOT = os.path.join(MODELS, "ltxv-2b-0.9.8-distilled")
EMB = os.path.join(MODELS, "ltx_prompt_cache")
CKPT = ("Lightricks/LTX-Video", "ltxv-2b-0.9.8-distilled.safetensors")
CFG = "Lightricks/LTX-Video-0.9.5"  # 同樣的 2B 架構 / VAE 設定 + tokenizer + scheduler
T5 = "city96/t5-v1_1-xxl-encoder-bf16"  # 官方 T5 是 19GB fp32，這份 bf16 只要一半
TIMESTEPS = [1000, 993, 987, 981, 975, 909, 725, 0.03]  # 官方蒸餾版 8 步

# 畫質（寬 x 高，都要是 32 的倍數）與長度（幀數要是 8n+1，24fps）
QUALITY = {"fast": (384, 256), "standard": (512, 320), "high": (768, 512)}
LENGTH = {"2s": 49, "4s": 97}
DEFAULTS = {"cpu": ("fast", "2s"), "mps": ("high", "4s"), "cuda": ("high", "4s")}


def available():
    """權重是否已下載（不觸發下載）。"""
    from huggingface_hub import try_to_load_from_cache
    return isinstance(try_to_load_from_cache(*CKPT), str)


def _ensure_converted(job):
    if os.path.isdir(os.path.join(ROOT, "vae")):
        return
    from diffusers import AutoencoderKLLTXVideo, FlowMatchEulerDiscreteScheduler, LTXVideoTransformer3DModel
    from huggingface_hub import hf_hub_download

    job["message"] = "下載 LTX-Video 模型（約 6GB，只有第一次）…"
    ckpt = hf_hub_download(*CKPT)
    job["message"] = "轉換模型格式（只有第一次，約需數分鐘）…"
    tr = LTXVideoTransformer3DModel.from_single_file(ckpt, config=CFG, subfolder="transformer", torch_dtype=torch.bfloat16)
    tr.save_pretrained(os.path.join(ROOT, "transformer"))
    del tr
    gc.collect()
    vae = AutoencoderKLLTXVideo.from_single_file(ckpt, config=CFG, subfolder="vae", torch_dtype=torch.float32)
    vae.save_pretrained(os.path.join(ROOT, "vae"))
    del vae
    gc.collect()
    FlowMatchEulerDiscreteScheduler.from_pretrained(CFG, subfolder="scheduler").save_pretrained(
        os.path.join(ROOT, "scheduler"))


def load(device):
    from diffusers import AutoencoderKLLTXVideo, FlowMatchEulerDiscreteScheduler, LTXConditionPipeline, LTXVideoTransformer3DModel

    tr = LTXVideoTransformer3DModel.from_pretrained(ROOT, subfolder="transformer", torch_dtype=torch.bfloat16)
    # 權重以 bf16 存、計算時升成 fp32（這顆 CPU 沒有 bf16 指令）；預設會跳過 proj_in/out 與 norm，
    # 但整個模型是以 bf16 載入的，跳過的層會留在 bf16 而跟 fp32 輸入衝突，所以全部都要掛上轉型
    tr.enable_layerwise_casting(storage_dtype=torch.bfloat16, compute_dtype=torch.float32, skip_modules_pattern=())
    vae = AutoencoderKLLTXVideo.from_pretrained(ROOT, subfolder="vae", torch_dtype=torch.float32)
    vae.enable_tiling()
    sch = FlowMatchEulerDiscreteScheduler.from_pretrained(ROOT, subfolder="scheduler")
    pipe = LTXConditionPipeline(scheduler=sch, vae=vae, text_encoder=None, tokenizer=None, transformer=tr)
    pipe.to(device)
    pipe.set_progress_bar_config(disable=True)
    return pipe


# T5 在子程序裡跑：結束後 10GB 記憶體直接還給作業系統
_T5_CODE = r'''
import sys, torch
from transformers import AutoTokenizer, T5EncoderModel
from diffusers import LTXConditionPipeline
torch.set_num_threads(int(sys.argv[3]))
tok = AutoTokenizer.from_pretrained(sys.argv[4], subfolder="tokenizer")
te = T5EncoderModel.from_pretrained(sys.argv[5], dtype=torch.bfloat16).eval()
p = LTXConditionPipeline(scheduler=None, vae=None, text_encoder=te, tokenizer=tok, transformer=None)
with torch.inference_mode():
    pe, pm, _, _ = p.encode_prompt(sys.argv[1], do_classifier_free_guidance=False, max_sequence_length=128,
                                   device=torch.device("cpu"), dtype=torch.float32)
torch.save({"pe": pe, "pm": pm}, sys.argv[2])
'''


def _emb_path(prompt):
    return os.path.join(EMB, hashlib.sha1(prompt.encode()).hexdigest() + ".pt")


def is_cached(prompt):
    return os.path.exists(_emb_path(prompt))


def prompt_embeds(prompt, job, threads):
    os.makedirs(EMB, exist_ok=True)
    f = _emb_path(prompt)
    if not os.path.exists(f):
        job["message"] = "理解提示詞（T5 文字編碼器，第一次會下載約 10GB）…"
        tmp = f + ".tmp"
        # stderr 寫到檔案（進度條很多，用 PIPE 又沒人讀會把子程序卡死）
        with tempfile.TemporaryFile() as log:
            proc = subprocess.Popen([sys.executable, "-c", _T5_CODE, prompt, tmp, str(threads), CFG, T5],
                                    stdout=subprocess.DEVNULL, stderr=log)
            while proc.poll() is None:
                if job.get("cancel"):
                    proc.kill()
                    raise RuntimeError("已取消")
                time.sleep(0.5)
            if proc.returncode != 0:
                log.seek(0)
                err = log.read().decode(errors="ignore").strip().splitlines()
                raise RuntimeError("T5 文字編碼失敗：" + (err[-1] if err else f"exit {proc.returncode}"))
        os.replace(tmp, f)
    d = torch.load(f)
    return d["pe"], d["pm"]


def condition_image(img, w, h, crf=29):
    """置中裁切成 w x h，再做一次 H.264 壓縮（官方推論與 ComfyUI 都這樣做，動作會更自然）。"""
    import imageio

    img = img.convert("RGB")
    s = max(w / img.width, h / img.height)
    img = img.resize((max(w, round(img.width * s)), max(h, round(img.height * s))), Image.LANCZOS)
    left, top = (img.width - w) // 2, (img.height - h) // 2
    img = img.crop((left, top, left + w, top + h))
    fd, tmp = tempfile.mkstemp(suffix=".mp4")
    os.close(fd)
    try:
        wr = imageio.v2.get_writer(tmp, format="FFMPEG", fps=1, codec="libx264", quality=None, pixelformat="yuv420p",
                                   macro_block_size=16, ffmpeg_params=["-crf", str(crf), "-preset", "veryfast"])
        wr.append_data(np.asarray(img))
        wr.close()
        rd = imageio.v2.get_reader(tmp, format="FFMPEG")
        out = Image.fromarray(rd.get_data(0))
        rd.close()
    finally:
        os.remove(tmp)
    return out


def resolve(device, quality, length, reference=None):
    dq, dl = DEFAULTS.get(device, DEFAULTS["cpu"])
    w, h = QUALITY.get(quality if quality in QUALITY else dq)
    n = LENGTH.get(length if length in LENGTH else dl)
    if reference is not None and reference.height > reference.width:
        w, h = h, w  # 直式照片就生成直式影片
    return w, h, n


@torch.inference_mode()
def generate(pipe, job, prompt, device, threads, quality="auto", length="auto", reference=None, out_path=None):
    import imageio

    w, h, n = resolve(device, quality, length, reference)
    pe, pm = prompt_embeds(prompt, job, threads)
    cond = condition_image(reference, w, h) if reference is not None else None
    job["message"] = f"生成動態 {w}x{h}・{n} 幀"
    out = pipe(image=cond, prompt_embeds=pe.to(device), prompt_attention_mask=pm.to(device),
               width=w, height=h, num_frames=n, frame_rate=24, timesteps=TIMESTEPS, guidance_scale=1.0,
               decode_timestep=0.05, decode_noise_scale=0.025, image_cond_noise_scale=0.15,
               generator=torch.Generator("cpu").manual_seed(int.from_bytes(os.urandom(4), "little")),
               output_type="np")
    frames = out.frames[0]
    job["message"] = "輸出影片"
    u8 = (np.clip(frames, 0, 1) * 255).round().astype(np.uint8)
    scale = 2 if w < 768 else 1  # 小尺寸在輸出時用 lanczos 放大，幾乎不花時間
    wr = imageio.v2.get_writer(out_path, format="FFMPEG", fps=24, codec="libx264", quality=None,
                               pixelformat="yuv420p", macro_block_size=16,
                               ffmpeg_params=["-crf", "18", "-preset", "medium", "-movflags", "+faststart",
                                              "-vf", f"scale={w * scale}:{h * scale}:flags=lanczos"])
    for f in u8:
        wr.append_data(f)
    wr.close()
    return Image.fromarray(u8[0])
