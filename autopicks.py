#!/usr/bin/env python3
"""Work out the gallery picks from the file names in Finished/, so picks.yaml is optional.

    python3 autopicks.py --selftest
    python3 autopicks.py --root /mnt/user/Telescopes [--picks FILE] [--slim]

Finished/ names follow README - Portfolio.md:

    <Cat#> <Name> <S30P|D3|S30P+D3> [mosaic] <PixInsight|Siril> [tags] [HOO] [Astro] <YYYYMMDD[+MMDD...]> [wide].jpg

Files are grouped into targets by their leading catalogue number (M27 = M 27, NGC7380 = NGC 7380;
a name with no number goes by its first word, e.g. Veil, Moon). Per target:

- natural: the version with the most nights, then the latest night, then PixInsight over Siril,
  then not "wide", then the fewest extra tags (hand, BXT+NXT+SXT, v2, ...).
- hoo: the HOO twin of that file (same name with " HOO" added), else the best HOO version.
- inset: the best image of a child target (CATALOGUE "parent", e.g. IC 1848 Soul inside the
  IC 1805 Heart mosaic), or for a Seestar mosaic, the best Dwarf 3 image of the same object.
- alt: the wide/crop twin of the main image, plus the best version of every other night set,
  scope and rendering that is not a subset of the main image's nights.
- sessions: every capture session whose target is one of the target's catalogue numbers or
  aliases, from the scopes that appear in its file names.
- name, kind, id: from CATALOGUE, else from the file name and "other". goal_h: from the kind
  (README - Portfolio.md depth goals).

Files without PixInsight or Siril in the name (quick app exports such as "M27 D3 20260911.jpeg")
are used only when a target has nothing else, and a target made only of those is not published.

picks.yaml still wins field by field (see resolve()). --slim prints picks.yaml with every field
that the automatic picks already produce left out, so new versions are picked up by themselves.
"""
import argparse
import os
import re
import sys

# Catalogue key -> what file names cannot tell. id keeps the page names (ngc281.html) stable;
# aliases are other numbers the object or its capture sessions go by; parent makes the target an
# inset of another target instead of a target of its own.
CATALOGUE = {
    "NGC 281": dict(id="ngc281", name="NGC 281 Pacman", kind="emission nebula"),
    "IC 1805": dict(id="heart-soul", name="IC 1805 / IC 1848 Heart & Soul", kind="emission nebula"),
    "IC 1848": dict(name="IC 1848 Soul Nebula", kind="emission nebula", parent="IC 1805"),
    "VEIL": dict(id="veil", name="Veil Nebula complex", kind="supernova remnant", aliases=["NGC 6960"]),
    "C 33": dict(id="c33", name="C 33 Eastern Veil", kind="supernova remnant", aliases=["NGC 6992", "NGC 6995"]),
    "M 31": dict(id="m31", name="M31 Andromeda Galaxy", kind="galaxy"),
    "M 51": dict(id="m51", name="M51 Whirlpool Galaxy", kind="galaxy"),
    "M 33": dict(id="m33", name="M33 Triangulum Galaxy", kind="galaxy"),
    "NGC 6643": dict(id="ngc6643", name="NGC 6643", kind="galaxy"),
    "C 20": dict(id="c20", name="NGC 7000 North America (C 20)", kind="emission nebula", aliases=["NGC 7000"]),
    "NGC 7000": dict(id="ngc7000-mosaic", name="NGC 7000 + IC 5070 North America & Pelican",
                     kind="emission nebula", aliases=["C 20", "IC 5070"]),
    "NGC 7380": dict(id="ngc7380", name="NGC 7380 Wizard Nebula", kind="emission nebula"),
    "NGC 7023": dict(id="ngc7023", name="NGC 7023 Iris Nebula", kind="reflection nebula", aliases=["C 4"]),
    "C 4": dict(name="NGC 7023 Iris Nebula (C 4)", kind="reflection nebula", parent="NGC 7023"),
    "C 27": dict(id="c27", name="NGC 6888 Crescent Nebula (C 27)", kind="emission nebula", aliases=["NGC 6888"]),
    "M 52": dict(id="m52-bubble", name="NGC 7635 Bubble + M52", kind="emission nebula + cluster",
                 aliases=["NGC 7635"]),
    "IC 443": dict(id="ic443", name="IC 443 Jellyfish Nebula", kind="supernova remnant"),
    "M 1": dict(id="m1", name="M1 Crab Nebula", kind="supernova remnant"),
    "NGC 891": dict(id="ngc891", name="NGC 891 (C 23)", kind="galaxy", aliases=["C 23"]),
    "NGC 7331": dict(id="ngc7331", name="NGC 7331 + Stephan's Quintet", kind="galaxy", aliases=["C 30"]),
    "NGC 869": dict(id="double-cluster", name="NGC 869 / 884 Double Cluster", kind="open cluster",
                    aliases=["NGC 884"]),
    "M 45": dict(id="m45", name="M45 Pleiades", kind="reflection nebula + cluster"),
    "IC 1396": dict(id="ic1396", name="IC 1396 Elephant's Trunk", kind="emission nebula"),
    "IC 405": dict(id="ic405-410", name="IC 405 Flaming Star + IC 410 Tadpoles", kind="emission nebula",
                   aliases=["NGC 1893"]),
    "IC 410": dict(name="IC 410 Tadpoles", kind="emission nebula", parent="IC 405"),
    "NGC 1499": dict(id="ngc1499", name="NGC 1499 California Nebula", kind="emission nebula"),
    "SH2-108": dict(id="sh2-108", name="Sh2-108 Sadr region", kind="emission nebula"),
    "M 27": dict(id="m27", name="M27 Dumbbell Nebula", kind="planetary nebula"),
    "M 57": dict(id="m57", name="M57 Ring Nebula", kind="planetary nebula"),
    "C 15": dict(id="c15", name="NGC 6826 Blinking Planetary (C 15)", kind="planetary nebula",
                 aliases=["NGC 6826"]),
    "NGC 6543": dict(id="c6", name="NGC 6543 Cat's Eye (C 6)", kind="planetary nebula", aliases=["C 6"]),
    "MOON": dict(id="moon", name="Moon", kind="solar system"),
    "SUN": dict(id="sun", name="Sun (white light)", kind="solar system"),
    # not imaged yet; here so a first image gets a proper name and group
    "M 42": dict(name="M42 Orion Nebula", kind="emission nebula"),
    "M 81": dict(name="M81 Bode's Galaxy", kind="galaxy"),
    "M 82": dict(name="M82 Cigar Galaxy", kind="galaxy"),
    "M 101": dict(name="M101 Pinwheel Galaxy", kind="galaxy"),
    "M 13": dict(name="M13 Hercules Cluster", kind="globular cluster"),
    "NGC 2237": dict(name="NGC 2237 Rosette Nebula", kind="emission nebula", aliases=["C 49", "NGC 2244"]),
    "NGC 7293": dict(name="NGC 7293 Helix Nebula", kind="planetary nebula", aliases=["C 63"]),
    "IC 5146": dict(name="IC 5146 Cocoon Nebula", kind="emission nebula", aliases=["C 19"]),
    "NGC 2264": dict(name="NGC 2264 Cone + Christmas Tree", kind="emission nebula + cluster"),
}

# Depth goals by kind, from README - Portfolio.md; the first word found wins.
GOALS = [("solar system", None), ("mosaic", 5), ("planetary", 2), ("galaxy", 4), ("supernova", 3),
         ("emission", 3), ("reflection", 3), ("cluster", 1.5)]

SCOPE_TOKENS = ("S30P", "D3", "S30P+D3")
CAT_RE = re.compile(r"^(M|NGC|IC|C|SH2-|Sh2-)\s?(\d+)\b")
CAT_ANY_RE = re.compile(r"(?<![A-Za-z0-9])(M|NGC|IC|C|SH2-|Sh2-)\s?(\d+)(?!\d)")
DATE_RE = re.compile(r"^(\d{8})((?:\+\d{4})*)(?:-\d+)?$")
FIELDS = ("name", "kind", "goal_h", "sessions", "natural", "hoo", "alt", "inset")


def cat_ids(text):
    """Catalogue numbers in `text`, normalised: 'M27 Dumbbell + IC 410' -> ['M 27', 'IC 410']."""
    out = []
    for m in CAT_ANY_RE.finditer(text.replace("mosaic_", "")):
        pre = m[1].upper()
        out.append(f"{pre}{m[2]}" if pre.endswith("-") else f"{pre} {m[2]}")
    return out


def key_of(text):
    """Grouping key of a target name: its first catalogue number, else its first word in capitals."""
    ids = cat_ids(text)
    if ids and CAT_RE.match(text.replace("mosaic_", "").strip()):
        return ids[0]
    words = text.split()
    return words[0].upper() if words else ""


def parse(fname):
    """Fields of one Finished/ file name, or None when it does not follow the naming."""
    stem, ext = os.path.splitext(fname)
    if ext.lower() not in (".jpg", ".jpeg") or fname.startswith("."):  # ._* are macOS metadata
        return None
    toks = stem.split()
    si = next((i for i, t in enumerate(toks) if t in SCOPE_TOKENS), None)
    di = next((i for i, t in enumerate(toks) if DATE_RE.match(t)), None)
    if si is None or di is None or si == 0 or di < si:
        return None
    m = DATE_RE.match(toks[di])
    first = m[1]
    nights = [first] + [first[:4] + d for d in m[2].split("+") if d]
    after = toks[si + 1:di] + toks[di + 1:]
    tool = next((t for t in after if t.startswith(("PixInsight", "Siril"))), None)
    words = [t for t in after if t != tool]
    tags = [t for t in words if t not in ("mosaic", "HOO", "wide", "Astro")]
    label = " ".join(toks[:si])
    return dict(file=fname, label=label, key=key_of(label), ids=cat_ids(label), scope=toks[si],
                mosaic="mosaic" in words, tool=("PixInsight" if tool and tool.startswith("PixInsight")
                                                 else "Siril" if tool else None),
                hoo="HOO" in words, wide="wide" in words, nights=nights, tags=tags,
                twin=fname.replace(" HOO", "", 1) if "HOO" in words else fname)


def rank(p):
    """Higher is better: more nights, later, PixInsight, not wide, fewer extra tags."""
    tool = {"PixInsight": 2, "Siril": 1}.get(p["tool"], 0)
    return (len(p["nights"]), max(p["nights"]), tool, not p["wide"], -len(p["tags"]), p["file"])


def best(ps):
    return max(ps, key=rank) if ps else None


def pick_pair(ps):
    """(natural, hoo) parsed files from `ps`: the best natural and its HOO twin."""
    nat = best([p for p in ps if not p["hoo"]])
    hoos = [p for p in ps if p["hoo"]]
    if nat:
        twin = next((p for p in hoos if p["twin"] == nat["file"]), None)
        return nat, twin or best(hoos)
    return None, best(hoos)


def choose_alts(ps, main):
    """Other versions worth a click: see the module docstring."""
    nights = set(main["nights"])
    cands = []
    for p in ps:
        if set(p["nights"]) < nights:  # older data that the main image already includes
            continue
        if set(p["nights"]) == nights and p["scope"] == main["scope"]:
            # same data: keep only the other crop of the main image itself
            same = (p["tool"] == main["tool"] and p["tags"] == main["tags"] and p["wide"] != main["wide"])
            if not same:
                continue
        cands.append(p)
    groups = {}
    for p in cands:
        groups.setdefault((p["scope"], tuple(p["nights"]), p["hoo"], p["wide"] if set(p["nights"]) == nights else None), []).append(p)
    alts = [best(g) for g in groups.values()]
    names = {p["file"] for p in alts}
    alts = [p for p in alts if not (p["hoo"] and p["twin"] in names)]
    return sorted(alts, key=rank, reverse=True)


def auto_targets(files, sessions=None):
    """Targets worked out from Finished/ names: {key: entry} in picks.yaml form, plus '_files'
    (every file the target covers, insets and children included) and '_key'."""
    parsed = [p for p in map(parse, files) if p]
    groups = {}
    for p in parsed:
        groups.setdefault(p["key"], []).append(p)
    # quick app exports only count when there is nothing processed
    for k, ps in groups.items():
        tooled = [p for p in ps if p["tool"]]
        groups[k] = tooled or [dict(p, untooled=True) for p in ps]
    session_keys = sorted({f"{s['scope']}|{s['target']}" for s in sessions or [] if s.get("frames", 0) > 0})

    out = {}
    for k, ps in groups.items():
        cat = CATALOGUE.get(k, {})
        if cat.get("parent") in groups:
            continue  # shown as the parent's inset
        if all(p.get("untooled") for p in ps):
            continue
        children = [c for c, v in CATALOGUE.items() if v.get("parent") == k and c in groups]
        child_ps = [p for c in children for p in groups[c]]
        own, inset_ps = ps, child_ps
        nat, hoo = pick_pair(own)
        main = nat or hoo
        if not inset_ps and main["mosaic"] and main["scope"] == "S30P":
            inset_ps = [p for p in own if p["scope"] == "D3"]
            own = [p for p in own if p["scope"] != "D3"]
            nat, hoo = pick_pair(own)
            main = nat or hoo
        ins_nat, ins_hoo = pick_pair(inset_ps)
        used = {x["file"] for x in (nat, hoo, ins_nat, ins_hoo) if x}
        alts = choose_alts([p for p in own if p["file"] not in used], main)

        kind = cat.get("kind") or "other"
        if main["mosaic"] and "mosaic" not in kind:
            kind += ", mosaic"
        ids = {k} | set(cat.get("aliases", []))
        for p in own + child_ps:
            ids |= set(p["ids"])
        for c in children:
            ids |= set(CATALOGUE[c].get("aliases", []))
        scopes = {s for p in own + inset_ps for s in p["scope"].split("+")}
        sess = [sk for sk in session_keys
                if sk.split("|")[0] in scopes and key_of(sk.split("|", 1)[1]) in ids
                and "solar system" not in kind]  # no depth to track for the Moon and Sun
        e = dict(id=cat.get("id") or re.sub(r"[^a-z0-9]+", "", k.lower()) or "target",
                 name=cat.get("name") or main["label"], kind=kind,
                 goal_h=next((g for w, g in GOALS if w in kind), None),
                 sessions=sess, natural=nat and nat["file"], hoo=hoo and hoo["file"],
                 alt=[p["file"] for p in alts],
                 inset={"natural": ins_nat and ins_nat["file"], "hoo": ins_hoo and ins_hoo["file"]})
        e["_key"] = k
        e["_files"] = {p["file"] for p in groups[k] + child_ps}
        out[k] = e
    return out


def referenced(entry):
    """File names a picks.yaml entry points at."""
    names = [entry.get("natural"), entry.get("hoo")]
    alts = entry.get("alt") or []
    names += [alts] if isinstance(alts, str) else list(alts)
    ins = entry.get("inset") or {}
    names += [ins] if isinstance(ins, str) else [ins.get("natural"), ins.get("hoo")]
    return {n for n in names if n}


def resolve(picks, files, sessions=None):
    """picks.yaml targets merged with the automatic ones, in picks.yaml order, then new targets.

    A picks.yaml entry claims the automatic target whose key or id matches its `match` or `id`,
    or that holds its natural/hoo file; any other target holding one of its files is claimed too
    (so it is not shown twice). Every field the entry sets wins, even when empty (`hoo:` with no
    value means no HOO); fields it leaves out come from its automatic target. Unclaimed automatic
    targets are added after the picks.yaml ones."""
    auto = auto_targets(files, sessions)
    by_id = {e["id"]: k for k, e in auto.items()}
    owner = {f: k for k, e in auto.items() for f in e["_files"]}
    claimed, out = set(), []
    for p in picks or []:
        p = dict(p)
        m = p.get("match")
        prim = None
        if m:
            m = str(m)
            prim = m if m in auto else by_id.get(m) or (key_of(m) if key_of(m) in auto else None)
        prim = prim or by_id.get(str(p.get("id")))
        for f in (p.get("natural"), p.get("hoo")):
            prim = prim or owner.get(f)
        claimed |= {owner[f] for f in referenced(p) if f in owner}
        if prim:
            claimed.add(prim)
            a = auto[prim]
            pinned = referenced(p)
            for f in FIELDS:
                if f in p:
                    continue
                v = a[f]
                if f == "alt":
                    v = [x for x in v if x not in pinned]
                elif f == "inset":
                    v = {r: (x if x not in pinned else None) for r, x in v.items()}
                elif f in ("natural", "hoo") and v in pinned:
                    v = None
                p[f] = v
            p["_auto"] = prim
        out.append(p)
    for k, e in auto.items():
        if k not in claimed:
            out.append({f: e[f] for f in ("id",) + FIELDS} | {"_auto": k})
    return out


# ---------------------------------------------------------------- slim picks.yaml

def _yaml_scalar(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return f"{v:g}"
    s = str(v)
    if s == "" or re.search(r"[:#\[\]{},&*!|>'\"%@`]", s) or s.strip() != s or s.lower() in ("true", "false", "yes", "no", "null"):
        return '"' + s.replace('"', '\\"') + '"'
    return s


def slim(picks, files, sessions=None):
    """picks.yaml text keeping only the fields that differ from the automatic picks."""
    auto = auto_targets(files, sessions)
    by_id = {e["id"]: k for k, e in auto.items()}
    owner = {f: k for k, e in auto.items() for f in e["_files"]}
    lines = ["# Gallery overrides. Targets come from the file names in Finished/ (autopicks.py);",
             "# an entry here only changes what the automatic picks get wrong. Every field set here wins.",
             "", "targets:"]
    for p in picks or []:
        prim = by_id.get(str(p.get("id"))) or owner.get(p.get("natural")) or owner.get(p.get("hoo"))
        a = auto.get(prim, {})
        keep = [("id", p.get("id"))]
        if prim and a.get("id") != p.get("id"):
            keep.append(("match", prim))
        for f in ("show",) + FIELDS:
            if f not in p:
                continue
            v, av = p[f], a.get(f)
            if f == "alt":
                v = [v] if isinstance(v, str) else list(v or [])
            if f == "inset":
                v = {"natural": v} if isinstance(v, str) else {r: (v or {}).get(r) for r in ("natural", "hoo")}
            if f == "sessions":
                v, av = sorted(v or []), sorted(av or [])
            if v != av or not prim:
                keep.append((f, v))
        lines.append(f"  - id: {_yaml_scalar(keep[0][1])}")
        for f, v in keep[1:]:
            if f == "inset":
                lines.append("    inset:")
                lines += [f"      {r}: {_yaml_scalar(x)}" for r, x in v.items() if x] or ["      natural:"]
            elif isinstance(v, list):
                lines.append(f"    {f}: [" + ", ".join(_yaml_scalar(x) for x in v) + "]")
            else:
                lines.append(f"    {f}: {_yaml_scalar(v)}")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- self-test

FIXTURES = """\
C15 Blinking Planetary S30P Siril 20260712+0802 wide.jpg
C15 Blinking Planetary S30P Siril 20260712+0802.jpg
C20 North America D3 PixInsight 20260525+0615+0712.jpg
C20 North America D3 Siril BXT+NXT+SXT 20260525+0615+0712.jpg
C20 North America D3 Siril hand 20260525+0615+0712.jpg
C27 Crescent D3 PixInsight 20260821+0910 wide.jpg
C27 Crescent D3 PixInsight 20260821+0910+1005 wide.jpg
C27 Crescent D3 PixInsight HOO 20260821+0910+1005 wide.jpg
C27 Crescent D3 Siril hand 20260821+0910.jpg
C33 Eastern Veil D3 PixInsight 20260801 wide.jpg
C33 Eastern Veil D3 Siril hand 20260801 wide.jpg
C4 Iris D3 PixInsight 20261002 wide.jpg
C4 Iris D3 PixInsight 20261002+1004 wide.jpg
Heart D3 20260911.jpeg
IC 1396 Elephants Trunk S30P mosaic PixInsight 20261003+1005+1007+1008.jpg
IC 1396 Elephants Trunk S30P mosaic PixInsight 20261003+1005+1007.jpg
IC 1396 Elephants Trunk S30P mosaic PixInsight HOO 20261003+1005+1007+1008.jpg
IC 1396 Elephants Trunk S30P mosaic PixInsight HOO 20261003+1005+1007.jpg
IC 1805 Heart S30P mosaic PixInsight 20260911+1003+1004.jpg
IC 1805 Mosaic S30P 20260911 v2.jpeg
IC 1805-1848 Heart and Soul S30P mosaic PixInsight 20260911+1003+1004+1006+1007.jpg
IC 1805-1848 Heart and Soul S30P mosaic PixInsight HOO 20260911+1003+1004+1006+1007.jpg
IC 1848 Soul D3 PixInsight 20261003+1004 wide.jpg
IC 1848 Soul D3 PixInsight HOO 20261003+1004 wide.jpg
IC 405 Flaming Star + IC 410 Tadpoles S30P mosaic PixInsight 20261004+1005.jpg
IC 405 Flaming Star + IC 410 Tadpoles S30P mosaic PixInsight HOO 20261004+1005.jpg
IC 410 Tadpoles D3 PixInsight 20261004 wide.jpg
IC 410 Tadpoles D3 PixInsight HOO 20261004 wide.jpg
M1 Crab D3 PixInsight 20261006 wide.jpg
M1 Crab D3 PixInsight 20261006.jpg
M1 Crab D3 PixInsight HOO 20261006 wide.jpg
M1 Crab D3 PixInsight HOO 20261006.jpg
M27 D3 20260911.jpeg
M27 Dumbbell D3 PixInsight 20260910 wide.jpg
M27 Dumbbell D3 PixInsight Astro 20261006 wide.jpg
M27 Dumbbell D3 PixInsight Astro 20261006.jpg
M27 Dumbbell D3 PixInsight HOO Astro 20261006 wide.jpg
M27 Dumbbell D3 PixInsight HOO Astro 20261006.jpg
M27 Dumbbell D3 Siril hand 20260910.jpg
M31 Andromeda D3 PixInsight 20260803 wide.jpg
M31 Andromeda S30P mosaic PixInsight 20260822+0911+1002+1005.jpg
M31 Andromeda S30P mosaic PixInsight 20260822+0911+1002.jpg
M31 S30P Siril starreduced clean 20260803+0911.jpg
M31 S30P+D3 Siril clean 20260803+0911.jpg
M33 Triangulum D3 PixInsight 20260911 wide.jpg
M33 Triangulum D3 PixInsight 20261004 wide.jpg
M33 Triangulum D3 PixInsight 20261004+1005 wide.jpg
M33 Triangulum D3 Siril hand 20260911.jpg
Moon D3 Siril hand 20260524.jpg
Moon D3 Siril hand 20260822.jpg
NGC 1499 California D3 PixInsight 20261003 wide.jpg
NGC 1499 California D3 PixInsight HOO 20261003 wide.jpg
NGC 1499 California S30P mosaic PixInsight 20261004+1005.jpg
NGC 1499 California S30P mosaic PixInsight HOO 20261004+1005.jpg
NGC 6643 D3 Siril hand 20260711-1.jpg
NGC 6643 D3 PixInsight 20260711 wide.jpg
NGC 6960 S30P 20260911.jpeg
NGC 7000 North America + Pelican S30P mosaic PixInsight 20260822+0910+1004+1006+1007.jpg
NGC 7000 North America + Pelican S30P mosaic PixInsight HOO 20260822+0910+1004+1006+1007.jpg
NGC 7000 North America + Pelican S30P mosaic Siril 20260822.jpg
NGC 7023 Iris S30P Siril 20260613+0614.jpg
NGC7380 D3 Siril 20260713 wide.jpg
NGC 7380 D3 Siril hand v2 20260713.jpg
NGC 869 884 Double Cluster S30P PixInsight 20261006+1008.jpg
NGC 869 884 Double Cluster S30P PixInsight 20261006.jpg
NGC 891 D3 PixInsight 20261006+1007.jpg
NGC 891 D3 PixInsight 20261006.jpg
NGC7000 D3 20260911.jpeg
SH2-108 Sadr region S30P Siril BXT+NXT+SXT 20260803.jpg
Sun white light D3 Siril hand 20260525.jpg
Veil Nebula complex S30P mosaic PixInsight 20260911+1004+1006.jpg
Veil Nebula complex S30P mosaic PixInsight HOO 20260911+1004+1006.jpg
""".splitlines()

FIXTURE_SESSIONS = [dict(scope=s, target=t, frames=1) for s, t in (
    ("S30P", "mosaic_IC 1805"), ("D3", "IC 1848"), ("D3", "C 4"), ("S30P", "NGC 7023"), ("D3", "C 23"),
    ("S30P", "mosaic_NGC 1893"), ("D3", "IC 410"), ("S30P", "SH2-108 - Sadr Region"), ("D3", "C 20"),
    ("S30P", "mosaic_NGC 7000"), ("S30P", "NGC 7000"), ("D3", "M 27"), ("S30P", "M 27"), ("D3", "HD 198626"), ("D3", "Moon"))]


def selftest():
    fails = []

    def check(what, got, want):
        if got != want:
            fails.append(f"{what}: got {got!r}, want {want!r}")

    check("cat_ids", cat_ids("IC 405 Flaming Star + IC 410 Tadpoles"), ["IC 405", "IC 410"])
    check("cat_ids nospace", cat_ids("NGC7380"), ["NGC 7380"])
    check("cat_ids sh2", cat_ids("SH2-108 - Sadr Region"), ["SH2-108"])
    check("cat_ids mosaic_", cat_ids("mosaic_IC 1805"), ["IC 1805"])
    check("key name", key_of("Veil Nebula complex"), "VEIL")
    check("key range", key_of("IC 1805-1848 Heart and Soul"), "IC 1805")
    p = parse("M27 Dumbbell D3 PixInsight HOO Astro 20261006 wide.jpg")
    check("parse", (p["key"], p["scope"], p["tool"], p["hoo"], p["wide"], p["nights"], p["tags"]),
          ("M 27", "D3", "PixInsight", True, True, ["20261006"], []))
    check("parse nights", parse("NGC 6643 D3 Siril hand 20260711-1.jpg")["nights"], ["20260711"])
    check("parse tif", parse("M1 Crab D3 PixInsight 20261006.tif"), None)
    check("parse AppleDouble", parse("._M1 Crab D3 PixInsight 20261006.jpg"), None)

    a = auto_targets(FIXTURES, FIXTURE_SESSIONS)
    t = a["IC 1396"]
    check("ic1396 natural", t["natural"], "IC 1396 Elephants Trunk S30P mosaic PixInsight 20261003+1005+1007+1008.jpg")
    check("ic1396 hoo", t["hoo"], "IC 1396 Elephants Trunk S30P mosaic PixInsight HOO 20261003+1005+1007+1008.jpg")
    check("ic1396 alt (older nights dropped)", t["alt"], [])
    check("ic1396 kind", t["kind"], "emission nebula, mosaic")
    check("ic1396 goal", t["goal_h"], 5)
    t = a["IC 1805"]
    check("heart id", t["id"], "heart-soul")
    check("heart natural", t["natural"], "IC 1805-1848 Heart and Soul S30P mosaic PixInsight 20260911+1003+1004+1006+1007.jpg")
    check("heart inset", t["inset"], {"natural": "IC 1848 Soul D3 PixInsight 20261003+1004 wide.jpg",
                                      "hoo": "IC 1848 Soul D3 PixInsight HOO 20261003+1004 wide.jpg"})
    check("heart sessions", t["sessions"], ["D3|IC 1848", "S30P|mosaic_IC 1805"])
    check("soul not its own target", "IC 1848" in a, False)
    check("tadpoles inset", a["IC 405"]["inset"]["natural"], "IC 410 Tadpoles D3 PixInsight 20261004 wide.jpg")
    check("ic405 sessions", a["IC 405"]["sessions"], ["D3|IC 410", "S30P|mosaic_NGC 1893"])
    check("iris inset", a["NGC 7023"]["inset"]["natural"], "C4 Iris D3 PixInsight 20261002+1004 wide.jpg")
    check("iris sessions", a["NGC 7023"]["sessions"], ["D3|C 4", "S30P|NGC 7023"])
    t = a["NGC 1499"]
    check("california natural", t["natural"], "NGC 1499 California S30P mosaic PixInsight 20261004+1005.jpg")
    check("california D3 inset", t["inset"]["hoo"], "NGC 1499 California D3 PixInsight HOO 20261003 wide.jpg")
    t = a["M 1"]
    check("m1 natural (not wide)", t["natural"], "M1 Crab D3 PixInsight 20261006.jpg")
    check("m1 hoo twin", t["hoo"], "M1 Crab D3 PixInsight HOO 20261006.jpg")
    check("m1 alt (wide twin)", t["alt"], ["M1 Crab D3 PixInsight 20261006 wide.jpg"])
    t = a["M 27"]
    check("m27 natural", t["natural"], "M27 Dumbbell D3 PixInsight Astro 20261006.jpg")
    check("m27 alt", t["alt"], ["M27 Dumbbell D3 PixInsight Astro 20261006 wide.jpg",
                                "M27 Dumbbell D3 PixInsight 20260910 wide.jpg"])
    check("m27 sessions by scope", t["sessions"], ["D3|M 27"])
    t = a["M 31"]
    check("m31 natural", t["natural"], "M31 Andromeda S30P mosaic PixInsight 20260822+0911+1002+1005.jpg")
    check("m31 inset", t["inset"]["natural"], "M31 Andromeda D3 PixInsight 20260803 wide.jpg")
    check("m31 alt", sorted(t["alt"]), ["M31 S30P Siril starreduced clean 20260803+0911.jpg",
                                        "M31 S30P+D3 Siril clean 20260803+0911.jpg"])
    check("m33 alt", a["M 33"]["alt"], ["M33 Triangulum D3 PixInsight 20260911 wide.jpg"])
    check("ngc891 alias sessions", (a["NGC 891"]["id"], a["NGC 891"]["sessions"], a["NGC 891"]["alt"]),
          ("ngc891", ["D3|C 23"], []))
    check("c20 vs ngc7000", (a["C 20"]["sessions"], a["NGC 7000"]["sessions"]),
          (["D3|C 20"], ["S30P|NGC 7000", "S30P|mosaic_NGC 7000"]))
    check("ngc7380 merged", a["NGC 7380"]["natural"], "NGC 7380 D3 Siril hand v2 20260713.jpg")
    check("veil", (a["VEIL"]["id"], a["VEIL"]["kind"]), ("veil", "supernova remnant, mosaic"))
    check("double cluster", (a["NGC 869"]["id"], a["NGC 869"]["goal_h"], a["NGC 869"]["alt"]),
          ("double-cluster", 1.5, []))
    check("moon", (a["MOON"]["natural"], a["MOON"]["goal_h"], a["MOON"]["sessions"]),
          ("Moon D3 Siril hand 20260822.jpg", None, []))
    check("app exports only: not published", [k for k in ("HEART", "NGC 6960") if k in a], [])
    check("app export dropped", "M27 D3 20260911.jpeg" in a["M 27"]["alt"], False)

    picks = [
        dict(id="m27", natural="M27 Dumbbell D3 PixInsight 20260910 wide.jpg", hoo=None),
        dict(id="c33", name="C 33 Eastern Veil", goal_h=3),
        dict(id="veil", inset={"natural": "C33 Eastern Veil D3 PixInsight 20260801 wide.jpg"}),
        dict(id="m31-old", natural="M31 S30P+D3 Siril clean 20260803+0911.jpg"),
        dict(id="ic1396", show=False),
        dict(id="iris", match="NGC 7023", goal_h=6),
        dict(id="crescent", match="c27"),
    ]
    r = {e["id"]: e for e in resolve(picks, FIXTURES, FIXTURE_SESSIONS)}
    check("pinned natural wins", r["m27"]["natural"], "M27 Dumbbell D3 PixInsight 20260910 wide.jpg")
    check("empty hoo wins", r["m27"]["hoo"], None)
    check("pinned file not repeated as alt", "M27 Dumbbell D3 PixInsight 20260910 wide.jpg" in r["m27"]["alt"], False)
    check("auto fills the rest", r["m27"]["sessions"], ["D3|M 27"])
    check("c33 natural from auto", r["c33"]["natural"], "C33 Eastern Veil D3 PixInsight 20260801 wide.jpg")
    check("veil keeps its inset", r["veil"]["inset"], {"natural": "C33 Eastern Veil D3 PixInsight 20260801 wide.jpg"})
    check("m31 claimed via natural", "m31" in r, False)
    check("m31 auto alt not pinned twice", "M31 S30P+D3 Siril clean 20260803+0911.jpg" in r["m31-old"]["alt"], False)
    check("show false kept", r["ic1396"]["show"], False)
    check("match by key", (r["iris"]["natural"], r["iris"]["goal_h"], "ngc7023" in r),
          ("NGC 7023 Iris S30P Siril 20260613+0614.jpg", 6, False))
    check("match by id", r["crescent"]["natural"], "C27 Crescent D3 PixInsight 20260821+0910+1005 wide.jpg")
    check("new target appended", r["ngc281"]["natural"] if "ngc281" in r else "missing", "missing")
    check("order", [e["id"] for e in resolve(picks, FIXTURES)][:len(picks)], [p["id"] for p in picks])

    s = slim([dict(id="m1", name="M1 Crab Nebula", kind="supernova remnant", goal_h=3,
                   natural="M1 Crab D3 PixInsight 20261006.jpg", hoo="M1 Crab D3 PixInsight HOO 20261006.jpg",
                   alt=["M1 Crab D3 PixInsight 20261006 wide.jpg"]),
              dict(id="m27", goal_h=2.5, show=False)], FIXTURES)
    check("slim", [l.strip() for l in s.splitlines()[4:]],
          ["- id: m1", "", "- id: m27", "show: false", "goal_h: 2.5"])

    for f in fails:
        print("FAIL", f)
    print(f"selftest: {'ok' if not fails else f'{len(fails)} failed'}")
    return not fails


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true", help="check the rules against real file names")
    ap.add_argument("--root", help="the telescopes share (default $GALLERY_ROOT)")
    ap.add_argument("--finished", help="default <root>/Finished")
    ap.add_argument("--picks", help="default <root>/Gallery/picks.yaml")
    ap.add_argument("--slim", action="store_true", help="print picks.yaml with the automatic fields left out")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if selftest() else 1)
    import gallery
    import minyaml
    paths = gallery.input_paths(a.root, a.finished, a.picks)
    files = sorted(os.listdir(paths["finished"]))
    picks = gallery.load_picks(paths["picks"])
    sessions = gallery.load_inventory(paths)
    if a.slim:
        print(slim(picks, files, sessions))
        return
    for e in resolve(picks, files, sessions):
        print(f"{e['id']:18} {e.get('kind') or '':28} {e.get('natural') or e.get('hoo')}")


if __name__ == "__main__":
    main()
