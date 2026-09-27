"""Find duplicated code across the source tree, in any language of languages.json.
Tokens are normalized (identifiers that are not keywords become one symbol, literals another), so a
copy with renamed variables still matches. Every window of --min-tokens tokens is hashed with a rolling
hash; matching windows are extended to the longest common run and grouped into clone groups. Each group
says whether its copies are identical or differ only in names. Comments never count.

    python clones.py --out <run>/clones.json [--min-tokens 60]
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from functools import cache
from pathlib import Path

import inventory as inv

MIN_TOKENS = 60
MAX_BUCKET = 40          # windows repeated more often than this are boilerplate (tables, generated lists)
MAX_PERIOD = 16          # a run that repeats itself with a period this short is a table or a list, not a copy
PERIODIC_SHARE = 0.75    # ...when this share of its tokens match the tokens one period later
HASH_BASE = 1_000_003
HASH_MOD = (1 << 61) - 1
IDENT_SYMBOL = "$I"
LITERAL_SYMBOL = "$L"
REPORT_GROUPS = 15
LIST_GROUPS = 200


@cache
def keywords(lang: str) -> frozenset[str]:
    """Return a language's keywords, which stay literal during normalization.
    Args:
        lang: The language key.
    Returns:
        The keyword set.
    """
    return frozenset(inv.language(lang).get("keywords", []))


class Stream:
    """All files' normalized tokens in one sequence, with file boundaries that never match."""

    def __init__(self) -> None:
        """Start empty."""
        self.ids: list[int] = []
        self.raw: list[str] = []
        self.line: list[int] = []
        self.file: list[int] = []
        self.files: list[str] = []
        self.vocab: dict[str, int] = {}

    def add(self, sf: inv.SourceFile) -> None:
        """Append one file's tokens, then a unique boundary token.
        Args:
            sf: The source file.
        """
        kws = keywords(sf.lang)
        fi = len(self.files)
        self.files.append(sf.rel)
        for kind, text, line in inv.tokens(sf):
            if kind == "i":
                norm = text if text in kws else IDENT_SYMBOL
            elif kind in ("s", "n"):
                norm = LITERAL_SYMBOL
            else:
                norm = text
            self.ids.append(self.vocab.setdefault(norm, len(self.vocab) + 1))
            self.raw.append(text)
            self.line.append(line)
            self.file.append(fi)
        self.ids.append(-(fi + 1))
        self.raw.append("")
        self.line.append(0)
        self.file.append(fi)


def windows(ids: list[int], width: int) -> dict[int, list[int]]:
    """Hash every window of width tokens that does not cross a file boundary.
    Args:
        ids: The token ids (boundaries are negative).
        width: The window length.
    Returns:
        Hash to the window start positions.
    """
    buckets: dict[int, list[int]] = defaultdict(list)
    top = pow(HASH_BASE, width - 1, HASH_MOD)
    h, last_boundary = 0, -1
    for i, v in enumerate(ids):
        if v < 0:
            h, last_boundary = 0, i
            continue
        if i - width > last_boundary:
            h = (h - ids[i - width] * top) % HASH_MOD
        h = (h * HASH_BASE + v) % HASH_MOD
        start = i - width + 1
        if start > last_boundary:
            buckets[h].append(start)
    return buckets


def periodic(ids: list[int], start: int, n: int) -> bool:
    """Tell whether a run mostly repeats itself with a short period (field lists, widget rows, tables).
    Args:
        ids: The token ids.
        start: The run's first position.
        n: The run's length.
    Returns:
        True when some period up to MAX_PERIOD matches at least PERIODIC_SHARE of the run.
    """
    for p in range(1, min(MAX_PERIOD, n // 2) + 1):
        same = sum(1 for i in range(start, start + n - p) if ids[i] == ids[i + p])
        if same >= PERIODIC_SHARE * (n - p):
            return True
    return False


def find_pairs(s: Stream, width: int) -> tuple[list[tuple[int, int, int]], int]:
    """Find maximal duplicated runs.
    Args:
        s: The token stream.
        width: The minimum run length.
    Returns:
        (start a, start b, length) triples, and the number of boilerplate buckets skipped.
    """
    ids = s.ids
    pairs, skipped = [], 0
    for starts in windows(ids, width).values():
        if len(starts) < 2:
            continue
        if len(starts) > MAX_BUCKET:
            skipped += 1
            continue
        for x, a in enumerate(starts):
            for b in starts[x + 1:]:
                if a > 0 and ids[a - 1] >= 0 and ids[a - 1] == ids[b - 1]:
                    continue  # not a maximal start: the run began one token earlier
                if ids[a:a + width] != ids[b:b + width]:
                    continue  # hash collision
                n = width
                while b + n < len(ids) and ids[a + n] >= 0 and ids[a + n] == ids[b + n]:
                    n += 1
                if s.file[a] == s.file[b] and b < a + n:
                    continue  # a run that runs into its own copy is repetition, not a copy
                if not periodic(ids, a, n):
                    pairs.append((a, b, n))
    return pairs, skipped


def merge_sites(sites: list[tuple[str, int, int]]) -> list[tuple[str, int, int]]:
    """Merge sites that overlap in the same file into one region.
    Args:
        sites: (file, start line, end line), sorted.
    Returns:
        The merged sites.
    """
    out: list[tuple[str, int, int]] = []
    for f, start, end in sites:
        if out and out[-1][0] == f and start <= out[-1][2]:
            out[-1] = (f, out[-1][1], max(end, out[-1][2]))
        else:
            out.append((f, start, end))
    return out


def group(s: Stream, pairs: list[tuple[int, int, int]]) -> list[dict]:
    """Group pairs whose normalized runs are the same into clone groups.
    Args:
        s: The token stream.
        pairs: Maximal runs from find_pairs.
    Returns:
        Groups with tokens, lines, copies, kind (identical or renamed) and file:start-end sites.
    """
    groups: dict[tuple, dict] = {}
    for a, b, n in pairs:
        key = (n, hash(tuple(s.ids[a:a + n])))
        g = groups.setdefault(key, {"starts": set(), "identical": True, "tokens": n})
        g["starts"].update((a, b))
        if s.raw[a:a + n] != s.raw[b:b + n]:
            g["identical"] = False
    out = []
    for g in groups.values():
        n = g["tokens"]
        sites = merge_sites(sorted({(s.files[s.file[p]], s.line[p], s.line[p + n - 1]) for p in g["starts"]}))
        if len(sites) < 2:
            continue
        lines = max(end - start + 1 for _, start, end in sites)
        out.append({"tokens": n, "lines": lines, "copies": len(sites),
                    "kind": "identical" if g["identical"] else "renamed",
                    "sites": [f"{f}:{start}-{end}" for f, start, end in sites]})
    out.sort(key=lambda g: -g["tokens"] * (g["copies"] - 1))
    return out


def main() -> int:
    """Parse arguments, find clone groups, write JSON and print the largest.
    Returns:
        The process exit code.
    """
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    inv.add_common_args(ap)
    ap.add_argument("--min-tokens", type=int, default=MIN_TOKENS, help="shortest run that counts as a clone")
    ap.add_argument("--out", type=Path, help="write clones.json here")
    args = ap.parse_args()
    ctx = inv.load(args)
    stream = Stream()
    for sf in ctx.files:
        stream.add(sf)
    pairs, skipped = find_pairs(stream, args.min_tokens)
    groups = group(stream, pairs)
    duplicated = sum(g["lines"] * (g["copies"] - 1) for g in groups)
    total_lines = sum(sf.lines for sf in ctx.files)
    result = {"min_tokens": args.min_tokens, "files": len(ctx.files), "lines": total_lines,
              "tokens": len(stream.ids) - len(stream.files), "groups_total": len(groups),
              "duplicated_lines": duplicated, "boilerplate_buckets_skipped": skipped,
              "groups": groups[:LIST_GROUPS]}
    if args.out:
        inv.write_json(args.out, result)
    w = sys.stdout.write
    share = 100.0 * duplicated / max(total_lines, 1)
    w(f"{len(groups)} clone groups at >= {args.min_tokens} tokens; ~{duplicated} duplicated lines "
      f"({share:.1f}% of {total_lines}); {skipped} boilerplate buckets skipped\n")
    for g in groups[:REPORT_GROUPS]:
        w(f"  {g['tokens']:5} tokens  {g['lines']:4} lines  x{g['copies']}  {g['kind']:9}  "
          + ", ".join(g["sites"][:4]) + (" ..." if len(g["sites"]) > 4 else "") + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
