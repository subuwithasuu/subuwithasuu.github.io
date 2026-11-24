"""Render a vertical recipe video pin (1080x1920) - no GPU, no paid tools.

Pexels photos + slow Ken Burns zoom + caption cards (Pillow) + Kokoro narration,
frames piped straight into ffmpeg. Runs on a free GitHub Actions runner.
"""
from __future__ import annotations

import os
import subprocess
import sys
import zlib
from pathlib import Path

import numpy as np
import soundfile as sf
from PIL import Image, ImageDraw, ImageFilter, ImageOps

from common import BUILD, ROOT, load_config, log, read_json
from make_pin import font, hex2rgb, site_label, wrap_fit

GAP = 0.35          # silence between narration lines (s)
TAIL = 0.6          # hold at the end (s)


def kokoro():
    from kokoro_onnx import Kokoro
    for d in (os.environ.get("KOKORO_DIR"), str(ROOT / "models"), os.path.expanduser("~/tools/kokoro")):
        if d and Path(d, "kokoro-v1.0.onnx").exists():
            return Kokoro(str(Path(d, "kokoro-v1.0.onnx")), str(Path(d, "voices-v1.0.bin")))
    raise RuntimeError("Kokoro model files not found (run scripts/fetch_assets.sh)")


def narrate(cfg: dict, lines: list[str], out_wav: Path) -> list[float]:
    """Speak each line; return each segment's duration (line + gap)."""
    k = kokoro()
    v = cfg["video"]
    chunks, durs, sr = [], [], 24000
    for i, text in enumerate(lines):
        samples, sr = k.create(text, voice=v["voice"], speed=v["speed"], lang="en-us")
        pad = TAIL if i == len(lines) - 1 else GAP
        chunks.append(np.concatenate([samples, np.zeros(int(sr * pad), dtype=samples.dtype)]))
        durs.append(len(samples) / sr + pad)
    lead = np.zeros(int(sr * 0.25), dtype=chunks[0].dtype)
    durs[0] += 0.25
    sf.write(out_wav, np.concatenate([lead, *chunks]), sr)
    return durs


def caption_overlay(W, H, text, pal, *, big=False, badge=None, footer=None) -> Image.Image:
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    # soft gradient at the bottom for legibility
    grad = Image.new("L", (1, H), 0)
    for y in range(H):
        grad.putpixel((0, y), int(150 * max(0, (y - H * 0.45) / (H * 0.55)) ** 1.6))
    ov.paste(Image.new("RGBA", (W, H), (0, 0, 0, 255)), (0, 0), grad.resize((W, H)))
    d = ImageDraw.Draw(ov)
    box_w = W - 140
    fname = "title" if big else "body"
    tf, lines, lh = wrap_fit(d, text, fname, box_w - 80, 520, start=104 if big else 66, min_size=44,
                             max_lines=4 if big else 5, spacing=1.12)
    th = lh * (len(lines) - 1) + tf.getbbox("Hg")[3]
    bh = th + 90 + (70 if badge else 0)
    cy = int(H * (0.52 if big else 0.66))
    x0, y0 = (W - box_w) // 2, cy - bh // 2
    sh = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle((x0, y0 + 16, x0 + box_w, y0 + bh + 16), 34, fill=(0, 0, 0, 110))
    ov.alpha_composite(sh.filter(ImageFilter.GaussianBlur(20)))
    d = ImageDraw.Draw(ov)
    d.rounded_rectangle((x0, y0, x0 + box_w, y0 + bh), 34, fill=hex2rgb(pal["box"]) + (245,))
    y = y0 + 45
    if badge:
        bf = font("caps", 34)
        bw = d.textlength(badge, font=bf) + 50
        d.rounded_rectangle((W / 2 - bw / 2, y - 6, W / 2 + bw / 2, y + 50), 28, fill=hex2rgb(pal["accent"]))
        d.text((W / 2 - d.textlength(badge, font=bf) / 2, y + 2), badge, font=bf, fill=(255, 255, 255))
        y += 70
    for ln in lines:
        d.text((W / 2 - d.textlength(ln, font=tf) / 2, y), ln, font=tf, fill=hex2rgb(pal["text"]))
        y += lh
    if footer:
        ff = font("caps", 40)
        d.text((W / 2 - d.textlength(footer, font=ff) / 2, H * 0.80), footer, font=ff, fill=(255, 255, 255))
    return ov


def render_video(cfg: dict, recipe: dict, photo_paths: list[Path], out: Path) -> Path:
    v = cfg["video"]
    W, H, FPS = v["width"], v["height"], v["fps"]
    pals = cfg["pin_design"]["palettes"]
    pal = pals[zlib.crc32(recipe["slug"].encode()) % len(pals)]
    vs = recipe["video_script"]
    steps = vs["steps_short"][: v["max_steps_shown"]]
    spoken = [vs["hook"], *steps, vs["outro"]]
    work = BUILD / "video" / recipe["slug"]
    work.mkdir(parents=True, exist_ok=True)
    wav = work / "voice.wav"
    durs = narrate(cfg, spoken, wav)

    label = site_label(cfg)
    overlays = [caption_overlay(W, H, recipe["pin_variants"][0]["overlay"], pal, big=True,
                                badge=(f"{recipe['protein_g']}G PROTEIN • {recipe['total_minutes']} MIN"
                                       if recipe.get("protein_g") else f"{recipe['total_minutes']} MIN RECIPE"))]
    for i, s in enumerate(steps, 1):
        overlays.append(caption_overlay(W, H, s, pal, badge=f"STEP {i}"))
    overlays.append(caption_overlay(W, H, "Full recipe + tips", pal, big=True, badge="SAVE THIS PIN",
                                    footer=label.upper()))

    # oversize each photo once; zoom by cropping a shrinking window
    zoom = 1.12
    bases = []
    for i in range(len(overlays)):
        im = Image.open(photo_paths[i % len(photo_paths)])
        bases.append(ImageOps.fit(im.convert("RGB"), (int(W * zoom), int(H * zoom)), Image.LANCZOS))

    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
           "-i", str(wav), "-c:v", "libx264", "-preset", "medium", "-crf", "21", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart", str(out)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    fade = int(FPS * 0.25)
    prev_last = None
    for seg, (base, ov, dur) in enumerate(zip(bases, overlays, durs)):
        n = max(1, int(round(dur * FPS)))
        zoom_in = seg % 2 == 0
        for f in range(n):
            t = f / max(1, n - 1)
            z = (1 + (zoom - 1) * t) if zoom_in else (zoom - (zoom - 1) * t)
            cw, ch = int(base.width / z), int(base.height / z)
            x0, y0 = (base.width - cw) // 2, (base.height - ch) // 2
            frame = base.crop((x0, y0, x0 + cw, y0 + ch)).resize((W, H), Image.BILINEAR).convert("RGBA")
            # caption pops in over the first 0.25 s
            a = min(1.0, f / fade) if fade else 1.0
            if a < 1:
                o = ov.copy()
                o.putalpha(o.getchannel("A").point(lambda p: int(p * a)))
                frame.alpha_composite(o)
            else:
                frame.alpha_composite(ov)
            frame = frame.convert("RGB")
            if prev_last is not None and f < fade:            # crossfade from previous segment
                frame = Image.blend(prev_last, frame, f / fade)
            proc.stdin.write(frame.tobytes())
            if f == n - 1:
                prev_last = frame
    proc.stdin.close()
    if proc.wait() != 0:
        raise RuntimeError("ffmpeg failed")
    log(f"video -> {out} ({sum(durs):.1f}s)")
    return out


def render_for_recipe(cfg: dict, recipe: dict) -> Path:
    import pexels
    paths = [pexels.download(p, width=1400) for p in recipe["photos"][:6]]
    return render_video(cfg, recipe, paths, BUILD / "videos" / f"{recipe['slug']}.mp4")


if __name__ == "__main__":
    cfg = load_config()
    r = read_json(Path(sys.argv[1]), None)
    print(render_for_recipe(cfg, r))
