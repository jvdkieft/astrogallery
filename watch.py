#!/usr/bin/env python3
"""Rebuild the gallery whenever its inputs change. Runs in the builder container on boris.

    python3 watch.py [--interval 60]

Polls the files gallery.py reads (the GALLERY_* environment variables, see gallery.py --help)
and reruns gallery.py once they have stopped changing for one poll, so a JPG that is still
being copied over SMB is not picked up half-written. Polling rather than inotify because
writes that arrive through Unraid's /mnt/user (shfs) or SMB do not reliably raise events.
Always builds once at startup.
"""
import argparse
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def inputs():
    env = os.environ.get
    src = env("GALLERY_SRC") or env("SCOPESSD") or "/Volumes/scopessd"
    return (env("GALLERY_FINISHED") or os.path.join(src, "Finished"),
            env("GALLERY_PICKS") or os.path.join(src, "Gallery", "picks.yaml"),
            env("GALLERY_INVENTORY") or os.path.join(src, "Gallery", "inventory.json"))


def signature():
    """(name, mtime, size) of every input file; top level of Finished/ only, like gallery.py."""
    finished, picks, inventory = inputs()
    sig = []
    for path in (picks, inventory):
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
