#!/usr/bin/env python3
"""Archive the working SSD to boris's telescopes share: copy what the share is missing.

    python3 archive.py [--dry-run] [--ssd /Volumes/scopessd] [--boris root@192.168.1.3]
                       [--share /mnt/user/Telescopes]

Copies Source Data/, NINA/, Finished/ and SirilWork/PI/nina_reject.json. A file is copied when
the share does not have it, or has it empty, or has it with a different size and an older
modification time (a copy that was cut short, or an image re-exported on the SSD). A share copy
that differs but is newer is left alone and listed, so an edit made on the share is never
overwritten. Nothing is ever deleted, on either side.

The plan comes from comparing file lists (find on both sides over SSH), not from rsync's own
dry run: macOS ships openrsync, whose --dry-run --stats counts files that already exist. The copy
itself is `rsync --files-from` with exactly that list. Copied files and the folders created for
them get Unraid's usual ownership (nobody:users, rw for all); nothing else changes owner.
"""
import argparse
import os
import shlex
import subprocess
import sys
import tempfile

PATHS = ["Source Data", "NINA", "Finished", "SirilWork/PI/nina_reject.json"]
SKIP = (".DS_Store",)


def local_files(ssd):
    """{relative path: (size, mtime)} of everything under PATHS on the SSD (macOS ._ files left out)."""
    out = {}
    for p in PATHS:
        full = os.path.join(ssd, p)
        if os.path.isfile(full):
            st = os.stat(full)
            out[p] = (st.st_size, st.st_mtime)
            continue
        for d, dirs, files in os.walk(full):
            dirs[:] = [x for x in dirs if not x.startswith(".")]
            for f in files:
                if f.startswith("._") or f in SKIP:
                    continue
                path = os.path.join(d, f)
                st = os.stat(path)
                out[os.path.relpath(path, ssd)] = (st.st_size, st.st_mtime)
    return out


def remote_files(boris, share):
    """{relative path: (size, mtime)} of the same paths on the share."""
    q = " ".join(shlex.quote(p) for p in PATHS)
    cmd = f"cd {shlex.quote(share)} && find {q} -type f -printf '%s\\t%T@\\t%p\\0' 2>/dev/null; true"
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", boris, cmd], capture_output=True, check=True)
    out = {}
    for rec in r.stdout.decode("utf-8", "surrogateescape").split("\0"):
        if rec:
            size, mtime, path = rec.split("\t", 2)
            out[path] = (int(size), float(mtime))
    return out


def gb(n):
    return f"{n / 1e9:.1f} GB"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", "-n", action="store_true", help="only show what would be copied")
    ap.add_argument("--ssd", default=os.environ.get("SSD", "/Volumes/scopessd"))
    ap.add_argument("--boris", default=os.environ.get("BORIS", "root@192.168.1.3"))
    ap.add_argument("--share", default=os.environ.get("GALLERY_ROOT", "/mnt/user/Telescopes"))
    a = ap.parse_args()
    if not os.path.isdir(os.path.join(a.ssd, "Source Data")):
        sys.exit(f"{a.ssd} is not mounted (no Source Data folder)")

    print(f"comparing {a.ssd} with {a.boris}:{a.share} ...", file=sys.stderr)
    mine, theirs = local_files(a.ssd), remote_files(a.boris, a.share)
    missing = sorted(p for p in mine if p not in theirs)
    differ = sorted(p for p in mine if p in theirs and theirs[p][0] != mine[p][0])
    # replace an empty or older share copy; keep one that is newer than the SSD's (2 s for SMB/FAT rounding)
    replace = [p for p in differ if theirs[p][0] == 0 or theirs[p][1] < mine[p][1] - 2]
    keep = [p for p in differ if p not in replace]
    todo = missing + replace

    by_top = {}
    for p in todo:
        parts = p.split("/")
        top = "/".join(parts[:2]) if parts[0] in ("Source Data", "NINA") and len(parts) > 2 else parts[0]
        n, s = by_top.get(top, (0, 0))
        by_top[top] = (n + 1, s + mine[p][0])
    total = sum(mine[p][0] for p in todo)
    print(f"{len(todo)} files, {gb(total)} to copy ({len(missing)} missing on the share, "
          f"{len(replace)} empty or older there)")
    for top, (n, s) in sorted(by_top.items(), key=lambda x: -x[1][1]):
        print(f"  {gb(s):>9} {n:6d}  {top}")
    for p in replace:
        print(f"  replace (share {theirs[p][0]} B, SSD {mine[p][0]} B): {p}")
    for p in keep:
        print(f"  left alone, the share copy is newer (share {theirs[p][0]} B, SSD {mine[p][0]} B): {p}")
    only_share = sum(1 for p in theirs if p not in mine)
    if only_share:
        print(f"  ({only_share} files exist only on the share; they are left alone)")
    if a.dry_run or not todo:
        return

    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as lst:
        lst.write("\n".join(todo) + "\n")
    try:
        # -rlt: no owner/group/permissions from the Mac; --files-from keeps the relative paths
        rc = subprocess.run(["rsync", "-rlt", "--partial", "--progress", f"--files-from={lst.name}",
                             a.ssd + "/", f"{a.boris}:{a.share}/"]).returncode
    finally:
        os.unlink(lst.name)
    # Unraid ownership for what was just copied, and for folders root created on the way
    dirs = sorted({q for p in todo for q in _parents(os.path.dirname(p))})
    script = ("cd {share} && while IFS= read -r -d '' p; do "
              "[ -e \"$p\" ] && [ \"$(stat -c %U \"$p\")\" = root ] && chown nobody:users \"$p\" && "
              "{{ [ -d \"$p\" ] && chmod 777 \"$p\" || chmod 666 \"$p\"; }}; done").format(share=shlex.quote(a.share))
    payload = "\0".join(todo + dirs) + "\0"
    subprocess.run(["ssh", "-o", "BatchMode=yes", a.boris, script], input=payload.encode(), check=False)
    if rc:
        sys.exit(f"rsync exited {rc}; run it again to finish (it only copies what is still missing)")
    print("done; run again with --dry-run to check: it should say 0 files")


def _parents(d):
    """'a/b/c' -> ['a', 'a/b', 'a/b/c']; '' -> []."""
    parts = [x for x in d.split("/") if x]
    return ["/".join(parts[:i]) for i in range(1, len(parts) + 1)]


if __name__ == "__main__":
    main()
