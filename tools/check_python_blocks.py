#!/usr/bin/env python3
"""Run the draft's python blocks and check them against an independently built MMR.

    python3 tools/check_python_blocks.py                 # checks; exit 0 = all pass
    python3 tools/check_python_blocks.py --leaves 64     # a smaller tree (faster)
    python3 tools/check_python_blocks.py --write-vectors tools/known-answers.json
    python3 tools/check_python_blocks.py --check-vectors tools/known-answers.json

Standard library only. The blocks are taken from the draft markdown exactly as
written, with two mechanical changes so the pseudocode runs:

  * each block is dedented;
  * a comment of the form `# if <cond> -> ERROR` (or `# elif`) becomes
    `if <cond>: raise ProofError(...)`, with `length(x)` read as `len(x)`.
    The rejections the text describes are therefore exercised, not skipped.

Primitives the text describes only in prose are supplied by this script and
reported (see PROSE_ONLY). A block that is an expression rather than a
function definition is reported and not executed.
"""
import argparse
import hashlib
import json
import re
import signal
import sys
import textwrap
from pathlib import Path

DRAFT = Path(__file__).resolve().parent.parent / "draft-bryce-cose-receipts-mmr-profile.md"

# Described in "Assumed bit primitives" without a python block.
PROSE_ONLY = {"ones_count": lambda v: bin(v).count("1")}


class ProofError(Exception):
    """A `-> ERROR` rejection from the pseudocode."""


def extract_blocks(text):
    """(line number, source) for every fenced python block."""
    blocks, lines, i = [], text.split("\n"), 0
    while i < len(lines):
        m = re.match(r"^\s*(~~~+|```+)\s*python\s*$", lines[i])
        if not m:
            i += 1
            continue
        fence, start, body = m.group(1), i + 1, []
        i += 1
        while i < len(lines) and lines[i].strip() != fence:
            body.append(lines[i])
            i += 1
        blocks.append((start + 1, textwrap.dedent("\n".join(body))))
        i += 1
    return blocks


ERROR_RE = re.compile(r"^(\s*)#\s*(if|elif)\s+(.+?)\s*->\s*ERROR\s*$")


def executable(src, line):
    out = []
    for n, l in enumerate(src.split("\n")):
        m = ERROR_RE.match(l)
        if m:
            ind, kw, cond = m.groups()
            cond = re.sub(r"\blength\(", "len(", cond)
            msg = f"draft line {line + n}: {cond}"
            out.append(f"{ind}{kw} {cond}: raise ProofError({msg!r})")
        else:
            out.append(l)
    return "\n".join(out)


def load(draft=DRAFT, report=print):
    ns = {"hashlib": hashlib, "ProofError": ProofError}
    skipped = []
    for line, src in extract_blocks(Path(draft).read_text()):
        if not re.match(r"^\s*def\s", src):
            skipped.append((line, src.strip()))
            continue
        exec(compile(executable(src, line), f"draft:{line}", "exec"), ns)
    for name, fn in PROSE_ONLY.items():
        if name not in ns:
            ns[name] = fn
            report(f"NOTE {name}: described in prose only; supplied by this script")
    for line, src in skipped:
        report(f"NOTE draft line {line}: not a function definition, not executed: {src}")
    return ns


# ---- an independent reference MMR (no draft code) ----------------------------

def leaf(n):
    """Leaf value n: SHA-256 of the 8-byte big-endian leaf number."""
    return hashlib.sha256(n.to_bytes(8, "big")).digest()


def reference_mmr(nleaves):
    """Node values and heights, built by the textbook carry rule."""
    nodes, heights, stack = [], [], []  # stack of (height, index)
    for n in range(nleaves):
        nodes.append(leaf(n)); heights.append(0); h, i = 0, len(nodes) - 1
        stack.append((0, i))
        while len(stack) >= 2 and stack[-1][0] == stack[-2][0]:
            (hr, ir), (hl, il) = stack.pop(), stack.pop()
            pos = len(nodes) + 1
            v = hashlib.sha256(pos.to_bytes(8, "big") + nodes[il] + nodes[ir]).digest()
            nodes.append(v); heights.append(hr + 1)
            stack.append((hr + 1, len(nodes) - 1))
    return nodes, heights


def complete_sizes(nleaves):
    return [2 * k - bin(k).count("1") for k in range(nleaves + 1)]


def peaks_of(size, heights):
    """Indices of the peaks of MMR(size), high to low, from the reference."""
    out, i, left = [], 0, size
    while left:
        h = (left + 1).bit_length() - 1
        span = (1 << h) - 1
        while span > left:
            h -= 1
            span = (1 << h) - 1
        i += span
        out.append(i - 1)
        left -= span
    return out


# ---- checks -------------------------------------------------------------------

def run_checks(ns, nleaves, report=print):
    nodes, heights = reference_mmr(nleaves)
    sizes = complete_sizes(nleaves)
    ok = True

    def fail(msg):
        nonlocal ok
        ok = False
        report("FAIL " + msg)

    # add_leaf_hash builds the same node values.
    class DB:
        def __init__(self): self.v = []
        def append(self, x): self.v.append(x); return len(self.v)
        def get(self, i): return self.v[i]
    db = DB()
    for n in range(nleaves):
        size = ns["add_leaf_hash"](db, leaf(n))
        if size != sizes[n + 1]:
            fail(f"add_leaf_hash: size after leaf {n} is {size}, want {sizes[n + 1]}")
            break
    if db.v != nodes:
        fail("add_leaf_hash: node values differ from the reference MMR")
    report(f"ok   add_leaf_hash: {nleaves} leaves, {len(nodes)} nodes, values match the reference")

    # index_height = structural height; MMR(n) complete iff index_height(n) == 0.
    bad = [i for i in range(len(nodes)) if ns["index_height"](i) != heights[i]]
    if bad:
        fail(f"index_height differs at {bad[:5]}")
    complete = set(sizes)
    bad = [n for n in range(1, len(nodes)) if (ns["index_height"](n) == 0) != (n in complete)]
    if bad:
        fail(f"completeness test index_height(n) == 0 wrong at {bad[:5]}")
    report(f"ok   index_height: {len(nodes)} nodes; completeness test at {len(nodes) - 1} sizes")

    # peaks / leaf_count / mmr_size_for_leaf_count against the reference.
    for k, s in enumerate(sizes[1:], 1):
        if ns["peaks"](s - 1) != peaks_of(s, heights):
            fail(f"peaks({s - 1}) = {ns['peaks'](s - 1)}, want {peaks_of(s, heights)}"); break
        if ns["leaf_count"](s - 1) != k:
            fail(f"leaf_count({s - 1}) = {ns['leaf_count'](s - 1)}, want {k}"); break
        if ns["mmr_size_for_leaf_count"](k) != s:
            fail(f"mmr_size_for_leaf_count({k}) wrong"); break
    report(f"ok   peaks, leaf_count, mmr_size_for_leaf_count: {nleaves} complete sizes")

    # Inclusion: every node at every complete size proves to the peak above it.
    n_checked = 0
    for s in sizes[1:]:
        c = s - 1
        pk = peaks_of(s, heights)
        for i in range(s):
            path = ns["inclusion_proof_path"](i, c)
            root = ns["included_root"](i, nodes[i], [nodes[x] for x in path])
            covering = next(p for p in pk if p >= i)
            if root != nodes[covering]:
                fail(f"inclusion: node {i} in MMR({s}) proves {root.hex()[:16]}, want peak {covering}")
                return ok
            n_checked += 1
    report(f"ok   inclusion_proof_path + included_root: {n_checked} (node, size) pairs")

    # Consistency: every pair of complete sizes, including from 0.
    n_pairs = 0
    for a, sa in enumerate(sizes):
        for sb in sizes[a + 1:]:
            ifrom_peaks = peaks_of(sa, heights) if sa else []
            acc = [nodes[p] for p in ifrom_peaks]
            proofs = [[nodes[x] for x in path] for path in
                      (ns["consistency_proof_paths"](sa - 1, sb - 1) if sa else [])]
            try:
                roots, nright = ns["consistent_roots"](sa, sb, acc, proofs)
            except ProofError as e:
                fail(f"consistency {sa} -> {sb} refused: {e}")
                return ok
            tp = [nodes[p] for p in peaks_of(sb, heights)]
            if roots + tp[len(roots):] != tp or len(tp) - len(roots) != nright:
                fail(f"consistency {sa} -> {sb}: roots do not complete the target accumulator")
                return ok
            n_pairs += 1
    report(f"ok   consistency_proof_paths + consistent_roots: {n_pairs} pairs of complete sizes (from 0)")

    # Each `-> ERROR` rejection fires on material made wrong in exactly one way.
    def rejects(label, want, *args):
        """`want`: the condition text of the `-> ERROR` line that must fire."""
        try:
            ns["consistent_roots"](*args)
        except ProofError as e:
            if want in str(e):
                return True
            fail(f"rejection {label!r}: raised by another check ({e}), not by '{want}'")
            return False
        fail(f"rejection not raised: {label}")
        return False

    def honest(sa, sb):
        acc = [nodes[p] for p in peaks_of(sa, heights)]
        prf = [[nodes[x] for x in p] for p in ns["consistency_proof_paths"](sa - 1, sb - 1)]
        return acc, prf
    s7, s8, s10, s16, s22 = 7, 8, 10, 16, 22
    s11, s39 = 11, 39
    acc, prf = honest(s11, s39)
    cases = [
        ("sizeto <= sizefrom", "sizeto <= sizefrom", (s11, s11, acc, prf)),
        ("sizeto not a complete MMR size", "mmr_size_for_leaf_count(leavesto) != sizeto", (s11, 40, acc, prf)),
        ("accumulator length", "len(accumulatorfrom) != n", (s11, s39, acc + [acc[0]], prf)),
        ("proofs length", "len(proofs) != n", (s11, s39, acc, prf[:-1])),
        ("path length below split", "len(proofs[i]) != split - h", (s11, s39, acc, [prf[0] + [nodes[0]]] + prf[1:])),
        ("proven != root", "proven != root", (s11, s39, acc, [prf[0], [bytes(32)] * len(prf[1])] + prf[2:])),
    ]
    acc16, prf16 = honest(s16, s22)  # 16 -> 22: origin peak above the split
    cases.append(("non-empty path above split", "len(proofs[i]) != 0", (s16, s22, acc16, [prf16[0] + [nodes[0]]] + prf16[1:])))
    passed = sum(rejects(label, want, *args) for label, want, args in cases)
    report(f"ok   consistent_roots rejections: {passed}/{len(cases)} fire")
    return ok


# ---- known-answer vectors -------------------------------------------------------

def vectors(ns):
    nodes, heights = reference_mmr(21)  # MMR(39): 21 leaves
    assert len(nodes) == 39
    hx = lambda b: b.hex()

    def inclusion(i, size):
        path = ns["inclusion_proof_path"](i, size - 1)
        return {"index": i, "size": size, "node": hx(nodes[i]), "path_indices": path,
                "path": [hx(nodes[x]) for x in path],
                "root": hx(ns["included_root"](i, nodes[i], [nodes[x] for x in path]))}

    def consistency(sa, sb):
        acc = [nodes[p] for p in peaks_of(sa, heights)] if sa else []
        paths = ns["consistency_proof_paths"](sa - 1, sb - 1) if sa else []
        prf = [[nodes[x] for x in p] for p in paths]
        roots, nright = ns["consistent_roots"](sa, sb, acc, prf)
        tp = peaks_of(sb, heights)
        return {"sizefrom": sa, "sizeto": sb, "accumulatorfrom": [hx(x) for x in acc],
                "proofs": [[hx(x) for x in p] for p in prf], "roots": [hx(x) for x in roots],
                "right_peaks_required": nright,
                "accumulatorto": [hx(nodes[p]) for p in tp]}

    def confusable(sa, sb_honest, sb_claimed):
        """The honest sb proof material, folded at a different declared size."""
        acc = [nodes[p] for p in peaks_of(sa, heights)]
        prf = [[nodes[x] for x in p] for p in ns["consistency_proof_paths"](sa - 1, sb_honest - 1)]
        roots, nright = ns["consistent_roots"](sa, sb_claimed, acc, prf)
        return {"sizefrom": sa, "honest_sizeto": sb_honest, "declared_sizeto": sb_claimed,
                "fold_accepts": True, "roots": [hx(x) for x in roots],
                "right_peaks_required": nright,
                "note": "the fold accepts; only the signed tree-size-2 compared with the "
                        "proof's tree-size-2 rejects this (see Security Considerations)"}

    return {
        "description": "Known-answer values for draft-bryce-cose-receipts-mmr-profile, "
                       "computed by running the draft's python blocks "
                       "(tools/check_python_blocks.py). Leaf n = SHA-256(n as 8-byte big-endian).",
        "hash": "SHA-256",
        "mmr39": {"leaves": 21, "nodes": [hx(x) for x in nodes], "heights": heights},
        "inclusion": [
            inclusion(0, 39),             # the first leaf: the longest path (4)
            inclusion(17, 39),            # an interior node of height 1
            inclusion(30, 39),            # the height-4 peak: empty path, root = node
            inclusion(38, 39),            # the last leaf, a height-0 peak: empty path
        ],
        "consistency": [
            consistency(0, 39),           # from the empty tree
            consistency(7, 39),
            consistency(11, 39),          # several origin peaks below the split
            consistency(19, 26),          # one origin peak above the split (empty path)
            consistency(31, 38),          # a single origin peak, one right-peak
        ],
        "confusable_sizes": [
            confusable(7, 8, 10),         # empty-path case (note, 26 Sep 2026)
            confusable(7, 16, 18),        # carried case
            confusable(7, 16, 22),
        ],
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--draft", default=str(DRAFT))
    ap.add_argument("--leaves", type=int, default=256)
    ap.add_argument("--write-vectors")
    ap.add_argument("--check-vectors")
    ap.add_argument("--timeout", type=int, default=120, help="seconds before a non-terminating block counts as a failure")
    a = ap.parse_args()

    def timed_out(*_):
        raise TimeoutError(f"no result after {a.timeout}s: a block does not terminate")
    signal.signal(signal.SIGALRM, timed_out)
    signal.alarm(a.timeout)
    try:
        ns = load(a.draft)
        ok = run_checks(ns, a.leaves)
        v = vectors(ns)
    except Exception as e:  # the draft's code raised or hung: a failure, not a crash
        print(f"FAIL {type(e).__name__}: {e}")
        print("FAILED")
        return 1
    finally:
        signal.alarm(0)
    if a.write_vectors:
        Path(a.write_vectors).write_text(json.dumps(v, indent=1) + "\n")
        print(f"wrote {a.write_vectors}")
    if a.check_vectors:
        if json.loads(Path(a.check_vectors).read_text()) != v:
            print(f"FAIL {a.check_vectors} differs from the values the draft's code computes")
            ok = False
        else:
            print(f"ok   {a.check_vectors} matches")
    print("PASS" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
