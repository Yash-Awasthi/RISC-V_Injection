# `demo/failures/` — Negative test cases for the `attnrec` matcher

This directory complements [`../sdpa_test.c`](../sdpa_test.c) (the
canonical *passing* SDPA) with a collection of deliberately crafted
*failing* SDPAs. Each file is a near-attention C program that the
`attnrec` pass rejects for one specific, well-understood reason.
The goal is to map every reject path in
[`../../gcc/gcc/tree-ssa-attn.cc`](../../gcc/gcc/tree-ssa-attn.cc) to
a concrete piece of C code, so that a reader can:

1. compile each file with `-mattn -O2 -S` and confirm that **no**
   `attn` instruction is emitted, and
2. dump the GIMPLE (`-fdump-tree-attnrec-details`) and see exactly
   which reject line in `attn_match` fired.

If even one of these files starts emitting `attn`, the matcher has
become too permissive and needs tightening — these are the regression
sentinels for false positives.

---

## How to run

Build the toolchain as documented in
[`../../README.md`](../../README.md), then from the repository root:

```bash
GCC=$HOME/riscv-install/bin/riscv64-unknown-elf-gcc

for f in demo/failures/fail_*.c; do
    $GCC -mattn -O2 \
         -fno-schedule-insns -fno-schedule-insns2 \
         -fdump-tree-attnrec-details \
         -S "$f" -o /tmp/out.s 2>/dev/null
    if grep -q '\battn\b' /tmp/out.s; then
        echo "FAIL  $f  — attn emitted unexpectedly"
    else
        echo "OK    $f  — correctly rejected"
    fi
done
```

To see *why* a particular file was rejected, look at the per-file
GIMPLE dump:

```bash
$GCC -mattn -O2 -fdump-tree-attnrec-details \
     -c demo/failures/fail_noexpf.c -o /dev/null
grep 'attnrec: loop' fail_noexpf.c.*attnrec*
# Expected line:
#   ;; attnrec: loop 1 rejected — missing softmax/sqrt
```

The reject reason printed by the pass corresponds one-to-one with the
"Matcher gate hit" line in each file's top-of-file comment.

---

## The seven failure cases

Each row below cross-references the exact early-return statement
inside `attn_match` (or its callees) that causes the rejection.

| File | Why it fails | Reject statement in `tree-ssa-attn.cc` |
|------|-------------|----------------------------------------|
| [`fail_loop-missing.c`](./fail_loop-missing.c) | Body is fully hand-unrolled — no loops at all. | `execute()`: `if (number_of_loops (fun) <= 1) return 0;` and `loop_depth (loop) != 1` filter. |
| [`fail_main-not-nested.c`](./fail_main-not-nested.c) | Code is written inside `main` as a single flat loop, no inner nest. | `attn_match`: `if (!outer->inner) return false;` |
| [`fail_no-madd-reduction.c`](./fail_no-madd-reduction.c) | Inner reduction uses `fabsf(a-b)` instead of `a*b` — PHI's back-edge is fed from PLUS(ABS(MINUS)), not PLUS(MULT). | `attn_match`: `if (!found_madd) return false;` (via `attn_find_madd_reduction`) |
| [`fail_extra-op-in-inner-loop.c`](./fail_extra-op-in-inner-loop.c) | Extra `+ bias[d]` *inside* the inner loop poisons the madd PHI shape. | Same as above — `attn_find_madd_reduction` returns NULL because the PHI back-edge feeds from a second PLUS_EXPR, not from a MULT_EXPR. |
| [`fail_noexpf.c`](./fail_noexpf.c) | Softmax replaced by ReLU + multiplicative inverse — no `expf` call and no `RDIV_EXPR` in the function. | `attn_match`: `if (!attn_has_softmax_and_scale (outer)) return false;` |
| [`fail_only-two-bases.c`](./fail_only-two-bases.c) | V is aliased to K, leaving only two distinct non-local load bases. | `attn_match`: `if (load_bases.length () < 3 || !store_base) return false;` |
| [`fail_unfused-three-toplevel-loops.c`](./fail_unfused-three-toplevel-loops.c) | The three phases (QK^T, softmax, SV) are written as three sibling top-level loop nests; no single nest sees Q, K, V together. | Same as above — every per-loop attempt sees `load_bases.length () < 3`. |
| [`fail_unknown-trip-count.c`](./fail_unknown-trip-count.c) | Outer loop bound is a `volatile` global plus a data-dependent `break`; SCEV returns `chrec_dont_know`. | `attn_match`: `if (n == chrec_dont_know) return false;` |

That is eight files in total — one for each independent reject path
in the matcher.

---

## The passing condition (mirror image of the above)

A function will be recognised by `attnrec` and rewritten into a
single `attn rd, rs1, rs2, rs3` instruction **if and only if** every
one of the following holds — they are exactly the negations of the
eight rows above.

1. The pass gate is open: compiled with `-mattn`, `-O2` (or higher)
   and `-ftree-loop-optimize` (on by default at O2).
2. The function contains at least one non-root loop, and at least
   one of them is a *direct child* of the root loop tree
   (`loop_depth == 1`).
3. That top-level loop has at least one inner loop
   (`outer->inner != NULL`).
4. Somewhere in the nest there is a fused multiply-add reduction —
   a PHI whose latch-edge value is `acc + (a * b)` — detected by
   `attn_find_madd_reduction`.
5. Somewhere in the function (not just in this loop) there is at
   least one `expf` / `exp` call **and** at least one floating-point
   division (`RDIV_EXPR`) — the softmax signature.
6. After stripping local stack variables, the function's loads use
   at least three distinct base pointers (Q, K, V).
7. The function has at least one non-local store base (O).
8. The outer loop's latch trip count is statically known to SCEV —
   typically a closed-form `for (i = 0; i < N; i++)` with a
   compile-time-constant or loop-invariant `N`.

The canonical reference satisfying all eight conditions is
[`../sdpa_test.c`](../sdpa_test.c).

---

## Adding a new failure case

If you change the matcher in `tree-ssa-attn.cc` and introduce a new
reject path, please add a corresponding `fail_<short-name>.c` here.
The convention for each file is:

* file name: `fail_<kebab-case-reason>.c`;
* top-of-file block-comment with the four sections used in every
  existing file: **Failure category**, **Matcher gate hit**,
  **Why this fails**, **How to make this pass**;
* the body should be as close to a passing SDPA as possible, with
  exactly one deliberate deviation — otherwise it is hard to tell
  which check the matcher actually tripped over.

Then add a row to the table above with a link and a one-line
explanation. The harness loop in [§ How to run](#how-to-run) picks
up every `fail_*.c` automatically, so no script edit is needed.
