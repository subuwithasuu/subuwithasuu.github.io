"""Render a 1000x1500 food pin with Pillow.

Design rules (from the canvas-design skill, adapted for a mobile feed):
  * restraint: one photo treatment, one type pairing, one accent colour per pin
  * hierarchy: protein badge -> hook title -> small eyebrow -> brand, nothing else
  * strict margins (64 px), nothing overlaps, every text block measured
  * MOBILE: Pinterest's phone grid shows a pin ~180 px wide (18% of 1000), so
    titles never go below 72 px (~13 px on screen) and the protein number is
    drawn at 96 px+. Text colour is checked for WCAG contrast >= 4.5.

Layouts (stable names, used by the analytics feedback loop):
  split  - photo / full-width text band / photo   (classic, highest legibility)
  single - full-bleed hero photo, dark scrim, big left-aligned title
  band   - poster: photo top 60%, solid block below, badge on the seam
"""
from __future__ import annotations

import re
import sys
import zlib
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

from common import BUILD, FONTS, load_config

LAYOUTS = ["split", "single", "band"]
M = 64                       # outer margin
MIN_TITLE = 72               # never smaller: ~13 px on a phone grid


# ---------------------------------------------------------------- basics
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


def _lum(rgb) -> float:
    def ch(c):
        c /= 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(x) for x in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a, b) -> float:
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def readable(fg, bg, min_ratio: float = 4.5):
    """Keep the palette colour if it is readable on bg, else near-black/white."""
    if contrast(fg, bg) >= min_ratio:
        return fg
    dark, light = (24, 24, 28), (255, 255, 255)
    return dark if contrast(dark, bg) >= contrast(light, bg) else light


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


def block_h(f, lines, lh) -> int:
    return lh * (len(lines) - 1) + f.getbbox("Hg")[3]


def draw_lines(draw, lines, f, x, y, lh, fill, align="center"):
    for ln in lines:
        w = draw.textlength(ln, font=f)
        draw.text((x - w / 2 if align == "center" else x, y), ln, font=f, fill=fill)
        y += lh
    return y


def site_label(cfg: dict) -> str:
    return cfg["site"].get("pin_label") or cfg["site"]["name"]


def protein_badge(img: Image.Image, grams: int, cx: int, cy: int, pal: dict, r: int = 104) -> None:
    """Round stamp: '32g' huge, 'PROTEIN' small. The niche's #1 hook, readable at thumbnail size."""
    acc = hex2rgb(pal["accent"])
    ink = readable((255, 255, 255), acc)
    sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).ellipse((cx - r, cy - r + 10, cx + r, cy + r + 10), fill=(0, 0, 0, 80))
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(12)))
    d = ImageDraw.Draw(img)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=acc + (255,))
    d.ellipse((cx - r + 9, cy - r + 9, cx + r - 9, cy + r - 9), outline=ink + (230,), width=3)
    num = f"{grams}g"
    nf = font("impact", 96 if len(num) <= 3 else 84)
    lf = font("caps", 26)
    nh, lh = nf.getbbox(num)[3], lf.getbbox("PROTEIN")[3]
    top = cy - (nh + 6 + lh) // 2 - 6
    d.text((cx - d.textlength(num, font=nf) / 2, top), num, font=nf, fill=ink)
    d.text((cx - d.textlength("PROTEIN", font=lf) / 2, top + nh + 6), "PROTEIN", font=lf, fill=ink)


def pin_style(cfg: dict, recipe: dict, variant: int) -> tuple[str, int]:
    """Default layout + palette for a variant (the queue may override from learnings)."""
    pals = cfg["pin_design"]["palettes"]
    return LAYOUTS[variant % len(LAYOUTS)], (zlib.crc32(recipe["slug"].encode()) + variant) % len(pals)


# ---------------------------------------------------------------- layouts
def _split(c, cfg, pal, v, protein, p1, p2):
    W, H = c.size
    d = ImageDraw.Draw(c)
    box, acc = hex2rgb(pal["box"]), hex2rgb(pal["accent"])
    txt = readable(hex2rgb(pal["text"]), box)
    eb = readable(acc, box, 3.0)
    tf, lines, lh = wrap_fit(d, v["overlay"], "title", W - 2 * M, 330, 116, MIN_TITLE)
    ef, sf = font("caps", 32), font("body", 28)
    eyebrow = v.get("subline", "").upper()
    band_h = 44 + ef.getbbox("H")[3] + 26 + block_h(tf, lines, lh) + 28 + sf.getbbox("Hg")[3] + 44
    y0 = int(H * 0.47) - band_h // 2
    c.paste(cover(p1, W, y0, 0.55), (0, 0))
    c.paste(cover(p2, W, H - (y0 + band_h), 0.45), (0, y0 + band_h))
    d = ImageDraw.Draw(c)
    d.rectangle((0, y0, W, y0 + band_h), fill=box)
    d.rectangle((0, y0, W, y0 + 8), fill=acc)
    d.rectangle((0, y0 + band_h - 8, W, y0 + band_h), fill=acc)
    y = y0 + 44
    d.text((W / 2 - d.textlength(eyebrow, font=ef) / 2, y), eyebrow, font=ef, fill=eb)
    y += ef.getbbox("H")[3] + 26
    draw_lines(d, lines, tf, W / 2, y, lh, txt)
    y += block_h(tf, lines, lh) + 28
    lbl = site_label(cfg)
    d.text((W / 2 - d.textlength(lbl, font=sf) / 2, y), lbl, font=sf, fill=txt)
    if protein:
        protein_badge(c, protein, W - M - 104, M + 104, pal)


def _single(c, cfg, pal, v, protein, p1, _p2):
    W, H = c.size
    c.paste(cover(p1, W, H, 0.4), (0, 0))
    # bottom scrim: transparent -> 82% black over the lower 55%
    scrim = Image.new("L", (1, H), 0)
    for y in range(H):
        t = max(0.0, (y - H * 0.45) / (H * 0.55))
        scrim.putpixel((0, y), int(210 * min(1.0, t * 1.25) ** 1.4))
    c.alpha_composite(Image.merge("RGBA", (*[Image.new("L", (W, H), 0)] * 3, scrim.resize((W, H)))))
    d = ImageDraw.Draw(c)
    acc = hex2rgb(pal["accent"])
    title = re.sub(r"(\d+)G\b", r"\1g", v["overlay"].upper())      # "30g PROTEIN", not "30G"
    tf, lines, lh = wrap_fit(d, title, "impact", W - 2 * M, 420, 132, MIN_TITLE + 8,
                             max_lines=3, spacing=1.04)
    sf = font("body", 30)
    ef = font("caps", 32)
    eyebrow = v.get("subline", "").upper()
    bottom = H - M - sf.getbbox("Hg")[3]
    y_title = bottom - 30 - block_h(tf, lines, lh)
    chip_h = ef.getbbox("H")[3] + 28
    y_chip = y_title - 30 - chip_h
    cw = d.textlength(eyebrow, font=ef) + 44
    d.rounded_rectangle((M, y_chip, M + cw, y_chip + chip_h), chip_h // 2, fill=acc)
    d.text((M + 22, y_chip + 14), eyebrow, font=ef, fill=readable((255, 255, 255), acc))
    draw_lines(d, lines, tf, M, y_title, lh, (255, 255, 255), align="left")
    d.rectangle((M, bottom - 2, M + 90, bottom + 2), fill=acc)
    d.text((M + 110, bottom - sf.getbbox("Hg")[3] // 2 - 2), site_label(cfg), font=sf, fill=(255, 255, 255))
    if protein:
        protein_badge(c, protein, W - M - 104, M + 104, pal)


def _band(c, cfg, pal, v, protein, p1, _p2):
    W, H = c.size
    seam = int(H * 0.60)
    c.paste(cover(p1, W, seam, 0.5), (0, 0))
    d = ImageDraw.Draw(c)
    box, acc = hex2rgb(pal["box"]), hex2rgb(pal["accent"])
    d.rectangle((0, seam, W, H), fill=box)
    txt = readable(hex2rgb(pal["text"]), box)
    eb = readable(acc, box, 3.0)
    top = seam + (104 + 28 if protein else 56)
    ef, sf = font("caps", 32), font("body", 28)
    eyebrow = v.get("subline", "").upper()
    avail = H - M - sf.getbbox("Hg")[3] - 36 - (top + ef.getbbox("H")[3] + 24)
    tf, lines, lh = wrap_fit(d, v["overlay"], "title", W - 2 * M, avail, 118, MIN_TITLE)
    d.text((W / 2 - d.textlength(eyebrow, font=ef) / 2, top), eyebrow, font=ef, fill=eb)
    draw_lines(d, lines, tf, W / 2, top + ef.getbbox("H")[3] + 24, lh, txt)
    lbl = site_label(cfg)
    yb = H - M - sf.getbbox("Hg")[3]
    lw = d.textlength(lbl, font=sf)
    d.line((W / 2 - lw / 2 - 70, yb + 16, W / 2 - lw / 2 - 20, yb + 16), fill=eb, width=3)
    d.line((W / 2 + lw / 2 + 20, yb + 16, W / 2 + lw / 2 + 70, yb + 16), fill=eb, width=3)
    d.text((W / 2 - lw / 2, yb), lbl, font=sf, fill=txt)
    if protein:
        protein_badge(c, protein, W // 2, seam, pal)


RENDER = {"split": _split, "single": _single, "band": _band}


def render_pin(cfg: dict, recipe: dict, variant: int, photo_paths: list[Path], out: Path,
               layout: str | None = None, palette: int | None = None) -> Path:
    W, H = cfg["pin_design"]["width"], cfg["pin_design"]["height"]
    pals = cfg["pin_design"]["palettes"]
    d_layout, d_pal = pin_style(cfg, recipe, variant)
    layout = layout if layout in RENDER else d_layout
    pal = pals[(palette if palette is not None else d_pal) % len(pals)]
    v = recipe["pin_variants"][variant % len(recipe["pin_variants"])]
    photos = [Image.open(p) for p in photo_paths]
    p1, p2 = photos[variant % len(photos)], photos[(variant + 1) % len(photos)]
    canvas = Image.new("RGBA", (W, H), hex2rgb(pal["band"]) + (255,))
    RENDER[layout](canvas, cfg, pal, v, recipe.get("protein_g"), p1, p2)
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.convert("RGB").save(out, "JPEG", quality=90, optimize=True, progressive=True)
    return out


def render_for_recipe(cfg: dict, recipe: dict, variant: int, layout: str | None = None,
                      palette: int | None = None) -> Path:
    import pexels
    paths = [pexels.download(p) for p in recipe["photos"][:4]]
    tag = f"_{layout}" if layout else ""
    out = BUILD / "pins" / f"{recipe['slug']}_v{variant}{tag}.jpg"
    return render_pin(cfg, recipe, variant, paths, out, layout, palette)


if __name__ == "__main__":
    # python src/make_pin.py <recipe.json> <variant> [layout]
    from common import read_json
    cfg = load_config()
    r = read_json(Path(sys.argv[1]), None)
    print(render_for_recipe(cfg, r, int(sys.argv[2]) if len(sys.argv) > 2 else 0,
                            sys.argv[3] if len(sys.argv) > 3 else None))
