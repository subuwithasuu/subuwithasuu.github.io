"""Build the static recipe website into ./site (deployed by GitHub Pages).

Affiliate links: add them later in data/affiliate.yaml, e.g.
  "skillet": {name: "Lodge 12-inch cast iron skillet", url: "https://amzn.to/...", note: "the one we use"}
Any recipe tool whose name contains the key gets that link.
"""
from __future__ import annotations

import json
import shutil
from datetime import date
from xml.sax.saxutils import escape

import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

from common import DATA, ROOT, SITE_OUT, board_slug, load_config, load_recipes, log, recipe_url
from nutrition import MEAT


def asset_version() -> str:
    """Cache-busting tag: changes whenever the CSS/JS change."""
    import hashlib
    h = hashlib.sha1()
    for f in sorted((ROOT / "assets").glob("site.*")):
        h.update(f.read_bytes())
    return h.hexdigest()[:8]


def ingredient_line(i: dict) -> str:
    parts = [str(i.get("qty", "")).strip(), str(i.get("unit", "")).strip(), str(i.get("item", "")).strip()]
    s = " ".join(p for p in parts if p)
    if i.get("note"):
        s += f", {i['note']}"
    return s


def jsonld(cfg: dict, r: dict, url: str) -> str:
    return json.dumps({
        "@context": "https://schema.org",
        "@type": "Recipe",
        "name": r["title"],
        "description": r["description"],
        "image": [p["web"] for p in r["photos"][:3]],
        "author": {"@type": "Organization", "name": cfg["site"]["author"]},
        "datePublished": r.get("created", date.today().isoformat()),
        "prepTime": f"PT{r['prep_minutes']}M",
        "cookTime": f"PT{r['cook_minutes']}M",
        "totalTime": f"PT{r['total_minutes']}M",
        "recipeYield": f"{r['servings']} servings",
        "recipeCategory": r.get("category", ""),
        "recipeCuisine": r.get("cuisine", ""),
        "keywords": ", ".join(r.get("keywords", [])),
        "recipeIngredient": [ingredient_line(i) for i in r["ingredients"]],
        "recipeInstructions": [{"@type": "HowToStep", "text": s} for s in r["steps"]],
        "url": url,
        **({"nutrition": {"@type": "NutritionInformation", "proteinContent": f"{r['protein_g']} g",
                          "servingSize": "1 serving"}} if r.get("protein_g") else {}),
    }, ensure_ascii=False).replace("</", "<\\/")


def tools_for(r: dict, aff: dict) -> list[dict]:
    out = []
    for t in r.get("tools", []):
        hit = next((v for k, v in aff.items() if k.lower() in t.lower()), None)
        out.append({"name": hit.get("name", t), "url": hit.get("url"), "note": hit.get("note")} if hit else {"name": t})
    return out


PRIVACY = """
<p>Last updated: {date}</p>
<p>{name} ("we") runs this website and the {name} Pinterest account. This policy explains what data we handle.</p>
<h2>Website visitors</h2>
<p>This is a static website hosted on GitHub Pages. We do not use accounts, forms, cookies or tracking scripts.
GitHub may log basic technical data (such as IP address) for security; see GitHub's privacy statement.</p>
<h2>Pinterest integration</h2>
<p>Our own automation uses the Pinterest API only to publish pins and boards to <strong>our own</strong> Pinterest
account and to read analytics for those pins. We do not access, collect, store or share any data about other
Pinterest users. Access tokens are stored as encrypted secrets and used only for this purpose.</p>
<h2>Affiliate links</h2>
<p>Some recipe pages contain affiliate links. If you buy through them, the retailer may set its own cookies under its own policy.</p>
<h2>Contact</h2>
<p>Questions: open an issue on this site's GitHub repository.</p>
"""

ABOUT = """
<p>{name} shares easy, reliable recipes for busy home cooks: {tagline}</p>
<p>Every recipe is checked against established, published recipes for the same dish before it goes live:
ingredient ratios, temperatures, timings and food-safety temperatures. Photos are real food photography from
Unsplash and show the style of the dish.</p>
"""


def main() -> None:
    cfg = load_config()
    site = dict(cfg["site"])
    recipes = sorted(load_recipes(), key=lambda r: r.get("created", ""), reverse=True)
    aff_file = DATA / "affiliate.yaml"
    aff = yaml.safe_load(aff_file.read_text()) if aff_file.exists() else {}
    aff = aff or {}

    env = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=select_autoescape(["html"]))
    veg_boards = {b["name"] for b in cfg["boards"] if b.get("vegetarian")}
    for r in recipes:                          # fields the cards/filters need
        items_txt = " ".join(str(i.get("item", "")) for i in r.get("ingredients", []))
        r["veg"] = r["board"] in veg_boards or not MEAT.search(items_txt)
        r["search_text"] = " ".join([r["title"], r["board"], " ".join(r.get("keywords", [])), items_txt]).lower()
    boards = []
    for b in cfg["boards"]:
        first = next((r for r in recipes if r["board"] == b["name"] and r.get("photos")), None)
        boards.append({"name": b["name"], "slug": board_slug(b["name"]), "description": b["description"],
                       "image": first["photos"][0]["web"] if first else None})
    featured = sorted([r for r in recipes if r.get("photos")], key=lambda r: -(r.get("protein_g") or 0))[:3]
    if len(featured) == 3:                 # middle card is the front one: put the top recipe there
        featured = [featured[1], featured[0], featured[2]]
    product = cfg.get("product") if (cfg.get("product") or {}).get("url") else None
    common = {"site": site, "boards": boards, "year": date.today().year, "product": product,
              "asset_v": asset_version()}

    if SITE_OUT.exists():
        shutil.rmtree(SITE_OUT)
    SITE_OUT.mkdir(parents=True)
    shutil.copytree(ROOT / "assets", SITE_OUT / "assets")
    urls = []

    def write(rel: str, html: str):
        p = SITE_OUT / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(html, encoding="utf-8")

    base = site["base_url"]
    for r in recipes:
        url = recipe_url(cfg, r)
        related = [x for x in recipes if x["board"] == r["board"] and x["slug"] != r["slug"]][:4]
        html = env.get_template("recipe.html").render(
            **common, r=r, url=url, hero=r["photos"][0], gallery=r["photos"][1:4],
            tools=tools_for(r, aff), related=related, board_slug=board_slug(r["board"]),
            jsonld=jsonld(cfg, r, url))
        write(f"recipes/{r['slug']}/index.html", html)
        urls.append((url, r.get("created")))

    for b in boards:
        items = [r for r in recipes if r["board"] == b["name"]]
        url = f"{base}/c/{b['slug']}/"
        write(f"c/{b['slug']}/index.html", env.get_template("list.html").render(
            **common, heading=b["name"], intro=b["description"], items=items, url=url))
        urls.append((url, None))

    write("index.html", env.get_template("home.html").render(
        **common, items=recipes, featured=featured, url=f"{base}/"))
    urls.append((f"{base}/", None))
    today = date.today().isoformat()
    for rel, heading, text in (("privacy", "Privacy policy", PRIVACY), ("about", "About", ABOUT)):
        write(f"{rel}/index.html", env.get_template("page.html").render(
            **common, heading=heading, url=f"{base}/{rel}/",
            content=text.format(date=today, name=site["name"], tagline=site["tagline"])))
        urls.append((f"{base}/{rel}/", None))

    sm = ['<?xml version="1.0" encoding="UTF-8"?>',
          '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for u, d in urls:
        sm.append(f"<url><loc>{escape(u)}</loc>" + (f"<lastmod>{d}</lastmod>" if d else "") + "</url>")
    sm.append("</urlset>")
    write("sitemap.xml", "\n".join(sm))
    write("robots.txt", f"User-agent: *\nAllow: /\nSitemap: {base}/sitemap.xml\n")
    write(".nojekyll", "")
    if site.get("custom_domain"):
        write("CNAME", site["custom_domain"].strip() + "\n")
    log(f"site built: {len(recipes)} recipes, {len(urls)} pages -> {SITE_OUT}")


if __name__ == "__main__":
    main()
