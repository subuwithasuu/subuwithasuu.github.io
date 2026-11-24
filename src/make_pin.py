"""Render a 1000x1500 food pin with Pillow.

Three classic food-niche layouts (rotated so the feed doesn't look cloned):
  split  - photo on top, different photo on the bottom, centered text box
  single - one full-bleed photo with a centered text box
  band   - photo on top 2/3, solid colour band with the title below
"""
from __future__ import annotations

import sys
import zlib
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from common import BUILD, FONTS, load_config

LAYOUTS = ["split", "single", "band"]


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    files = {
        "title": ["DMSerifDisplay-Regular.ttf"],
        "impact": ["Anton-Regular.ttf"],
        "caps": ["Lato-Black.ttf", "Lato-Bold.ttf"],
        "body": ["Lato-Bold.ttf"],
        "script": ["Pacifico-Regular.ttf"],
    }[name]
    for f in files:
        p = FONTS / f
        if p.exists():
            return ImageFont.truetype(str(p), size)
    for fallback in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                     "/System/Library/Fonts/Supplemental/Georgia Bold.ttf"):
        if Path(fallback).exists():
            return ImageFont.truetype(fallback, size)
    return ImageFont.load_default(size)


def hex2rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def cover(img: Image.Image, w: int, h: int, focus_y: float = 0.5) -> Image.Image:
    return ImageOps.fit(img.convert("RGB"), (w, h), Image.LANCZOS, centering=(0.5, focus_y))


def wrap_fit(draw, text: str, fname: str, max_w: int, max_h: int, start: int, min_size: int,
             max_lines: int = 3, spacing: float = 1.08):
    """Largest font size where `text` wraps into <= max_lines lines inside max_w x max_h."""
    words = text.split()
    for size in range(start, min_size - 1, -2):
        f = font(fname, size)
        lines, cur = [], ""
        for w in words:
            t = (cur + " " + w).strip()
            if draw.textlength(t, font=f) <= max_w:
                cur = t
            else:
                if cur:
                    lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        line_h = int(size * spacing)
        if len(lines) <= max_lines and all(draw.textlength(l, font=f) <= max_w for l in lines) \
                and line_h * len(lines) <= max_h:
            return f, lines, line_h
    f = font(fname, min_size)
    return f, [text], int(min_size * spacing)


def draw_centered(draw, lines, f, cx, y, line_h, fill):
    for ln in lines:
        w = draw.textlength(ln, font=f)
        draw.text((cx - w / 2, y), ln, font=f, fill=fill)
        y += line_h
    return y


def site_label(cfg: dict) -> str:
    return cfg["site"].get("pin_label") or cfg["site"]["name"]


def text_box(img: Image.Image, cfg: dict, pal: dict, overlay: str, subline: str,
             cx: int, cy: int, box_w: int, title_font: str = "title", shadow: bool = True) -> None:
    W = img.width
    d = ImageDraw.Draw(img)
    inner = box_w - 90
    sub_f = font("caps", 30)
    tf, lines, lh = wrap_fit(d, overlay, title_font, inner, 300,
                             start=96 if title_font == "title" else 104, min_size=50)
    site_f = font("body", 26)
    sub = subline.upper()
    sub_h = sub_f.getbbox("HG")[3]
    title_h = lh * (len(lines) - 1) + tf.getbbox("Hg")[3]
    site_h = site_f.getbbox("Hg")[3]
    box_h = 40 + sub_h + 24 + title_h + 26 + site_h + 38
    x0, y0 = cx - box_w // 2, cy - box_h // 2
    if shadow:
        sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ImageDraw.Draw(sh).rounded_rectangle((x0, y0 + 14, x0 + box_w, y0 + box_h + 14), 26, fill=(0, 0, 0, 90))
        img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(18)))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((x0, y0, x0 + box_w, y0 + box_h), 26, fill=hex2rgb(pal["box"]) + (255,))
    d.rounded_rectangle((x0 + 12, y0 + 12, x0 + box_w - 12, y0 + box_h - 12), 18,
                        outline=hex2rgb(pal["accent"]) + (255,), width=3)
    y = y0 + 40
    sw = d.textlength(sub, font=sub_f)
    d.text((cx - sw / 2, y), sub, font=sub_f, fill=hex2rgb(pal["accent"]))
    y += sub_h + 24
    draw_centered(d, lines, tf, cx, y, lh, hex2rgb(pal["text"]))
    y += title_h + 26
    lbl = site_label(cfg)
    lw = d.textlength(lbl, font=site_f)
    d.text((cx - lw / 2, y), lbl, font=site_f, fill=hex2rgb(pal["text"]) + (200,))
    _ = W


def render_pin(cfg: dict, recipe: dict, variant: int, photo_paths: list[Path], out: Path) -> Path:
    W, H = cfg["pin_design"]["width"], cfg["pin_design"]["height"]
    pals = cfg["pin_design"]["palettes"]
    pal = pals[(zlib.crc32(recipe["slug"].encode()) + variant) % len(pals)]
    v = recipe["pin_variants"][variant % len(recipe["pin_variants"])]
    layout = LAYOUTS[variant % len(LAYOUTS)]
    photos = [Image.open(p) for p in photo_paths]
    p1 = photos[variant % len(photos)]
    p2 = photos[(variant + 1) % len(photos)]
    canvas = Image.new("RGBA", (W, H), hex2rgb(pal["band"]) + (255,))

    if layout == "split":
        canvas.paste(cover(p1, W, H // 2, 0.5), (0, 0))
        canvas.paste(cover(p2, W, H // 2, 0.5), (0, H // 2))
        text_box(canvas, cfg, pal, v["overlay"], v.get("subline", ""), W // 2, H // 2, 880)
    elif layout == "single":
        canvas.paste(cover(p1, W, H, 0.45), (0, 0))
        text_box(canvas, cfg, pal, v["overlay"], v.get("subline", ""), W // 2, int(H * 0.60), 860,
                 title_font="impact")
    else:  # band
        ph = int(H * 0.64)
        canvas.paste(cover(p1, W, ph, 0.5), (0, 0))
        d = ImageDraw.Draw(canvas)
        d.rectangle((0, ph, W, H), fill=hex2rgb(pal["box"]))
        d.rectangle((0, ph, W, ph + 10), fill=hex2rgb(pal["accent"]))
        sub_f = font("caps", 32)
        sub = v.get("subline", "").upper()
        y = ph + 52
        d.text((W / 2 - d.textlength(sub, font=sub_f) / 2, y), sub, font=sub_f, fill=hex2rgb(pal["accent"]))
        tf, lines, lh = wrap_fit(d, v["overlay"], "title", W - 120, H - ph - 200, 104, 54)
        y = draw_centered(d, lines, tf, W // 2, y + 56, lh, hex2rgb(pal["text"]))
        sf = font("body", 28)
        lbl = site_label(cfg)
        d.text((W / 2 - d.textlength(lbl, font=sf) / 2, H - 70), lbl, font=sf, fill=hex2rgb(pal["text"]))

    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(out, "JPEG", quality=90, optimize=True, progressive=True)
    return out


def render_for_recipe(cfg: dict, recipe: dict, variant: int) -> Path:
    import pexels
    paths = [pexels.download(p) for p in recipe["photos"][:4]]
    out = BUILD / "pins" / f"{recipe['slug']}_v{variant}.jpg"
    return render_pin(cfg, recipe, variant, paths, out)


if __name__ == "__main__":
    # python src/make_pin.py <recipe.json> <variant>
    from common import read_json
    cfg = load_config()
    r = read_json(Path(sys.argv[1]), None)
    print(render_for_recipe(cfg, r, int(sys.argv[2]) if len(sys.argv) > 2 else 0))
