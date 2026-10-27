#!/usr/bin/env python3
"""Rebuild the gallery whenever its inputs change. Runs in the builder container on boris.

    python3 watch.py [--interval 60]

Polls the files gallery.py reads (GALLERY_ROOT and the other GALLERY_* variables, see
gallery.py --help): picks.yaml, the JPGs in Finished/, the stacks and shotsInfo.json files
in Seestar S30 Pro/ and Dwarf 3/ and the subs in NINA/ (or inventory.json when that is used instead). Reruns gallery.py once they have
stopped changing for one poll, so a JPG that is still being copied over SMB is not picked up
half-written. Polling rather than inotify because writes that arrive through Unraid's
/mnt/user (shfs) or SMB do not reliably raise events.
Always builds once at startup.
"""
import argparse
import html
import os
import subprocess
import sys
import time

import inventory
from gallery import input_paths

HERE = os.path.dirname(os.path.abspath(__file__))


def signature():
    """(name, mtime, size) of every input file; top level of Finished/ only, like gallery.py.
    Paths are resolved on every poll, so adding or removing Gallery/inventory.json is noticed."""
    p = input_paths()
    finished, picks, inv_path = p["finished"], p["picks"], p["inventory"]
    files = [picks, inv_path or os.path.join(os.path.dirname(picks), "inventory.json")]
    if not inv_path:
        files += inventory.inputs(p["source"], p["nina"], p["reject"])
    sig = []
    for path in files:
        try:
            st = os.stat(path)
            sig.append((path, st.st_mtime, st.st_size))
        except OSError:
            sig.append((path, None, None))
    try:
        with os.scandir(finished) as it:
            for e in it:
                try:
                    if e.is_file() and e.name.lower().endswith((".jpg", ".jpeg")):
                        st = e.stat()
                        sig.append((e.name, st.st_mtime, st.st_size))
                except OSError:  # one entry vanishing mid-poll must not hide the rest
                    sig.append((e.name, None, None))
    except OSError as e:
        print(f"watch: cannot read {finished}: {e}", file=sys.stderr, flush=True)
    return sorted(sig, key=str)


def build():
    """Run gallery.py once; True when it succeeded."""
    t0 = time.time()
    try:
        r = subprocess.run([sys.executable, os.path.join(HERE, "gallery.py")], stderr=subprocess.PIPE, text=True,
                           timeout=float(os.environ.get("GALLERY_BUILD_TIMEOUT") or 3600))
        code, log = r.returncode, r.stderr
    except subprocess.TimeoutExpired as e:
        err = e.stderr.decode(errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
        code, log = -1, err + "\nbuild timed out\n"
    sys.stderr.write(log)
    status = "ok" if code == 0 else f"FAILED (exit {code}), retrying in a few polls or on the next change"
    print(f"watch: build {status} in {time.time() - t0:.0f} s", file=sys.stderr, flush=True)
    if code:
        failed_status(log)
    return code == 0


def failed_status(log):
    """Replace status.html with the failed build's log; the rest of the site stays as it was."""
    out = os.environ.get("GALLERY_OUT") or os.path.join(HERE, "site")
    when = time.strftime("%Y-%m-%d %H:%M %Z")
    tail = html.escape("\n".join(log.splitlines()[-40:]))
    page = (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>Build failed · Astro gallery</title><link rel="stylesheet" href="style.css"></head><body>'
            f'<header class="site-header"><nav class="crumbs"><a href="index.html">← Gallery</a></nav>'
            f'<h1>Build failed</h1><p class="sub">{when}. The gallery still shows the last good build; '
            f'the next change on the share triggers another try.</p></header>'
            f'<main class="status"><pre class="log">{tail}</pre></main></body></html>\n')
    try:
        os.makedirs(out, exist_ok=True)
        with open(os.path.join(out, "status.html.tmp"), "w", encoding="utf-8") as f:
            f.write(page)
        os.replace(os.path.join(out, "status.html.tmp"), os.path.join(out, "status.html"))
    except OSError as e:
        print(f"watch: cannot write status.html: {e}", file=sys.stderr, flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--interval", type=float, default=float(os.environ.get("GALLERY_INTERVAL") or 60),
                    help="seconds between polls (default $GALLERY_INTERVAL or 60)")
    a = ap.parse_args()

    built = signature()
    ok = build()
    last, since = built, 0  # polls since the last build
    while True:
        time.sleep(a.interval)
        now = signature()
        since += 1
        # settled, and either changed since the last build or that build failed (a transient
        # error, say the share hiccupping, then gets another go after five polls)
        if now == last and (now != built or (not ok and since >= 5)):
            built = now
            ok = build()
            since = 0
        last = now


if __name__ == "__main__":
    main()
