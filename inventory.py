#!/usr/bin/env python3
"""Capture sessions (frames the scope STACKED x exposure) found under a Source Data folder.

    python3 inventory.py "<root>/Source Data" [out.json]

Port of SirilWork/PI/py/inventory.py with the folder as an argument instead of
~/mnt/scopessd, so the builder on boris can scan the telescopes share itself. gallery.py calls
scan() directly; the command line prints one line per session (and writes out.json if given).

    Seestar S30 Pro/<target>/Stacked_<n>_<name>_<exp>s_<filter>_<YYYYmmdd-HHMMSS>.fit
    Dwarf 3/Astronomy/DWARF_RAW_*/shotsInfo.json
"""
import datetime as dt
import glob
import json
import math
import os
import re
import sys

SEESTAR_RE = re.compile(r"Stacked_(\d+)_(.*)_([\d.]+)s_([A-Z]+)_(\d{8}-\d{6})\.fit$")

# Manual (coordinate) Dwarf sessions and Seestar fields named after a star get the nearest
# catalogue target within 1 deg (RA hours, Dec deg)
CAT = {"M 52": (23.413, 61.59), "NGC 7635": (23.347, 61.20), "IC 443": (6.28, 22.5),
       "IC 1396": (21.65, 57.5), "NGC 7000": (20.98, 44.3), "NGC 6888": (20.20, 38.35),
       "NGC 1499": (4.05, 36.4), "IC 410": (5.38, 33.5), "IC 1805": (2.55, 61.45),
       "IC 1848": (2.86, 60.4), "M 33": (1.565, 30.66), "M 31": (0.712, 41.27),
       "NGC 891": (2.377, 42.35), "M 1": (5.575, 22.01), "M 45": (3.79, 24.1),
       "NGC 7331": (22.62, 34.42), "NGC 7023": (21.03, 68.17), "NGC 281": (0.88, 56.62),
       "NGC 6960": (20.76, 30.7), "M 27": (19.99, 22.72)}


def evening(t):
    """Local evening the session started."""
    return (t - dt.timedelta(hours=12)).strftime("%Y-%m-%d")


def nearest(ra, de):
    best = None
    for k, (r, d) in CAT.items():
        c = (math.sin(math.radians(de)) * math.sin(math.radians(d))
             + math.cos(math.radians(de)) * math.cos(math.radians(d)) * math.cos(math.radians((ra - r) * 15)))
        dd = math.degrees(math.acos(min(1, c)))
        if best is None or dd < best[1]:
            best = (k, dd)
    return best[0] if best and best[1] < 1.0 else None


def fexp(v):
    v = str(v)
    if "/" in v:
        a, b = v.split("/")
        return float(a) / float(b)
    return float(v)


def seestar_dirs(src):
    for d in sorted(glob.glob(os.path.join(glob.escape(src), "Seestar S30 Pro", "*", ""))):
        tgt = os.path.basename(os.path.dirname(d))
        if tgt.endswith("_sub") or tgt.lower().startswith(("lunar", "solar")):
            continue
        yield d, tgt


def dwarf_dirs(src):
    return sorted(glob.glob(os.path.join(glob.escape(src), "Dwarf 3", "Astronomy", "DWARF_RAW_*")))


def inputs(src):
    """Every file scan() reads, so a watcher can stat them instead of rescanning."""
    files = []
    for d, _ in seestar_dirs(src):
        files += glob.glob(os.path.join(glob.escape(d), "Stacked_*.fit"))
    files += [os.path.join(d, "shotsInfo.json") for d in dwarf_dirs(src)]
    return files


def scan(src):
    out = []
    for d, tgt in seestar_dirs(src):
        best = {}
        for f in glob.glob(os.path.join(glob.escape(d), "Stacked_*.fit")):
            m = SEESTAR_RE.match(os.path.basename(f))
            if not m:
                continue
            n, name, exp, flt = int(m[1]), m[2], float(m[3]), m[4]
            ts = dt.datetime.strptime(m[5], "%Y%m%d-%H%M%S")
            k = (evening(ts), flt, exp)
            pre = "mosaic_" if name.startswith("mosaic_") else ""
            if name[len(pre):].startswith(("HIP ", "HD ", "TYC ", "SAO ")):
                # Seestar named the field after a star: use the plate-solve centre
                try:
                    with open(f, "rb") as fh:
                        hdr = fh.read(2880 * 4).decode("latin1")
                    g = lambda kk: float(hdr.split(kk)[1].split("=")[1].split("/")[0])
                    nn = nearest(g("CRVAL1  ") / 15.0, g("CRVAL2  "))
                    name = (pre + nn) if nn else name
                except Exception:
                    pass
            if k not in best or n > best[k]["frames"]:
                best[k] = dict(scope="S30P", folder=tgt, target=name, night=k[0], filter=flt, exp=exp,
                               frames=n, file=os.path.basename(f))
        out += best.values()
    for d in dwarf_dirs(src):
        j = os.path.join(d, "shotsInfo.json")
        m = re.search(r"(\d{4}-\d{2}-\d{2}-\d{2}-\d{2}-\d{2})", os.path.basename(d))
        if not m or not os.path.exists(j):
            continue
        try:
            with open(j) as fh:
                s = json.load(fh)
        except Exception:
            continue
        ts = dt.datetime.strptime(m[1], "%Y-%m-%d-%H-%M-%S")
        tgt = s.get("target") or os.path.basename(d)
        if tgt in ("Manual", "Unknown") and s.get("RA") is not None:
            n = nearest(float(s["RA"]), float(s["DEC"]))
            tgt = ("M 52" if n in ("M 52", "NGC 7635") else n) if n else tgt
        out.append(dict(scope="D3", folder=os.path.basename(d), target=tgt, night=evening(ts),
                        filter=s.get("ir"), exp=fexp(s.get("exp", 0)), frames=int(s.get("shotsStacked", 0)),
                        taken=s.get("shotsTaken"), mosaic="mosaicInfo" in s, wide="WIDE" in d))
    seen = set()
    out = [o for o in out if not (o.get("file") and (o["file"] in seen or seen.add(o["file"])))]
    for o in out:
        o["hours"] = round(o["frames"] * o["exp"] / 3600, 2)
    return out


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    out = scan(sys.argv[1])
    if len(sys.argv) > 2:
        with open(sys.argv[2], "w") as f:
            json.dump(out, f, indent=1)
    for o in sorted(out, key=lambda o: (o["target"], o["night"])):
        if o["exp"] < 1:
            continue
        print(f"{o['scope']:4} {o['target'][:22]:22} {o['night']} {str(o['filter']):9} {o['exp']:>4.0f}s "
              f"{o['frames']:>4} {o['hours']:>5.2f}h  {o.get('file', o['folder'])[:60]}")


if __name__ == "__main__":
    main()
