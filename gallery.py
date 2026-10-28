#!/usr/bin/env python3
"""Build the astro gallery: Finished/*.jpg (+ picks.yaml overrides) + capture sessions -> site/.

    python3 gallery.py --root /mnt/user/Telescopes [--finished DIR] [--picks FILE]
                       [--inventory FILE | --no-inventory] [--source DIR] [--nina DIR]
                       [--out site] [--jobs N]

--root is the telescopes share: Finished/, Gallery/picks.yaml, Seestar S30 Pro/, Dwarf 3/ and
NINA/. Targets and their images come from the Finished/ file names (autopicks.py); picks.yaml,
if present, overrides them field by field. Hours and sessions come from scanning
Seestar S30 Pro/, Dwarf 3/ and NINA/ (inventory.py), unless Gallery/inventory.json exists or --inventory is given. Every path also has a GALLERY_* environment variable (see --help); the
builder container on boris uses those. Without sessions the site has no hours.

Reads only; nothing under --root is ever written. Web derivatives (600 px thumb, 2560 px
display, both progressive sRGB JPEG) and a copy of the full-res JPG go in site/img and
site/full. The copy keeps the picture data byte for byte but not the metadata (Exif, XMP,
IPTC, comments): a JPG exported with a location or a camera serial in it must not publish them.
Derivatives are rebuilt only when the source file's mtime or size changes
(site/.manifest.json), so a rerun after a single new image is quick.
"""
import argparse
import math
import datetime as dt
import html
import io
import json
import os
import re
import shutil
import sys
from concurrent.futures import ProcessPoolExecutor

from PIL import Image, ImageCms, ImageOps

import autopicks
import inventory as inventory_scan
import minyaml

HERE = os.path.dirname(os.path.abspath(__file__))
THUMB, DISPLAY = 600, 2560
GEN_VERSION = 2  # bump to force every derivative to be rebuilt (2: full/ copies lose their metadata)

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
    ("clusters", "Star clusters", ("cluster",)),
    ("solar", "Solar system", ("solar system", "moon", "sun", "planet")),
    ("other", "Other", ()),
]
GROUP_ORDER = ["emission", "reflection", "snr", "planetary", "galaxies", "clusters", "solar", "other"]

RECENT = 6  # cards in the "What's new" row
NEW_DAYS = 14  # a target updated this recently gets a "new" badge

esc = html.escape


def group_of(kind):
    k = (kind or "").lower()
    for gid, _, words in GROUPS:
        if any(w in k for w in words):
            return gid
    return "other"


WARNINGS = []  # everything warn() said this build, for status.html


def warn(msg):
    WARNINGS.append(msg)
    print(f"warning: {msg}", file=sys.stderr)


# ---------------------------------------------------------------- images

_SRGB = None


def srgb_bytes():
    global _SRGB
    if _SRGB is None:
        _SRGB = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    return _SRGB


ORIENTATION = 0x0112  # the one Exif tag the full-res copy keeps


def strip_metadata(src, dst, orientation=None):
    """Copy the JPEG at src to dst without its metadata. The picture data, the JFIF and Adobe
    headers and the ICC profile are kept byte for byte; every other APPn segment (Exif, XMP,
    IPTC/Photoshop, MPF previews) and comments are dropped, also between the scans of a
    progressive file, and so is anything after the EOI marker (motion-photo videos and maker
    trailers live there). An orientation of 2..8 is written back as a one-tag Exif block, so the
    copy shows the same way up as the original. Raises ValueError for anything that is not a
    JPEG stream."""
    with open(src, "rb") as f:
        data = f.read()
    n = len(data)
    if data[:2] != b"\xff\xd8":
        raise ValueError("not a JPEG")
    exif = b""
    if isinstance(orientation, int) and 2 <= orientation <= 8:  # the values exif_transpose honours
        tiff = (b"II*\0" + (8).to_bytes(4, "little") + (1).to_bytes(2, "little")
                + ORIENTATION.to_bytes(2, "little") + (3).to_bytes(2, "little") + (1).to_bytes(4, "little")
                + orientation.to_bytes(2, "little") + b"\0\0" + b"\0\0\0\0")
        payload = b"Exif\0\0" + tiff
        exif = b"\xff\xe1" + (len(payload) + 2).to_bytes(2, "big") + payload
    out = [b"\xff\xd8"]
    i = 2
    while True:
        while i + 1 < n and data[i] == 0xFF and data[i + 1] == 0xFF:  # fill bytes before a marker
            i += 1
        if i + 1 >= n or data[i] != 0xFF:
            raise ValueError("corrupt JPEG header")
        marker = data[i + 1]
        if marker == 0xD9:  # EOI: the picture ends here, whatever follows is not it
            out.append(b"\xff\xd9")
            break
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:  # TEM, RSTn: no length
            out.append(data[i:i + 2])
            i += 2
            continue
        ln = int.from_bytes(data[i + 2:i + 4], "big") if i + 4 <= n else 0
        if ln < 2 or i + 2 + ln > n:
            raise ValueError("corrupt JPEG header")
        seg = data[i:i + 2 + ln]
        payload = seg[4:]
        if 0xE0 <= marker <= 0xEF or marker == 0xFE:  # APPn or COM
            keep = (marker == 0xE0 or (marker == 0xE2 and payload.startswith(b"ICC_PROFILE\0"))
                    or (marker == 0xEE and payload.startswith(b"Adobe")))
        else:  # tables, frame and scan headers, restart interval, ...
            keep = True
        if keep:
            if exif and marker != 0xE0:  # no JFIF header: Exif goes first instead
                out.append(exif)
                exif = b""
            out.append(seg)
            if exif and marker == 0xE0:  # JFIF must stay right after SOI; Exif follows it
                out.append(exif)
                exif = b""
        i += 2 + ln
        if marker != 0xDA:
            continue
        # SOS: entropy-coded data up to the next marker. FF00 is a stuffed byte, FFD0-D7 a
        # restart marker and FFFF a fill byte; none of them ends the scan.
        j = i
        while True:
            j = data.find(b"\xff", j)
            if j < 0 or j + 1 >= n:  # no EOI at all: keep what there is, it is all picture
                j = n
                break
            m = data[j + 1]
            if m == 0 or 0xD0 <= m <= 0xD7:
                j += 2
            elif m == 0xFF:
                j += 1
            else:
                break
        out.append(data[i:j])
        i = j
        if i >= n:
            break
    with open(dst, "wb") as f:
        f.write(b"".join(out))


def make_derivatives(job):
    """Worker: one source JPG -> thumb + display JPG + full-res copy. Returns sizes."""
    src, thumb, display, full = job
    im = Image.open(src)
    orientation = im.getexif().get(ORIENTATION)
    im = ImageOps.exif_transpose(im)
    icc = im.info.get("icc_profile")
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    warnings = []  # a worker process: warn() here would never reach the parent's status.html
    if icc:
        try:
            im = ImageCms.profileToProfile(
                im, ImageCms.ImageCmsProfile(io.BytesIO(icc)),
                ImageCms.createProfile("sRGB"), outputMode="RGB")
        except (ImageCms.PyCMSError, OSError, ValueError) as e:  # a garbage profile is an OSError
            warnings.append(f"{os.path.basename(src)}: ICC conversion failed ({e}), assuming sRGB")
    if im.mode != "RGB":
        im = im.convert("RGB")
    W, H = im.size
    out = {"w": W, "h": H, "warnings": warnings}
    for path, edge, q, sub in ((display, DISPLAY, 88, 0), (thumb, THUMB, 82, 2)):
        r = im.copy()
        for k in ("comment", "xmp"):  # Pillow carries a COM segment over; nothing else from the source
            r.info.pop(k, None)
        r.thumbnail((edge, edge), Image.LANCZOS)  # never upscales
        tmp = path + ".tmp"
        r.save(tmp, "JPEG", quality=q, progressive=True, optimize=True,
               subsampling=sub, icc_profile=srgb_bytes())
        os.replace(tmp, path)
        out["dw" if edge == DISPLAY else "tw"], out["dh" if edge == DISPLAY else "th"] = r.size
    strip_metadata(src, full + ".tmp", orientation)
    os.replace(full + ".tmp", full)
    out["bytes"] = os.path.getsize(full)
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
        self.failed = set()  # slugs with no usable derivative after build()

    def add(self, slug, name):
        """Register `name` (a file in Finished/) under `slug`; returns the slug or None if missing."""
        if not name:
            return None
        name = str(name)
        if "/" in name or os.sep in name or name in (".", ".."):
            warn(f"not a plain file name in Finished/: {name!r}")
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
                    try:
                        res = fut.result()
                    except Exception as e:  # unreadable or half-copied JPG: keep the rest of the site going
                        old = self.manifest.get(slug)
                        # only a derivative set of this generation may stand in: an older full/
                        # copy would still carry the metadata GEN_VERSION 2 strips
                        if old and old.get("v") == GEN_VERSION and all(map(os.path.exists, self.paths(slug))):
                            warn(f"cannot read {key['src']} ({e}); still showing {old['src']}")
                            self.wanted[slug] = old["src"]
                        else:
                            warn(f"cannot read {key['src']} ({e}); left out")
                            self.failed.add(slug)
                            del self.wanted[slug]
                        continue
                    for w in res.pop("warnings", ()):
                        warn(w)
                    self.manifest[slug] = {**key, **res}
                    print(f"  {slug}", file=sys.stderr)
        else:
            print(f"images: all {len(self.wanted)} up to date", file=sys.stderr)
        self.save_manifest()

    def prune(self):
        """Drop the derivatives of images no longer picked. Called once the pages are written, so a
        build that dies in between never leaves a page pointing at a deleted image."""
        for slug in list(self.manifest):
            if slug not in self.wanted:
                for p in self.paths(slug):
                    if os.path.exists(p):
                        os.remove(p)
                del self.manifest[slug]
        self.save_manifest()

    def save_manifest(self):
        with open(self.mpath + ".tmp", "w") as f:
            json.dump(self.manifest, f, indent=1, sort_keys=True)
        os.replace(self.mpath + ".tmp", self.mpath)

    def info(self, slug):
        return self.manifest[slug]


# ---------------------------------------------------------------- data

def input_paths(root=None, finished=None, picks=None, inventory=None, source=None, nina=None):
    """{finished, picks, inventory, source, nina, reject} from the arguments, else GALLERY_*
    variables, else under root (GALLERY_ROOT). inventory is None unless given or
    <root>/Gallery/inventory.json exists; it wins over scanning source and nina. inventory
    "none" forces the scan. watch.py and autopicks.py use this too."""
    env = os.environ.get
    root = root or env("GALLERY_ROOT")
    finished = finished or env("GALLERY_FINISHED")
    picks = picks or env("GALLERY_PICKS")
    inventory = inventory or env("GALLERY_INVENTORY")
    source = source or env("GALLERY_SOURCE")
    nina = nina or env("GALLERY_NINA")
    reject = env("GALLERY_NINA_REJECT")
    if root:
        finished = finished or os.path.join(root, "Finished")
        picks = picks or os.path.join(root, "Gallery", "picks.yaml")
        source = source or root
        nina = nina or os.path.join(root, "NINA")
        reject = reject or inventory_scan.find_reject(root, nina)
        inv = os.path.join(root, "Gallery", "inventory.json")
        inventory = inventory or (inv if os.path.isfile(inv) else None)
    if not finished:
        sys.exit("say where the telescopes share is: --root DIR or GALLERY_ROOT (or --finished)")
    if str(inventory).lower() == "none":
        inventory = None
    return dict(finished=finished, picks=picks, inventory=inventory, source=source, nina=nina, reject=reject)


def load_inventory(paths):
    """Session list from paths["inventory"] if set, else from scanning the Seestar, Dwarf and NINA
    folders;
    None when neither has any."""
    inv_path, source, nina = paths["inventory"], paths["source"], paths["nina"]
    if inv_path:
        with open(inv_path) as f:
            sessions = json.load(f)
        if not sessions:
            sys.exit(f"{inv_path} has no sessions; delete it to scan the capture folders instead")
        try:
            for s in sessions:
                s["scope"], s["target"], s["night"] = str(s.get("scope") or ""), str(s.get("target") or ""), str(s.get("night") or "")
                s["frames"], s["exp"], s["hours"] = int(s.get("frames") or 0), float(s.get("exp") or 0), float(s.get("hours") or 0)
        except (TypeError, ValueError, AttributeError) as e:
            sys.exit(f"{inv_path}: unusable session {s!r} ({e})")
        print(f"sessions: {len(sessions)} from {inv_path}", file=sys.stderr)
        return sessions
    have = [p for p, ok in ((source, inventory_scan.has_captures(source)),
                            (nina, nina and os.path.isdir(nina))) if ok]
    if have:
        sessions = inventory_scan.scan(source, nina, paths["reject"])
        for problem in inventory_scan.PROBLEMS:
            warn(problem)
        if sessions:
            hours = sum(s["hours"] for s in sessions)
            print(f"sessions: {len(sessions)} ({hours:.1f} h) from {' + '.join(have)}"
                  + (f", minus {paths['reject']}" if paths["reject"] and nina in have else ""), file=sys.stderr)
            return sessions
        warn(f"no capture sessions found in {' or '.join(have)}; building without session data")
    else:  # images still publish; hours, nights and session tables are left out
        warn(f"no inventory.json and no Seestar S30 Pro, Dwarf 3 or NINA folder in {source}; building without session data")
    return None


def load_picks(picks_path):
    """The targets list of picks.yaml; [] when the file is missing or empty."""
    if not (picks_path and os.path.isfile(picks_path)):
        if picks_path:
            print(f"picks: no {picks_path}, every target is automatic", file=sys.stderr)
        return []
    return (minyaml.load(picks_path) or {}).get("targets") or []


def utf8_name(name):
    """False (with a warning) for a file name that is not valid UTF-8: it could not be written into a page."""
    try:
        name.encode("utf-8")
        return True
    except UnicodeEncodeError:
        warn(f"file name is not UTF-8, skipped: {name!r}")
        return False


def load_targets(picks_path, inventory, images):
    """picks.yaml merged with the automatic picks from the Finished/ file names (autopicks.py)."""
    try:
        files = sorted(e.name for e in os.scandir(images.src_dir) if e.is_file() and utf8_name(e.name))
    except OSError as e:
        sys.exit(f"cannot read {images.src_dir}: {e}")
    picks = load_picks(picks_path)
    entries = autopicks.resolve(picks, files, inventory)
    print(f"picks: {len(picks)} from picks.yaml, {len(entries) - len(picks)} new from Finished/ names",
          file=sys.stderr)
    by_key = {}
    for s in inventory or []:
        by_key.setdefault(f"{s['scope']}|{s['target']}", []).append(s)

    targets, seen = [], set()
    for p in entries:
        tid = str(p.get("id") or "")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", tid) or tid in seen:  # it names files and pages
            warn(f"pick without a usable, unique id: {p.get('id')!r} ({p.get('name')!r})")
            continue
        seen.add(tid)
        if p.get("show") is False:
            continue
        goal = p.get("goal_h")
        try:
            goal = float(goal) if goal not in (None, "") else None
        except (TypeError, ValueError):
            warn(f"{tid}: goal_h {goal!r} is not a number, ignored")
            goal = None
        kind = str(p.get("kind") or "")
        t = {"id": tid, "name": str(p.get("name") or tid), "kind": kind, "goal": goal, "group": group_of(kind)}

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
        t["src"] = images.wanted  # slug -> source name, for captions

        sessions = []
        keys = p.get("sessions") or []
        if isinstance(keys, str):
            keys = [keys]
        for key in keys:
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


def drop_failed(targets, images):
    """Targets without the images that could not be read; a target left with no main image goes."""
    f, out = images.failed, []
    for t in targets:
        t["natural"] = None if t["natural"] in f else t["natural"]
        t["hoo"] = None if t["hoo"] in f else t["hoo"]
        if not (t["natural"] or t["hoo"]):
            warn(f"{t['id']}: no readable natural or hoo image, skipped")
            continue
        t["inset"] = {r: (x if x not in f else None) for r, x in t["inset"].items()}
        t["alts"] = [x for x in t["alts"] if x not in f]
        out.append(t)
    return out


def set_updated(targets, images):
    """t["updated"] (YYYY-MM-DD): the latest night in the names of the images the target shows,
    or the file's modification date for a name without nights (a picks.yaml file)."""
    for t in targets:
        days = []
        for slug in [t["natural"], t["hoo"], *t["inset"].values(), *t["alts"]]:
            if not slug:
                continue
            name = images.wanted[slug]
            p = autopicks.parse(name)
            if p:
                days.append(max(p["nights"]))
            else:
                mtime = os.path.getmtime(os.path.join(images.src_dir, name))
                days.append(dt.date.fromtimestamp(mtime).strftime("%Y%m%d"))
        d = max(days)
        t["updated"] = f"{d[:4]}-{d[4:6]}-{d[6:]}"


def is_new(t, today):
    return (today - dt.date.fromisoformat(t["updated"])).days <= NEW_DAYS


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


FAVICON = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'%3E"
           "%3Ccircle cx='8' cy='8' r='7' fill='%230b0d12'/%3E%3Ccircle cx='8' cy='8' r='2.2' fill='%237fb4ff'/%3E"
           "%3Ccircle cx='3.5' cy='4' r='.7' fill='%23e6e8ee'/%3E%3Ccircle cx='12' cy='11.5' r='.6' fill='%23e6e8ee'/%3E%3C/svg%3E")


def page(title, body, generated, css="style.css", extra_head=""):
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<title>{esc(title)}</title>
<link rel="stylesheet" href="{css}">
<link rel="icon" href="{FAVICON}">
{extra_head}</head>
<body>
{body}
<footer class="site-footer">Built {generated} · <a href="about.html">How these are made</a> · <a href="stats.html">Stats</a> · <a href="status.html">build status</a></footer>
</body>
</html>
"""


def card_kind(kind):
    """The kind line of a card; "mosaic" is left to the badge."""
    return ", ".join(w for w in (x.strip() for x in kind.split(",")) if w.lower() != "mosaic")


def card(t, images, today, dated=False):
    """One index card; `dated` adds an "Updated" line (the What's new and Newest first views)."""
    slug = t["hoo"] or t["natural"]
    i = images.info(slug)
    meta = [f'Updated {t["updated"]}'] if dated else []
    if t["scopes"]:
        meta.append(" + ".join(SCOPES[s] for s in t["scopes"]))
    if t["nights"]:
        n = len(t["nights"])
        meta.append(f'{n} night{"s" if n != 1 else ""} · {date_range(t["nights"])}')
    if t["filters"]:
        meta.append(", ".join(t["filters"]))
    badges = "".join(f'<span class="badge">{b}</span>' for b in
                     (["HOO"] if t["hoo"] else []) + (["mosaic"] if "mosaic" in t["kind"].lower() else []))
    if is_new(t, today):
        badges += '<span class="badge new">new</span>'
    return f"""<a class="card" href="{esc(t['id'])}.html">
  <img src="img/{slug}-{THUMB}.jpg" width="{i['tw']}" height="{i['th']}" alt="{esc(t['name'])}" loading="lazy">
  <div class="card-body">
    <h3>{esc(t['name'])}</h3>
    <p class="kind">{esc(card_kind(t['kind']))}{f' · {card_distance(t)}' if card_distance(t) else ''}{badges}</p>
    {depth_bar(t)}
    {"".join(f'<p class="meta{" updated" if dated and not n else ""}">{esc(m)}</p>' for n, m in enumerate(meta))}
  </div>
</a>"""


def lightline(targets):
    """'The light in these pictures is between 444 years and 70 million years old.' (deep sky only)"""
    ds = [(DISTANCE[t["id"]][0], t) for t in targets if t["id"] in DISTANCE]
    if len(ds) < 2:
        return ""
    (lo, _), (hi, far) = min(ds, key=lambda x: x[0]), max(ds, key=lambda x: x[0])
    age = lambda ly: ly_text(ly).replace("light-years", "years") if ly >= 1_000_000 else f"{ly:,} years"
    return (f"The light in these pictures set out between {age(lo)} and {age(hi)} ago; the oldest is "
            f'<a href="{esc(far["id"])}.html">{esc(far["name"])}</a>.')


def index_html(targets, images, generated, today):
    present = [g for g in GROUP_ORDER if any(t["group"] == g for t in targets)]
    names = {gid: label for gid, label, _ in GROUPS}
    chips = ['<input type="radio" name="f" id="f-all" class="sr" checked><label for="f-all">All</label>']
    rules = []
    for g in present + ["newest"]:
        label = "Newest first" if g == "newest" else names[g]
        chips.append(f'<input type="radio" name="f" id="f-{g}" class="sr"><label for="f-{g}">{label}</label>')
        rules.append(f"body:has(#f-{g}:checked) .group:not(#g-{g}){{display:none}}")
    # "All" shows the What's new row above the groups; "Newest first" shows every target by date instead
    rules.append("#g-newest{display:none} body:has(#f-newest:checked) #g-newest{display:block}")
    by_date = sorted(targets, key=lambda t: t["updated"], reverse=True)  # stable: page order breaks ties
    recent = "\n".join(card(t, images, today, dated=True) for t in by_date[:RECENT])
    sections = [f'<section class="group" id="g-recent"><h2>What\'s new</h2>\n<div class="grid">\n{recent}\n</div></section>']
    for g in present:
        cards = "\n".join(card(t, images, today) for t in targets if t["group"] == g)
        sections.append(f'<section class="group" id="g-{g}"><h2>{names[g]}</h2>\n<div class="grid">\n{cards}\n</div></section>')
    newest = "\n".join(card(t, images, today, dated=True) for t in by_date)
    sections.append(f'<section class="group" id="g-newest"><h2>Newest first</h2>\n<div class="grid">\n{newest}\n</div></section>')
    total = sum(t["hours"] for t in targets)
    body = f"""<header class="site-header">
  <h1>Deep sky from a Bortle 8.6 backyard</h1>
  <p class="sub">{len(targets)} targets · {total:.0f} h of stacked integration · Seestar S30 Pro and Dwarf 3 smart telescopes, narrowband filters against the suburban glow</p>
  <p class="sub">{lightline(targets)}</p>
  <p class="sub about-link"><a href="about.html">How these are made →</a></p>
  <nav class="filters" aria-label="Filter by kind">{"".join(chips)}</nav>
</header>
<main>
{chr(10).join(sections)}
</main>"""
    return page("Deep sky from a Bortle 8.6 backyard · bortle9.space", body, generated, extra_head=f"<style>{' '.join(rules)}</style>\n")


def figure(slug, images, cls, caption, alt):
    i = images.info(slug)
    mb = i["bytes"] / 1e6
    return f"""<figure class="{cls}">
  <a href="img/{slug}-{DISPLAY}.jpg"><img src="img/{slug}-{DISPLAY}.jpg"
    srcset="img/{slug}-{THUMB}.jpg {i['tw']}w, img/{slug}-{DISPLAY}.jpg {i['dw']}w"
    sizes="(max-width: 1200px) 100vw, 1200px" width="{i['dw']}" height="{i['dh']}" alt="{esc(alt)}"></a>
  <figcaption><span class="file">{esc(caption)}</span>
    <a class="dl" href="full/{slug}.jpg" download="{esc(caption)}">Full resolution · {i['w']}×{i['h']} · {mb:.1f} MB</a></figcaption>
</figure>"""


def viewer(vid, nat, hoo, t, images, what=""):
    """A natural/HOO pair with a CSS-only toggle (HOO checked first); a single image otherwise.
    `what` names the viewer in the alt text ("detail"); the file name stays in the caption."""
    alt = t["name"] + (f", {what}" if what else "")
    if not (nat and hoo):
        s = nat or hoo
        return f'<div class="viewer single">{figure(s, images, "shown", t["src"][s], alt + (", HOO" if hoo else ""))}</div>'
    return f"""<div class="viewer">
  <input type="radio" name="{vid}" id="{vid}-hoo" class="sr r-hoo" checked>
  <input type="radio" name="{vid}" id="{vid}-nat" class="sr r-nat">
  <div class="toggle" role="group" aria-label="Rendering">
    <label for="{vid}-hoo" class="l-hoo">HOO</label><label for="{vid}-nat" class="l-nat">Natural</label>
  </div>
  <div class="frames">
    {figure(hoo, images, "f-hoo", t["src"][hoo], alt + ", HOO")}
    {figure(nat, images, "f-nat", t["src"][nat], alt + ", natural")}
  </div>
</div>"""


def night_cell(night, one_year):
    """YYYY-MM-DD; the year can be dropped on phones when every night is in the same year."""
    return f'<span class="yr">{night[:5]}</span>{night[5:]}' if one_year else night


def target_html(t, prev, nxt, images, generated):
    parts = [f"""<header class="target-header">
  <nav class="crumbs"><a href="index.html">← Gallery</a>
    <span class="pn">{f'<a href="{esc(prev["id"])}.html">‹ {esc(prev["name"])}</a>' if prev else ''}
    {f'<a href="{esc(nxt["id"])}.html">{esc(nxt["name"])} ›</a>' if nxt else ''}</span></nav>
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
    facts += distance_facts(t)
    if facts:
        parts.append('<dl class="facts">' + "".join(f"<div><dt>{k}</dt><dd>{v}</dd></div>" for k, v in facts) + "</dl>")

    ins = t["inset"]
    if ins["natural"] or ins["hoo"]:
        parts.append('<section><h2>Detail</h2>' + viewer("inset", ins["natural"], ins["hoo"], t, images, "detail") + "</section>")

    if t["alts"]:
        items = []
        for n, s in enumerate(t["alts"], 1):
            i = images.info(s)
            items.append(f"""<figure>
  <a href="img/{s}-{DISPLAY}.jpg"><img src="img/{s}-{THUMB}.jpg" width="{i['tw']}" height="{i['th']}" alt="{esc(t['name'])}, other version {n}" loading="lazy"></a>
  <figcaption><span class="file">{esc(t['src'][s])}</span>
    <a class="dl" href="full/{s}.jpg" download="{esc(t['src'][s])}">Full resolution · {i['w']}×{i['h']}</a></figcaption>
</figure>""")
        parts.append('<section><h2>Other versions</h2><div class="alts">' + "".join(items) + "</div></section>")

    if t["sessions"]:
        one_year = len({n[:4] for n in t["nights"]}) == 1
        rows = "".join(
            f'<tr><td>{night_cell(esc(s["night"]), one_year)}</td><td><span class="long">{esc(SCOPES.get(s["scope"], s["scope"]))}</span><span class="short">{esc(s["scope"])}</span></td><td class="flt">{esc(filter_name(s))}</td>'
            f'<td class="num">{s["frames"]} × {fmt_exp(s["exp"])}</td><td class="num">{s["hours"]:.2f}</td></tr>'
            for s in t["sessions"])
        parts.append(f"""<section><h2>Sessions</h2>
<div class="table-wrap"><table class="sessions{' one-filter' if len(t['filters']) == 1 else ''}">
<thead><tr><th>Night</th><th>Scope</th><th class="flt">Filter</th><th class="num">Frames × exp</th><th class="num">Hours</th></tr></thead>
<tbody>{rows}</tbody>
<tfoot><tr><td colspan="2">Total stacked</td><td class="flt"></td><td></td><td class="num">{t['hours']:.2f}</td></tr></tfoot>
</table></div>
<p class="note">Frames the scope stacked, not frames taken. Night = local evening the session started.</p>
</section>""")
    parts.append("</main>")
    return page(f"{t['name']} · bortle9.space", "\n".join(parts), generated)


def goal_of(t):
    return f" / {t['goal']:g}" if t["goal"] else ""


def about_html(targets, generated):
    """about.html: how the pictures are taken, processed and published. Numbers come from this build."""
    sessions = [x for t in targets for x in t["sessions"]]
    hours = sum(t["hours"] for t in targets)
    nights = len({x["night"] for x in sessions})
    frames = sum(int(x.get("frames") or 0) for x in sessions)
    exps = sorted({float(x["exp"]) for x in sessions if x.get("exp")})
    exp = (f"{exps[0]:g} to {exps[-1]:g} seconds" if len(exps) > 1 else f"{exps[0]:g} seconds") if exps else "a few seconds"
    hoo = sum(1 for t in targets if t.get("hoo"))
    stats = (f"Running total: {len(targets)} targets, {hours:.0f} hours of exposure, {nights} nights "
             f"and {frames:,} individual frames.") if sessions else ""
    body = f"""<header class="target-header">
  <nav class="crumbs"><a href="index.html">← Gallery</a></nav>
  <h1>How these are made</h1>
  <p class="kind">Two little smart scopes, one very bright sky, and a lot of late nights</p>
</header>
<main class="about">
<p>Everything here was shot from a back deck in the Central Iowa suburbs, under a Bortle 8.6 sky. The Bortle
scale runs from 1 (middle of nowhere, the Milky Way bright enough to cast shadows) to 9 (downtown).
At 8.6 the Milky Way is gone and, on a good night, the stars overhead can be counted by eye. The
site name rounds up, because it feels like a 9. {stats}</p>

<p>Not one of these objects is visible from that yard. That's the fun part.</p>

<h2>The gear</h2>
<p>Two small all-in-one smart telescopes: a ZWO Seestar S30 Pro and a DwarfLab Dwarf 3. Camera,
optics and mount in one box each, and both run on equatorial mounts, so they track the sky the way
a proper mount does and the frame doesn't slowly rotate through the night. Polar-aligning on a
wooden deck is a hobby of its own: the boards flex, and walking away from the scope at the wrong
moment is enough to knock it off.</p>

<h2>Shooting</h2>
<ul>
<li><strong>Lots of short frames, not one long one.</strong> Each scope takes exposures of {exp}
all night long, and they get stacked afterwards. A plane, a satellite or a gust of wind costs a
few frames instead of the whole night.</li>
<li><strong>Filters are what make this possible.</strong> Streetlights and city glow cover the whole
spectrum, but emission nebulae glow at a couple of very specific colors: hydrogen-alpha (deep red)
and oxygen-III (teal). A dual-band filter lets just those through and throws out most of the light
pollution. Galaxies and star clusters shine in every color, so a narrow filter would throw their
light out too. Those are shot broadband and simply need a lot more time.</li>
<li><strong>Some nights the Seestar is driven from a computer</strong> with N.I.N.A. instead of
its phone app, taking raw 60-second frames. Every frame is kept, the bad ones (clouds, trailing,
the neighbor's floodlight) get tossed, and each night is calibrated properly with darks and
flats.</li>
</ul>

<h2>Many nights per target</h2>
<p>One night is almost never enough under this sky. Most objects get several nights, sometimes
weeks apart, all stacked into one image. The hours on each card are the real total that went in,
counted straight from the capture files. The bar underneath is the goal for that kind of object:
1.5 hours for a bright cluster, 2 for a planetary nebula, 3 for a bright nebula, 4 for a galaxy,
5 or more for faint targets and mosaics. Past the goal, doubling the time buys only about 40% more
signal, so that's where a target counts as done. The really big ones are mosaics: several
overlapping fields stitched together.</p>

<h2>Processing</h2>
<ul>
<li><strong>PixInsight does nearly all of it.</strong> The early images were done in Siril (a few
of those are still here), and PixInsight won the side-by-side comparison. Stacking, color
calibration, pulling out the gradient the city glow smears across every frame, then RC-Astro's
BlurXTerminator, NoiseXTerminator and StarXTerminator to sharpen, quiet the noise and lift the
stars off so the nebula can be worked on by itself.</li>
<li><strong>Stretching is where the picture shows up.</strong> A raw stack looks almost black:
the faint stuff is a tiny fraction of the brightest stars. A careful stretch brings it up without
turning every star into a blob.</li>
<li><strong>HOO vs Natural.</strong> Emission nebulae get two versions. Natural keeps the colors
roughly the way the filter caught them. HOO maps hydrogen to red and oxygen to teal, which shows
where each gas sits and how they mix. HOO is the favorite, so it opens first. {hoo} targets have
both; the toggle above the image switches.</li>
</ul>

</main>"""
    return page("How these are made · bortle9.space", body, generated)


# ---------------------------------------------------------------- distances

# Distance from Earth by page id: light-years, plus a note where a frame holds objects at very
# different distances. Rounded, commonly quoted values (Gaia-era where there is one); distances to
# nebulae and galaxies are uncertain by 10-30%, so they are shown as "about". The solar system gets
# a fixed text instead. A target missing here simply shows no distance.
DISTANCE = {
    "ngc281": (9_500, ""), "heart-soul": (7_500, ""), "veil": (2_400, ""), "c33": (2_400, ""),
    "ngc6960": (2_400, ""), "m31": (2_500_000, ""), "m51": (28_000_000, ""), "m33": (2_700_000, ""),
    "ngc6643": (70_000_000, ""), "c20": (2_600, ""), "ngc7000-mosaic": (2_600, ""),
    "ngc7380": (7_200, ""), "ngc7023": (1_300, ""), "c27": (5_000, ""),
    "m52-bubble": (7_100, "M52 is nearer, about 5,000 light-years"), "ic443": (5_000, ""),
    "m1": (6_500, ""), "ngc891": (30_000_000, ""),
    "ngc7331": (40_000_000, "Stephan's Quintet, behind it: about 290 million light-years"),
    "double-cluster": (7_500, ""), "m45": (444, ""), "ic1396": (2_400, ""),
    "ic405-410": (1_500, "the Tadpoles (IC 410) are far behind the Flaming Star, about 12,000 light-years"),
    "ngc1499": (1_000, ""), "ngc7822": (3_000, ""), "sh2-108": (4_000, "Sadr itself is a foreground star, about 1,800 light-years"),
    "ngc1333": (1_000, ""), "m81": (12_000_000, "M82 is at the same distance"), "ngc2237": (5_200, ""),
    "ic5146": (2_500, ""), "ngc752": (1_400, ""), "ic342": (11_000_000, ""), "m27": (1_360, ""),
    "m57": (2_600, ""), "c15": (4_500, ""), "c9": (2_400, ""), "ic63": (550, ""),
    "m35": (2_800, "NGC 2158, the small cluster beside it, is about 11,000 light-years away"),
    "ngc2403": (10_000_000, ""), "ngc7635": (7_100, ""), "alpha": (570, ""), "ngc2264": (2_500, ""),
    "ic417": (10_000, ""), "ngc1491": (10_000, ""), "ngc2174": (6_400, ""), "sh2132": (10_000, ""),
    "sh2240": (3_000, ""), "m42": (1_350, ""),
}
NEARBY = {  # solar system: (card text, page text, light travel time)
    "moon": ("384,000 km", "384,400 km on average", "1.3 seconds"),
    "sun": ("150 million km", "150 million km (1 astronomical unit)", "8.3 minutes"),
    "saturn": ("1.3 billion km", "about 1.3 billion km near opposition in October 2026", "1.2 hours"),
}
THIS_YEAR = dt.date.today().year


def ly_text(ly, short=False):
    if ly >= 1_000_000:
        m = ly / 1_000_000
        return f'{m:.1f}'.rstrip("0").rstrip(".") + (" Mly" if short else " million light-years")
    return f"{ly:,} ly" if short else f"{ly:,} light-years"


def distance_facts(t):
    """[(label, html)] for a target page: distance, and when its light set out."""
    if t["id"] in NEARBY:
        _, text, travel = NEARBY[t["id"]]
        return [("Distance", esc(text)), ("Light travel time", esc(travel))]
    d = DISTANCE.get(t["id"])
    if not d:
        return []
    ly, note = d
    dist = ("about " if ly >= 2_000 else "") + ly_text(ly) + (f'<span class="note">{esc(note)}</span>' if note else "")
    year = THIS_YEAR - ly
    if ly >= 1_000_000:
        left = ly_text(ly).replace("light-years", "years ago")
    elif ly > 10_000 or year < -5_000:
        left = f"{ly:,} years ago"
    elif year > 0:
        left = f"around {year} AD"
    else:
        left = f"around {-year:,} BC"
    return [("Distance", dist), ("Light left it", esc(left))]


def card_distance(t):
    if t["id"] in NEARBY:
        return NEARBY[t["id"]][0]
    d = DISTANCE.get(t["id"])
    return ly_text(d[0], short=True) if d else ""


# ---------------------------------------------------------------- stats.html

NARROWBAND = {"Duo-Band", "LP"}  # Dwarf 3 dual-band, Seestar "LP on" (Ha/OIII); the rest is broadband
SYNODIC = 29.530588853
NEW_MOON = dt.datetime(2000, 1, 6, 18, 14, tzinfo=dt.timezone.utc)


def moon_lit(night):
    """Fraction of the moon lit late on the evening of `night` (YYYY-MM-DD), about 11 pm US Central."""
    t = dt.datetime.strptime(night, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc) + dt.timedelta(days=1, hours=4)
    age = ((t - NEW_MOON).total_seconds() / 86400) % SYNODIC
    return (1 - math.cos(2 * math.pi * age / SYNODIC)) / 2


def moon_glyph(lit, waxing=True):
    return "🌑🌒🌓🌔🌕"[min(4, int(lit * 5))] if waxing else "🌑🌘🌗🌖🌕"[min(4, int(lit * 5))]


def bars(rows, unit="h", total=None):
    """rows: [(label, value, extra_html)] -> horizontal bar list, widths relative to the largest."""
    top = max((v for _, v, _ in rows), default=0) or 1
    out = []
    for label, v, extra in rows:
        pct = 100 * v / top
        share = f' <span class="muted">{100 * v / total:.0f}%</span>' if total else ""
        out.append(f'<div class="hbar"><span class="hl">{label}</span>'
                   f'<span class="ht"><span style="width:{pct:.1f}%"></span></span>'
                   f'<span class="hv">{v:,.1f} {unit}{share}{extra}</span></div>')
    return "\n".join(out)


def stats_html(sessions, targets, generated, today):
    """stats.html: the nights, hours and moon behind the gallery. Everything from this build's data."""
    ss = [x for x in (sessions or []) if (x.get("frames") or 0) > 0 and x.get("night")]
    if not ss:
        return page("Stats · bortle9.space", '<header class="target-header"><nav class="crumbs"><a href="index.html">← Gallery</a></nav>'
                    '<h1>Stats</h1></header><main class="stats"><p>No capture data in this build.</p></main>', generated)
    per_night = {}
    for x in ss:
        n = per_night.setdefault(x["night"], {"h": 0.0, "nb": 0.0, "bb": 0.0, "scopes": set(), "targets": set(), "frames": 0})
        h = float(x.get("hours") or 0)
        n["h"] += h
        n["nb" if x.get("filter") in NARROWBAND else "bb"] += h
        n["scopes"].add(x["scope"]); n["targets"].add(x.get("target")); n["frames"] += int(x["frames"])
    nights = sorted(per_night)
    total_h = sum(v["h"] for v in per_night.values())
    first = dt.date.fromisoformat(nights[0])

    # 1. calendar: one column per week (Sunday first), one square per day
    start = first - dt.timedelta(days=(first.weekday() + 1) % 7)
    weeks = (today - start).days // 7 + 1
    cells, months = [], []
    for w in range(weeks):
        for d in range(7):
            day = start + dt.timedelta(days=7 * w + d)
            if day > today:
                cells.append('<span class="d out"></span>')
                continue
            if day.day == 1 or (w == 0 and d == 0):
                months.append(f'<span style="grid-column:{w + 1}">{day:%b}</span>')
            n = per_night.get(day.isoformat())
            if n:
                lvl = 1 if n["h"] < 2 else 2 if n["h"] < 5 else 3 if n["h"] < 10 else 4
                lit = moon_lit(day.isoformat())
                tip = f'{day:%a %b} {day.day}: {n["h"]:.1f} h, {len(n["targets"])} targets, moon {lit:.0%} lit'
                cells.append(f'<span class="d l{lvl}" title="{esc(tip)}"></span>')
            else:
                cells.append(f'<span class="d" title="{day:%a %b} {day.day}"></span>')
    calendar = (f'<div class="cal" style="--weeks:{weeks}"><div class="cal-m">{"".join(months)}</div>'
                f'<div class="cal-d">{"".join(cells)}</div></div>'
                '<p class="legend muted">less <span class="d"></span><span class="d l1"></span><span class="d l2"></span>'
                '<span class="d l3"></span><span class="d l4"></span> more · each square is a day; hover for the night</p>')

    # 2. cumulative integration (inline SVG, a step line)
    W, H, pad = 760, 220, 34
    span = max(1, (today - first).days)
    pts, cum = [(pad, H - pad)], 0.0
    for nm in nights:
        x = pad + (W - 2 * pad) * (dt.date.fromisoformat(nm) - first).days / span
        y_prev = H - pad - (H - 2 * pad) * cum / total_h
        cum += per_night[nm]["h"]
        y = H - pad - (H - 2 * pad) * cum / total_h
        pts += [(x, y_prev), (x, y)]
    pts.append((W - pad, pts[-1][1]))
    poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    area = poly + f" {W - pad:.1f},{H - pad:.1f} {pad:.1f},{H - pad:.1f}"
    ticks = []
    m = dt.date(first.year, first.month, 1)
    while m <= today:
        if m >= first:
            x = pad + (W - 2 * pad) * (m - first).days / span
            ticks.append(f'<line x1="{x:.1f}" y1="{pad}" x2="{x:.1f}" y2="{H - pad}" class="grid"/>'
                         f'<text x="{x:.1f}" y="{H - pad + 16}" class="lbl" text-anchor="middle">{m:%b}</text>')
        m = dt.date(m.year + (m.month == 12), m.month % 12 + 1, 1)
    curve = (f'<svg class="curve" viewBox="0 0 {W} {H}" role="img" aria-label="Cumulative hours of exposure from '
             f'{first:%B} {first.day} to now: {total_h:.0f} hours"'
             f'>{"".join(ticks)}<polygon points="{area}" class="fill"/><polyline points="{poly}" class="line"/>'
             f'<text x="{W - pad}" y="{pad - 10}" class="lbl" text-anchor="end">{total_h:.0f} h</text>'
             f'<text x="{pad}" y="{H - pad - 6}" class="lbl">{first:%b} {first.day}: first light</text></svg>')

    # 3. moon
    buckets = [("🌑 0–25% lit", 0, .25), ("🌓 25–50%", .25, .5), ("🌔 50–75%", .5, .75), ("🌕 75–100%", .75, 1.01)]
    moon_rows = []
    for label, lo, hi in buckets:
        ns = [n for n in nights if lo <= moon_lit(n) < hi]
        nb = sum(per_night[n]["nb"] for n in ns); bb = sum(per_night[n]["bb"] for n in ns)
        moon_rows.append((label, len(ns), nb, bb))
    top = max((nb + bb for _, _, nb, bb in moon_rows), default=1) or 1
    moon = "\n".join(
        f'<div class="hbar"><span class="hl">{label} <span class="muted">· {k} night{"s" if k != 1 else ""}</span></span>'
        f'<span class="ht"><span class="nb" style="width:{100 * nb / top:.1f}%"></span>'
        f'<span class="bb" style="width:{100 * bb / top:.1f}%"></span></span>'
        f'<span class="hv">{nb:.1f} h narrowband · {bb:.1f} h broadband</span></div>'
        for label, k, nb, bb in moon_rows)
    bright_n = sum(r[1] for r in moon_rows[2:])

    # 4. totals
    scope_h = {}
    for x in ss:
        scope_h[x["scope"]] = scope_h.get(x["scope"], 0) + float(x.get("hours") or 0)
    by_scope = bars([(SCOPES.get(k, k), v, "") for k, v in sorted(scope_h.items(), key=lambda kv: -kv[1])], total=total_h)
    nb_h = sum(v["nb"] for v in per_night.values())
    by_filter = bars([("Narrowband (dual-band Ha/OIII)", nb_h, ""), ("Broadband", total_h - nb_h, "")], total=total_h)
    names = {gid: label for gid, label, _ in GROUPS}
    grp = {}
    for t in targets:
        if t["hours"]:
            grp[t["group"]] = grp.get(t["group"], 0) + t["hours"]
    by_kind = bars([(names.get(g, g), v, "") for g, v in sorted(grp.items(), key=lambda kv: -kv[1])])

    # 5. leaderboards
    def board(rows):
        return "<ol class=\"board\">" + "".join(f"<li>{r}</li>" for r in rows) + "</ol>"
    link = lambda t: f'<a href="{esc(t["id"])}.html">{esc(t["name"])}</a>'
    deep = board(f'{link(t)} <span class="muted">{t["hours"]:.1f} h</span>'
                 for t in sorted(targets, key=lambda t: -t["hours"])[:5])
    most = board(f'{link(t)} <span class="muted">{len(t["nights"])} nights</span>'
                 for t in sorted(targets, key=lambda t: (-len(t["nights"]), -t["hours"]))[:5])
    best = board(f'{dt.date.fromisoformat(n):%b} {int(n[8:])} <span class="muted">{per_night[n]["h"]:.1f} h · '
                 f'{len(per_night[n]["targets"])} targets{" · both scopes" if len(per_night[n]["scopes"]) > 1 else ""}</span>'
                 for n in sorted(nights, key=lambda n: -per_night[n]["h"])[:5])

    # 6. goals
    goal_t = [t for t in targets if t.get("goal") and t["sessions"]]
    done = [t for t in goal_t if t["hours"] >= t["goal"]]
    pct_done = 100 * len(done) / len(goal_t) if goal_t else 0
    closest = sorted((t for t in goal_t if t["hours"] < t["goal"]), key=lambda t: -t["hours"] / t["goal"])[:6]
    closest_html = "".join(f'<div class="goalrow">{link(t)}{depth_bar(t)}</div>' for t in closest)

    frames = sum(v["frames"] for v in per_night.values())
    body = f"""<header class="target-header">
  <nav class="crumbs"><a href="index.html">← Gallery</a></nav>
  <h1>Stats</h1>
  <p class="kind">The nights, hours and moon behind the gallery</p>
</header>
<main class="stats">
<div class="tiles">
  <div class="tile"><b>{len(nights)}</b><span>nights out</span></div>
  <div class="tile"><b>{total_h:.0f} h</b><span>of exposure</span></div>
  <div class="tile"><b>{frames:,}</b><span>frames</span></div>
  <div class="tile"><b>{len(targets)}</b><span>targets</span></div>
  <div class="tile"><b>{len(done)}/{len(goal_t)}</b><span>at their goal</span></div>
</div>

<section><h2>Clear nights</h2>
<p class="muted">Every night the scopes were out since first light, shaded by hours of exposure.</p>
{calendar}</section>

<section><h2>Integration over time</h2>
{curve}</section>

<section><h2>Shooting through the moon</h2>
<p class="muted">Hours per moon phase. Dual-band filters cut through moonlight much the way they cut through city glow, so a bright moon rarely ends a night: {bright_n} of {len(nights)} nights out had the moon more than half lit.</p>
<div class="key"><span class="sw nb"></span>narrowband <span class="sw bb"></span>broadband</div>
{moon}</section>

<section class="cols3">
<div><h2>By scope</h2>{by_scope}</div>
<div><h2>By filter</h2>{by_filter}</div>
<div><h2>By kind of object</h2>{by_kind}</div>
</section>

<section class="cols3">
<div><h2>Deepest</h2>{deep}</div>
<div><h2>Most nights</h2>{most}</div>
<div><h2>Best nights</h2>{best}</div>
</section>

<section><h2>Goals</h2>
<div class="goalbar"><span style="width:{pct_done:.1f}%"></span></div>
<p class="muted">{len(done)} of {len(goal_t)} targets have reached their depth goal. Closest to the line:</p>
{closest_html}</section>

<p class="note muted">Calendar, curve, moon and totals count everything captured ({total_h:.0f} h); the
leaderboards and goals count what went into the finished images. Moon phase is computed for about
11 pm on each night.</p>
</main>"""
    return page("Stats · bortle9.space", body, generated)


def share_relative(msg, paths):
    """A warning with the share's own location cut off (status.html is public): '/data/Finished/x.jpg'
    -> 'Finished/x.jpg'. Covers the root and every input folder, either slash."""
    roots = {os.path.dirname(os.path.normpath(paths["finished"]))}  # the share root
    if paths.get("source"):
        roots.add(os.path.normpath(paths["source"]))  # the same folder unless given separately
    for r in sorted((r for r in roots if r and r not in (".", os.sep)), key=len, reverse=True):
        for form in {r, r.replace("\\", "/"), r.replace("/", "\\")}:
            msg = msg.replace(form.rstrip("/\\") + "/", "").replace(form.rstrip("/\\") + "\\", "")
    return msg


def status_html(targets, images, unnamed, generated, paths=None):
    """status.html: what this build did. Public: share paths are cut from the warnings, and a failed
    build's log goes to build-log.html (home network only, see deploy/nginx.conf)."""
    version = os.environ.get("GALLERY_VERSION")
    hours = sum(t["hours"] for t in targets)
    parts = [f"""<header class="site-header">
  <nav class="crumbs"><a href="index.html">← Gallery</a></nav>
  <h1>Build status</h1>
  <p class="sub">Built {generated}{f' · code {esc(version)}' if version else ''} · {len(targets)} targets ·
    {len(images.wanted)} images · {hours:.1f} h</p>
</header>
<main class="status">"""]
    if WARNINGS:
        parts.append(f"<section><h2>Warnings ({len(WARNINGS)})</h2><ul class=\"warn\">"
                     + "".join(f"<li>{esc(share_relative(w, paths) if paths else w)}</li>" for w in WARNINGS) + "</ul></section>")
    else:
        parts.append("<section><h2>Warnings</h2><p class=\"ok\">None.</p></section>")
    if unnamed:
        parts.append(f"<section><h2>Not published: names the gallery cannot read ({len(unnamed)})</h2>"
                     "<p class=\"note\">These finished JPGs don't follow the file naming the gallery reads, "
                     "so they are not on the site yet.</p><ul class=\"files\">"
                     + "".join(f"<li>{esc(n)}</li>" for n in unnamed) + "</ul></section>")
    rows = "".join(
        f'<tr><td><a href="{esc(t["id"])}.html">{esc(t["name"])}</a></td>'
        f'<td class="file">{esc(images.wanted.get(t["hoo"] or t["natural"], ""))}</td>'
        f'<td class="num">{t["hours"]:.1f}{goal_of(t)}</td>'
        f'<td class="num">{len(t["nights"])}</td></tr>' for t in targets)
    parts.append(f"""<section><h2>Targets</h2><div class="table-wrap"><table class="sessions">
<thead><tr><th>Target</th><th>Main image</th><th class="num">Hours / goal</th><th class="num">Nights</th></tr></thead>
<tbody>{rows}</tbody></table></div></section></main>""")
    return page("Build status · bortle9.space", "\n".join(parts), generated[:10])


# ---------------------------------------------------------------- main

def finished_jpgs(folder):
    try:
        return sorted(e.name for e in os.scandir(folder) if e.is_file() and utf8_name(e.name)
                      and e.name.lower().endswith((".jpg", ".jpeg")) and not e.name.startswith("."))
    except OSError:
        return []


def write(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def selftest():
    """strip_metadata and make_derivatives against a progressive JPEG carrying every kind of
    metadata they must drop, plus a fill byte, a comment between scans and a trailer after EOI
    (make test)."""
    import tempfile
    im = Image.effect_mandelbrot((320, 200), (-2, -1.5, 1, 1.5), 40).convert("RGB")
    exif = Image.Exif()
    exif[ORIENTATION] = 6
    exif[0xA431] = "SERIAL"
    exif.get_ifd(0x8825)[29] = "GPSDATE"
    icc = srgb_bytes()
    secrets = (b"SERIAL", b"GPSDATE", b"COMMENT", b"XMP", b"MIDSCAN", b"TRAILER")
    with tempfile.TemporaryDirectory() as d:
        src, dst = os.path.join(d, "a.jpg"), os.path.join(d, "b.jpg")
        im.save(src, "JPEG", progressive=True, exif=exif, icc_profile=icc, comment=b"COMMENT", xmp=b"<x>XMP</x>")
        with open(src, "rb") as f:
            raw = f.read()
        assert raw.count(b"\xff\xda") > 1 and raw.endswith(b"\xff\xd9"), "fixture not progressive"
        dqt = raw.index(b"\xff\xdb")
        second_sos = raw.index(b"\xff\xda", raw.index(b"\xff\xda") + 2)
        com = b"\xff\xfe" + (9).to_bytes(2, "big") + b"MIDSCAN"
        raw = raw[:dqt] + b"\xff" + raw[dqt:second_sos] + com + raw[second_sos:] + b"TRAILER-GPS"
        with open(src, "wb") as f:
            f.write(raw)
        assert all(s in raw for s in secrets + (b"ICC_PROFILE",)), "bad fixture"
        assert Image.open(src).tobytes(), "Pillow cannot read the fixture"

        strip_metadata(src, dst, 6)
        with open(dst, "rb") as f:
            out = f.read()
        assert not any(s in out for s in secrets), "metadata left in"
        assert out.endswith(b"\xff\xd9") and b"\xff\xff\xdb" not in out, "trailer or fill byte kept"
        b = Image.open(dst)
        assert b.tobytes() == Image.open(src).tobytes(), "pixels changed"
        assert b.info.get("icc_profile") == icc, "ICC profile lost"
        assert dict(b.getexif()) == {ORIENTATION: 6}, f"exif {dict(b.getexif())}"
        assert "comment" not in b.info and "xmp" not in b.info, list(b.info)
        for o in (1, None, (6, 6), 0):
            strip_metadata(src, dst, o)
            with open(dst, "rb") as f:
                assert b"Exif" not in f.read(), f"orientation {o!r} wrote Exif"

        paths = tuple(os.path.join(d, p) for p in ("t.jpg", "d.jpg", "f.jpg"))
        info = make_derivatives((src, *paths))
        assert (info["dw"], info["dh"]) == (200, 320), info  # transposed
        for p in paths:
            with open(p, "rb") as f:
                assert not any(s in f.read() for s in secrets), f"{os.path.basename(p)} leaks"
        assert dict(Image.open(paths[2]).getexif()) == {ORIENTATION: 6}
    print("selftest: ok", file=sys.stderr)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    env = os.environ.get
    ap.add_argument("--root", help="the telescopes share (default $GALLERY_ROOT)")
    ap.add_argument("--finished", help="folder of finished JPGs (default $GALLERY_FINISHED or <root>/Finished)")
    ap.add_argument("--picks", help="default $GALLERY_PICKS or <root>/Gallery/picks.yaml")
    ap.add_argument("--inventory", help="sessions file; default $GALLERY_INVENTORY or "
                    "<root>/Gallery/inventory.json if it exists, else the capture folders are scanned")
    ap.add_argument("--no-inventory", action="store_true",
                    help="scan even if inventory.json exists (same as GALLERY_INVENTORY=none)")
    ap.add_argument("--source", help="folder holding Seestar S30 Pro/ and Dwarf 3/ to scan (default $GALLERY_SOURCE or <root>)")
    ap.add_argument("--nina", help="NINA folder to scan (default $GALLERY_NINA or <root>/NINA); subs listed in "
                    "<root>/SirilWork/PI/nina_reject.json or <root>/NINA/nina_reject.json "
                    "($GALLERY_NINA_REJECT) are left out")
    ap.add_argument("--out", default=env("GALLERY_OUT") or os.path.join(HERE, "site"))
    ap.add_argument("--jobs", type=int, default=int(env("GALLERY_JOBS") or 0) or os.cpu_count() or 4)
    ap.add_argument("--selftest", action="store_true", help="check the metadata stripping and exit")
    a = ap.parse_args()
    if a.selftest:
        return selftest()

    paths = input_paths(a.root, a.finished, a.picks, "none" if a.no_inventory else a.inventory, a.source, a.nina)
    images = Images(paths["finished"], a.out)
    inventory = load_inventory(paths)
    targets = load_targets(paths["picks"], inventory, images)
    images.build(a.jobs)
    targets = drop_failed(targets, images)
    set_updated(targets, images)
    used = set(images.wanted.values())
    unnamed = [n for n in finished_jpgs(paths["finished"]) if n not in used and not autopicks.parse(n)]

    now = dt.datetime.now().astimezone()
    generated = now.strftime("%Y-%m-%d %H:%M %Z")  # status.html
    day = now.strftime("%Y-%m-%d")  # the footers carry the date only
    ordered = [t for g in GROUP_ORDER for t in targets if t["group"] == g]
    keep = {"index.html", "about.html", "stats.html", "status.html", "build-log.html", "style.css", ".manifest.json", "img", "full"}
    write(os.path.join(a.out, "index.html"), index_html(ordered, images, day, now.date()))
    for n, t in enumerate(ordered):
        prev = ordered[n - 1] if n else None
        nxt = ordered[n + 1] if n + 1 < len(ordered) else None
        write(os.path.join(a.out, f"{t['id']}.html"), target_html(t, prev, nxt, images, day))
        keep.add(f"{t['id']}.html")
    write(os.path.join(a.out, "about.html"), about_html(ordered, day))
    write(os.path.join(a.out, "stats.html"), stats_html(inventory, ordered, day, now.date()))
    write(os.path.join(a.out, "status.html"), status_html(ordered, images, unnamed, generated, paths))
    css = os.path.join(a.out, "style.css")
    shutil.copyfile(os.path.join(HERE, "static", "style.css"), css + ".tmp")
    os.replace(css + ".tmp", css)
    for name in os.listdir(a.out):  # pages of targets that were removed or hidden
        if name.endswith(".html") and name not in keep:
            os.remove(os.path.join(a.out, name))
    images.prune()

    print(f"site: {len(ordered)} targets -> {a.out}", file=sys.stderr)
    for t in ordered:
        goal = f"/{t['goal']:g}" if t["goal"] else ""
        print(f"  {t['id']:16} {t['hours']:5.2f}{goal:>3} h  {len(t['nights'])} nights", file=sys.stderr)


if __name__ == "__main__":
    main()
