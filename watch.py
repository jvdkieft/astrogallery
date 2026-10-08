#!/usr/bin/env python3
"""Rebuild the gallery whenever its inputs change. Runs in the builder container on boris.

    python3 watch.py [--interval 60]

Polls the files gallery.py reads (GALLERY_ROOT and the other GALLERY_* variables, see
gallery.py --help): picks.yaml, the JPGs in Finished/, and the stacks and shotsInfo.json files
in Source Data/ (or inventory.json when that is used instead). Reruns gallery.py once they have
stopped changing for one poll, so a JPG that is still being copied over SMB is not picked up
half-written. Polling rather than inotify because writes that arrive through Unraid's
/mnt/user (shfs) or SMB do not reliably raise events.
Always builds once at startup.
"""
import argparse
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
    finished, picks, inv_path, source = input_paths()
    files = [picks, inv_path or os.path.join(os.path.dirname(picks), "inventory.json")]
    if not inv_path and source and os.path.isdir(source):
        files += inventory.inputs(source)
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
                if e.is_file() and e.name.lower().endswith((".jpg", ".jpeg")):
                    st = e.stat()
                    sig.append((e.name, st.st_mtime, st.st_size))
    except OSError as e:
        print(f"watch: cannot read {finished}: {e}", file=sys.stderr, flush=True)
    return sorted(sig, key=str)


def build():
    t0 = time.time()
    r = subprocess.run([sys.executable, os.path.join(HERE, "gallery.py")])
    status = "ok" if r.returncode == 0 else f"FAILED (exit {r.returncode}), retrying on next change"
    print(f"watch: build {status} in {time.time() - t0:.0f} s", file=sys.stderr, flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--interval", type=float, default=float(os.environ.get("GALLERY_INTERVAL") or 60),
                    help="seconds between polls (default $GALLERY_INTERVAL or 60)")
    a = ap.parse_args()

    built = signature()
    build()
    last = built
    while True:
        time.sleep(a.interval)
        now = signature()
        if now != built and now == last:  # changed since the last build, and settled
            built = now
            build()
        last = now


if __name__ == "__main__":
    main()
