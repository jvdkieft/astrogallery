#!/usr/bin/env python3
"""Build the astro gallery: picks.yaml + Finished/*.jpg + capture sessions -> site/.

    python3 gallery.py --root /mnt/user/Telescopes [--finished DIR] [--picks FILE]
                       [--inventory FILE] [--source DIR] [--out site] [--jobs N]

--root is the telescopes share: Finished/, Gallery/picks.yaml and Source Data/. Hours and
sessions come from scanning Source Data/ (inventory.py), unless Gallery/inventory.json exists or
--inventory is given. Every path also has a GALLERY_* environment variable (see --help); the
builder container on boris uses those. Without sessions the site has no hours.

Reads only; nothing under --root is ever written. Web derivatives (600 px thumb, 2560 px
display, both progressive sRGB JPEG) and a byte copy of the full-res JPG go in site/img and
site/full. They are rebuilt only when the source file's mtime or size changes
(site/.manifest.json), so a rerun after a single new image is quick.
"""
import argparse
import datetime as dt
import html
import io
import json
import os
import shutil
import sys
from concurrent.futures import ProcessPoolExecutor

from PIL import Image, ImageCms, ImageOps

import inventory as inventory_scan
import minyaml

HERE = os.path.dirname(os.path.abspath(__file__))
THUMB, DISPLAY = 600, 2560
GEN_VERSION = 1  # bump to force every derivative to be rebuilt

SCOPES = {"S30P": "Seestar S30 Pro", "D3": "Dwarf 3"}
# How the filter shows on the site. Seestar files say IRCUT for "LP off" (broadband).
FILTERS = {("S30P", "IRCUT"): "LP off", ("S30P", "LP"): "LP"}

# Display groups, in page order. The first keyword found in a pick's `kind` wins.
GROUPS = [
    ("galaxies", "Galaxies", ("galaxy",)),
    ("emission", "Emission nebulae", ("emission",)),
    ("reflection", "Reflection nebulae", ("reflection",)),
    ("snr", "Supernova remnants", ("supernova", "snr")),
    ("planetary", "Planetary nebulae", ("planetary",)),
    ("solar", "Solar system", ("solar system", "moon", "sun", "planet")),
    ("other", "Other", ()),
]
GROUP_ORDER = ["emission", "reflection", "snr", "planetary", "galaxies", "solar", "other"]

esc = html.escape


def group_of(kind):
    k = (kind or "").lower()
    for gid, _, words in GROUPS:
        if any(w in k for w in words):
            return gid
    return "other"


def warn(msg):
    print(f"warning: {msg}", file=sys.stderr)


# ---------------------------------------------------------------- images

_SRGB = None


def srgb_bytes():
    global _SRGB
    if _SRGB is None:
        _SRGB = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    return _SRGB


def make_derivatives(job):
    """Worker: one source JPG -> thumb + display JPG + full-res copy. Returns sizes."""
    src, thumb, display, full = job
    im = Image.open(src)
    im = ImageOps.exif_transpose(im)
    icc = im.info.get("icc_profile")
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    if icc:
        try:
            im = ImageCms.profileToProfile(
                im, ImageCms.ImageCmsProfile(io.BytesIO(icc)),
                ImageCms.createProfile("sRGB"), outputMode="RGB")
        except ImageCms.PyCMSError as e:
            warn(f"{os.path.basename(src)}: ICC conversion failed ({e}), assuming sRGB")
    if im.mode != "RGB":
        im = im.convert("RGB")
    W, H = im.size
    out = {"w": W, "h": H, "bytes": os.path.getsize(src)}
    for path, edge, q, sub in ((display, DISPLAY, 88, 0), (thumb, THUMB, 82, 2)):
        r = im.copy()
        r.thumbnail((edge, edge), Image.LANCZOS)  # never upscales
        tmp = path + ".tmp"
        r.save(tmp, "JPEG", quality=q, progressive=True, optimize=True,
               subsampling=sub, icc_profile=srgb_bytes())
        os.replace(tmp, path)
        out["dw" if edge == DISPLAY else "tw"], out["dh" if edge == DISPLAY else "th"] = r.size
    shutil.copyfile(src, full + ".tmp")
    os.replace(full + ".tmp", full)
    return out


class Images:
    """Tracks every image the site needs and builds the stale ones in one parallel pass."""

    def __init__(self, src_dir, out_dir):
        self.src_dir, self.out_dir = src_dir, out_dir
        self.mpath = os.path.join(out_dir, ".manifest.json")
        try:
            with open(self.mpath) as f:
                self.manifest = json.load(f)
        except (OSError, ValueError):
            self.manifest = {}
        self.wanted = {}  # slug -> source file name

    def add(self, slug, name):
        """Register `name` (a file in Finished/) under `slug`; returns the slug or None if missing."""
        if not name:
            return None
        if not os.path.isfile(os.path.join(self.src_dir, name)):
            warn(f"missing in Finished/: {name}")
            return None
        self.wanted[slug] = name
        return slug

    def paths(self, slug):
        return (os.path.join(self.out_dir, "img", f"{slug}-{THUMB}.jpg"),
                os.path.join(self.out_dir, "img", f"{slug}-{DISPLAY}.jpg"),
                os.path.join(self.out_dir, "full", f"{slug}.jpg"))

    def build(self, jobs):
        os.makedirs(os.path.join(self.out_dir, "img"), exist_ok=True)
        os.makedirs(os.path.join(self.out_dir, "full"), exist_ok=True)
        todo = []
        for slug, name in self.wanted.items():
            st = os.stat(os.path.join(self.src_dir, name))
            key = {"src": name, "mtime": st.st_mtime, "size": st.st_size, "v": GEN_VERSION}
            old = self.manifest.get(slug, {})
            if {k: old.get(k) for k in key} != key or not all(map(os.path.exists, self.paths(slug))):
                todo.append((slug, key))
        if todo:
            print(f"images: building {len(todo)} of {len(self.wanted)}", file=sys.stderr)
            with ProcessPoolExecutor(max_workers=jobs) as ex:
                futs = [(slug, key, ex.submit(make_derivatives,
                         (os.path.join(self.src_dir, key["src"]), *self.paths(slug))))
                        for slug, key in todo]
                for slug, key, fut in futs:
                    self.manifest[slug] = {**key, **fut.result()}
                    print(f"  {slug}", file=sys.stderr)
        else:
            print(f"images: all {len(self.wanted)} up to date", file=sys.stderr)
        # drop derivatives of images no longer picked
        for slug in list(self.manifest):
            if slug not in self.wanted:
                for p in self.paths(slug):
                    if os.path.exists(p):
                        os.remove(p)
                del self.manifest[slug]
        with open(self.mpath + ".tmp", "w") as f:
            json.dump(self.manifest, f, indent=1, sort_keys=True)
        os.replace(self.mpath + ".tmp", self.mpath)

    def info(self, slug):
        return self.manifest[slug]


# ---------------------------------------------------------------- data

def input_paths(root=None, finished=None, picks=None, inventory=None, source=None):
    """(finished, picks, inventory, source) from the arguments, else GALLERY_* variables, else
    under root (GALLERY_ROOT). inventory is None unless given or <root>/Gallery/inventory.json
    exists; it wins over scanning source. watch.py uses this too."""
    env = os.environ.get
    root = root or env("GALLERY_ROOT")
    finished = finished or env("GALLERY_FINISHED")
    picks = picks or env("GALLERY_PICKS")
    inventory = inventory or env("GALLERY_INVENTORY")
    source = source or env("GALLERY_SOURCE")
    if root:
        finished = finished or os.path.join(root, "Finished")
        picks = picks or os.path.join(root, "Gallery", "picks.yaml")
        source = source or os.path.join(root, "Source Data")
        inv = os.path.join(root, "Gallery", "inventory.json")
        inventory = inventory or (inv if os.path.isfile(inv) else None)
    if not (finished and picks):
        sys.exit("say where the telescopes share is: --root DIR or GALLERY_ROOT "
                 "(or both --finished and --picks)")
    return finished, picks, inventory, source


def load_inventory(inv_path, source):
    """Session list from inv_path if set, else from scanning source; None when neither has any."""
    if inv_path:
        with open(inv_path) as f:
            sessions = json.load(f)
        if not sessions:
            sys.exit(f"{inv_path} has no sessions; delete it to scan Source Data instead")
        print(f"sessions: {len(sessions)} from {inv_path}", file=sys.stderr)
        return sessions
    if source and os.path.isdir(source):
        sessions = inventory_scan.scan(source)
        if sessions:
            print(f"sessions: {len(sessions)} from {source}", file=sys.stderr)
            return sessions
        warn(f"no capture sessions found in {source}; building without session data")
    else:  # images still publish; hours, nights and session tables are left out
        warn(f"no inventory.json and no Source Data folder ({source}); building without session data")
    return None


def load_targets(picks_path, inventory, images):
    picks = minyaml.load(picks_path)
    by_key = {}
    for s in inventory or []:
        by_key.setdefault(f"{s['scope']}|{s['target']}", []).append(s)

    targets, seen = [], set()
    for p in picks.get("targets") or []:
        tid = str(p.get("id") or "")
        if not tid or tid in seen:
            warn(f"pick without a unique id: {p.get('name')!r}")
            continue
        seen.add(tid)
        if p.get("show") is False:
            continue
        t = {"id": tid, "name": p.get("name") or tid, "kind": p.get("kind") or "",
             "goal": p.get("goal_h"), "group": group_of(p.get("kind"))}

        t["natural"] = images.add(f"{tid}-natural", p.get("natural"))
        t["hoo"] = images.add(f"{tid}-hoo", p.get("hoo"))
        if not (t["natural"] or t["hoo"]):
            warn(f"{tid}: no natural or hoo image, skipped")
            continue
        inset = p.get("inset") or {}
        if isinstance(inset, str):
            inset = {"natural": inset}
        t["inset"] = {"natural": images.add(f"{tid}-inset-natural", inset.get("natural")),
                      "hoo": images.add(f"{tid}-inset-hoo", inset.get("hoo"))}
        alts = p.get("alt") or []
        if isinstance(alts, str):
            alts = [alts]
        t["alts"] = [s for s in (images.add(f"{tid}-alt{i + 1}", a) for i, a in enumerate(alts)) if s]
        t["src"] = dict(images.wanted)  # slug -> source name, for captions

        sessions = []
        for key in p.get("sessions") or []:
            if inventory is not None and key not in by_key:
                warn(f"{tid}: session key {key!r} not in the capture sessions")
            sessions += [s for s in by_key.get(key, []) if s.get("frames", 0) > 0]
        sessions.sort(key=lambda s: (s["night"], s["scope"], s.get("exp", 0)))
        t["sessions"] = sessions
        t["hours"] = round(sum(s["hours"] for s in sessions), 2)
        t["nights"] = sorted({s["night"] for s in sessions})
        t["scopes"] = [k for k in SCOPES if any(s["scope"] == k for s in sessions)]
        t["filters"] = list(dict.fromkeys(filter_name(s) for s in sessions))
        targets.append(t)
    return targets


def filter_name(s):
    return FILTERS.get((s["scope"], s.get("filter")), s.get("filter") or "?")


# ---------------------------------------------------------------- html

def fmt_h(h):
    return f"{h:.1f} h"


def fmt_exp(e):
    return f"{e:g} s"


def date_range(nights):
    if not nights:
        return ""
    return nights[0] if len(nights) == 1 else f"{nights[0]} – {nights[-1]}"


def depth_bar(t):
    if not t["sessions"]:
        return ""
    if not t["goal"]:
        return f'<div class="depth"><span class="depth-text">{fmt_h(t["hours"])}</span></div>'
    pct = min(100, round(100 * t["hours"] / t["goal"]))
    done = t["hours"] >= t["goal"]
    label = f'{fmt_h(t["hours"])} of {t["goal"]:g} h goal' + (" · done" if done else "")
    return (f'<div class="depth{" done" if done else ""}" title="{esc(label)}">'
            f'<div class="bar"><span style="width:{pct}%"></span></div>'
            f'<span class="depth-text">{esc(label)}</span></div>')


def page(title, body, generated, css="style.css", extra_head=""):
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<title>{esc(title)}</title>
<link rel="stylesheet" href="{css}">
{extra_head}</head>
<body>
{body}
<footer class="site-footer">Built {generated}</footer>
</body>
</html>
"""


def card(t, images):
    slug = t["hoo"] or t["natural"]
    i = images.info(slug)
    meta = []
    if t["scopes"]:
        meta.append(" + ".join(SCOPES[s] for s in t["scopes"]))
    if t["nights"]:
        n = len(t["nights"])
        meta.append(f'{n} night{"s" if n != 1 else ""} · {date_range(t["nights"])}')
    if t["filters"]:
        meta.append(", ".join(t["filters"]))
    badges = "".join(f'<span class="badge">{b}</span>' for b in
                     (["HOO"] if t["hoo"] else []) + (["mosaic"] if "mosaic" in t["kind"].lower() else []))
    return f"""<a class="card" href="{t['id']}.html">
  <img src="img/{slug}-{THUMB}.jpg" width="{i['tw']}" height="{i['th']}" alt="{esc(t['name'])}" loading="lazy">
  <div class="card-body">
    <h3>{esc(t['name'])}</h3>
    <p class="kind">{esc(t['kind'])}{badges}</p>
    {depth_bar(t)}
    {"".join(f'<p class="meta">{esc(m)}</p>' for m in meta)}
  </div>
</a>"""


def index_html(targets, images, generated):
    present = [g for g in GROUP_ORDER if any(t["group"] == g for t in targets)]
    names = {gid: label for gid, label, _ in GROUPS}
    chips = ['<input type="radio" name="f" id="f-all" class="sr" checked><label for="f-all">All</label>']
    rules = []
    for g in present:
        chips.append(f'<input type="radio" name="f" id="f-{g}" class="sr"><label for="f-{g}">{names[g]}</label>')
        rules.append(f"body:has(#f-{g}:checked) .group:not(#g-{g}){{display:none}}")
    sections = []
    for g in present:
        cards = "\n".join(card(t, images) for t in targets if t["group"] == g)
        sections.append(f'<section class="group" id="g-{g}"><h2>{names[g]}</h2>\n<div class="grid">\n{cards}\n</div></section>')
    total = sum(t["hours"] for t in targets)
    body = f"""<header class="site-header">
  <h1>Astro gallery</h1>
  <p class="sub">{len(targets)} targets · {total:.0f} h of stacked integration · Seestar S30 Pro and Dwarf 3, Bortle 6–7</p>
  <nav class="filters" aria-label="Filter by kind">{"".join(chips)}</nav>
</header>
<main>
{chr(10).join(sections)}
</main>"""
    return page("Astro gallery", body, generated, extra_head=f"<style>{' '.join(rules)}</style>\n")


def figure(slug, images, cls, caption):
    i = images.info(slug)
    mb = i["bytes"] / 1e6
    return f"""<figure class="{cls}">
  <a href="img/{slug}-{DISPLAY}.jpg"><img src="img/{slug}-{DISPLAY}.jpg"
    srcset="img/{slug}-{THUMB}.jpg {i['tw']}w, img/{slug}-{DISPLAY}.jpg {i['dw']}w"
    sizes="(max-width: 1200px) 100vw, 1200px" width="{i['dw']}" height="{i['dh']}" alt="{esc(caption)}"></a>
  <figcaption><span class="file">{esc(caption)}</span>
    <a class="dl" href="full/{slug}.jpg" download="{esc(caption)}">Full resolution · {i['w']}×{i['h']} · {mb:.1f} MB</a></figcaption>
</figure>"""


def viewer(vid, nat, hoo, t, images):
    """A natural/HOO pair with a CSS-only toggle (HOO checked first); a single image otherwise."""
    if not (nat and hoo):
        s = nat or hoo
        return f'<div class="viewer single">{figure(s, images, "shown", t["src"][s])}</div>'
    return f"""<div class="viewer">
  <input type="radio" name="{vid}" id="{vid}-hoo" class="sr r-hoo" checked>
  <input type="radio" name="{vid}" id="{vid}-nat" class="sr r-nat">
  <div class="toggle" role="group" aria-label="Rendering">
    <label for="{vid}-hoo" class="l-hoo">HOO</label><label for="{vid}-nat" class="l-nat">Natural</label>
  </div>
  <div class="frames">
    {figure(hoo, images, "f-hoo", t["src"][hoo])}
    {figure(nat, images, "f-nat", t["src"][nat])}
  </div>
</div>"""


def target_html(t, prev, nxt, images, generated):
    parts = [f"""<header class="target-header">
  <nav class="crumbs"><a href="index.html">← Gallery</a>
    <span class="pn">{f'<a href="{prev["id"]}.html">‹ {esc(prev["name"])}</a>' if prev else ''}
    {f'<a href="{nxt["id"]}.html">{esc(nxt["name"])} ›</a>' if nxt else ''}</span></nav>
  <h1>{esc(t['name'])}</h1>
  <p class="kind">{esc(t['kind'])}</p>
</header>
<main class="target">""", viewer("main", t["natural"], t["hoo"], t, images)]

    facts = []
    if t["sessions"]:
        facts.append(("Integration", depth_bar(t)))
        facts.append(("Nights", f'{len(t["nights"])} · {date_range(t["nights"])}'))
        facts.append(("Scopes", esc(" + ".join(SCOPES[s] for s in t["scopes"]))))
        facts.append(("Filters", esc(", ".join(t["filters"]))))
    if facts:
        parts.append('<dl class="facts">' + "".join(f"<div><dt>{k}</dt><dd>{v}</dd></div>" for k, v in facts) + "</dl>")

    ins = t["inset"]
    if ins["natural"] or ins["hoo"]:
        parts.append('<section><h2>Detail</h2>' + viewer("inset", ins["natural"], ins["hoo"], t, images) + "</section>")

    if t["alts"]:
        items = []
        for s in t["alts"]:
            i = images.info(s)
            items.append(f"""<figure>
  <a href="img/{s}-{DISPLAY}.jpg"><img src="img/{s}-{THUMB}.jpg" width="{i['tw']}" height="{i['th']}" alt="{esc(t['src'][s])}" loading="lazy"></a>
  <figcaption><span class="file">{esc(t['src'][s])}</span>
    <a class="dl" href="full/{s}.jpg" download="{esc(t['src'][s])}">Full resolution · {i['w']}×{i['h']}</a></figcaption>
</figure>""")
        parts.append('<section><h2>Other versions</h2><div class="alts">' + "".join(items) + "</div></section>")

    if t["sessions"]:
        rows = "".join(
            f'<tr><td>{s["night"]}</td><td><span class="long">{SCOPES.get(s["scope"], s["scope"])}</span><span class="short">{s["scope"]}</span></td><td>{esc(filter_name(s))}</td>'
            f'<td class="num">{s["frames"]} × {fmt_exp(s["exp"])}</td><td class="num">{s["hours"]:.2f}</td></tr>'
            for s in t["sessions"])
        parts.append(f"""<section><h2>Sessions</h2>
<div class="table-wrap"><table class="sessions">
<thead><tr><th>Night</th><th>Scope</th><th>Filter</th><th class="num">Frames × exp</th><th class="num">Hours</th></tr></thead>
<tbody>{rows}</tbody>
<tfoot><tr><td colspan="4">Total stacked</td><td class="num">{t['hours']:.2f}</td></tr></tfoot>
</table></div>
<p class="note">Frames the scope stacked, not frames taken. Night = local evening the session started.</p>
</section>""")
    parts.append("</main>")
    return page(f"{t['name']} · Astro gallery", "\n".join(parts), generated)


# ---------------------------------------------------------------- main

def write(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    env = os.environ.get
    ap.add_argument("--root", help="the telescopes share (default $GALLERY_ROOT)")
    ap.add_argument("--finished", help="folder of finished JPGs (default $GALLERY_FINISHED or <root>/Finished)")
    ap.add_argument("--picks", help="default $GALLERY_PICKS or <root>/Gallery/picks.yaml")
    ap.add_argument("--inventory", help="sessions file; default $GALLERY_INVENTORY or "
                    "<root>/Gallery/inventory.json if it exists, else Source Data is scanned")
    ap.add_argument("--source", help="Source Data folder to scan (default $GALLERY_SOURCE or <root>/Source Data)")
    ap.add_argument("--out", default=env("GALLERY_OUT") or os.path.join(HERE, "site"))
    ap.add_argument("--jobs", type=int, default=int(env("GALLERY_JOBS") or 0) or os.cpu_count() or 4)
    a = ap.parse_args()

    finished, picks, inv_path, source = input_paths(a.root, a.finished, a.picks, a.inventory, a.source)
    images = Images(finished, a.out)
    targets = load_targets(picks, load_inventory(inv_path, source), images)
    images.build(a.jobs)

    generated = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    ordered = [t for g in GROUP_ORDER for t in targets if t["group"] == g]
    keep = {"index.html", "style.css", ".manifest.json", "img", "full"}
    write(os.path.join(a.out, "index.html"), index_html(ordered, images, generated))
    for n, t in enumerate(ordered):
        prev = ordered[n - 1] if n else None
        nxt = ordered[n + 1] if n + 1 < len(ordered) else None
        write(os.path.join(a.out, f"{t['id']}.html"), target_html(t, prev, nxt, images, generated))
        keep.add(f"{t['id']}.html")
    shutil.copyfile(os.path.join(HERE, "static", "style.css"), os.path.join(a.out, "style.css"))
    for name in os.listdir(a.out):  # pages of targets that were removed or hidden
        if name.endswith(".html") and name not in keep:
            os.remove(os.path.join(a.out, name))

    print(f"site: {len(ordered)} targets -> {a.out}", file=sys.stderr)
    for t in ordered:
        goal = f"/{t['goal']:g}" if t["goal"] else ""
        print(f"  {t['id']:16} {t['hours']:5.2f}{goal:>3} h  {len(t['nights'])} nights", file=sys.stderr)


if __name__ == "__main__":
    main()
