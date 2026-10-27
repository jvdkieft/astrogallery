"""Tiny YAML-subset reader for picks.yaml, used when PyYAML is not installed.

Handles what picks.yaml uses: block mappings and lists, nested mappings, flow lists
([a, b] or ["a", "b"]), quoted and plain scalars, true/false, numbers and # comments.
Anything fancier (anchors, multi-line strings, flow mappings) is not supported.
"""
import re


def _strip_comment(line):
    out, quote = [], None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'" and line[:i].rstrip()[-1:] in ("", ":", "-", "[", ","):  # a quote starts a value only there
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            break
        out.append(ch)
    return "".join(out).rstrip()


def _split_flow(s):
    items, cur, quote = [], [], None
    for ch in s:
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            cur.append(ch)
        elif ch == ",":
            items.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if "".join(cur).strip():
        items.append("".join(cur))
    return [_scalar(i) for i in items]


def _scalar(s):
    s = s.strip()
    if not s:
        return None
    if s[0] == s[-1] and s[0] in "\"'" and len(s) >= 2:
        return s[1:-1].replace('\\"', '"').replace("\\\\", "\\") if s[0] == '"' else s[1:-1].replace("''", "'")
    if s.startswith("[") and s.endswith("]"):
        return _split_flow(s[1:-1])
    low = s.lower()
    if low in ("true", "yes"):
        return True
    if low in ("false", "no"):
        return False
    if low in ("null", "~"):
        return None
    if re.fullmatch(r"-?\d+", s):
        return int(s)
    if re.fullmatch(r"-?\d+\.\d*", s):
        return float(s)
    return s


def loads(text):
    text = text.lstrip("﻿")  # a BOM would otherwise be part of the first key
    toks = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = _strip_comment(raw)
        if line.strip():
            if "\t" in line[:len(line) - len(line.lstrip())]:
                raise ValueError(f"line {n}: tabs in the indentation; use spaces")
            toks.append([len(line) - len(line.lstrip(" ")), line.strip()])
    pos = 0

    def block(ind):
        nonlocal pos
        if toks[pos][1].startswith("- ") or toks[pos][1] == "-":
            out = []
            while pos < len(toks) and toks[pos][0] == ind and toks[pos][1].startswith("-"):
                rest = toks[pos][1][1:].lstrip()
                if not rest:
                    pos += 1
                    if pos >= len(toks) or toks[pos][0] <= ind:
                        raise ValueError("a list item '-' without a value")
                    out.append(block(toks[pos][0]))
                elif re.match(r"[^\"'\[]*?:(\s|$)", rest):
                    toks[pos] = [ind + 2, rest]  # "- key: v" starts a mapping one level in
                    out.append(block(ind + 2))
                else:
                    out.append(_scalar(rest))
                    pos += 1
            return out
        out = {}
        while pos < len(toks) and toks[pos][0] == ind:
            m = re.match(r"([^:]+?):(?:\s+(.*))?$", toks[pos][1])
            if not m:
                raise ValueError(f"cannot parse line: {toks[pos][1]!r}")
            key, rest = m[1].strip(), m[2]
            pos += 1
            if rest:
                out[key] = _scalar(rest)
            elif pos < len(toks) and toks[pos][0] > ind:
                out[key] = block(toks[pos][0])
            else:
                out[key] = None
        return out

    if not toks:
        return None
    out = block(toks[0][0])
    if pos < len(toks):  # a misindented line would otherwise silently end the file here
        raise ValueError(f"cannot place line {toks[pos][1]!r} (indentation?)")
    return out


def load(path):
    try:
        import yaml
    except ImportError:
        with open(path, encoding="utf-8-sig") as f:  # -sig: Notepad's BOM would otherwise be part of the first key
            return loads(f.read())
    with open(path, encoding="utf-8-sig") as f:
        return yaml.safe_load(f)
