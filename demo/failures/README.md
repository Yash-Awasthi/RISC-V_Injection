# `demo/failures/` — Negative test cases for the `attnrec` matcher

This directory complements [`../sdpa_test.c`](../sdpa_test.c) (the
canonical *passing* SDPA) with a collection of deliberately crafted
*failing* SDPAs. Each file is a near-attention C program that the
`attnrec` pass rejects for one specific, well-understood reason.
The goal is to map every reject path in
[`../../gcc/gcc/tree-ssa-attn.cc`](../../gcc/gcc/tree-ssa-attn.cc) to
a concrete piece of C code, so that a reader can:

1. compile each file with `-mattn -mattn-recognize -O2 -S` and
   confirm that **no** `attn` instruction is emitted, and
2. dump the GIMPLE (`-fdump-tree-attnrec-details`) and see exactly
   which reject line in `attn_match` fired.

**One exception.** `fail_scattered-signature-known-false-positive.c`
is not expected to be correctly rejected by the matcher as it stands
today — it is a documented correctness hazard from `plan.md`, not a
verified reject. See [its own top-of-file
comment](./fail_scattered-signature-known-false-positive.c) and the
"Known false positive" section below before assuming a `FAIL` on that
one file means this corpus is broken.

These files guard the experimental idiom recognizer only
(`-mattn-recognize`, see [`../../docs/02-compiler-pass.md`](../../docs/02-compiler-pass.md)).
They say nothing about the non-experimental `attn` instruction or
`__builtin_riscv_attn`, gated by plain `-mattn`: the builtin takes
its four pointer arguments literally, at the exact call site the
programmer wrote, so there is no shape for a matcher to guess wrong
and no false-positive risk to guard against. If even one of these
files starts emitting `attn` under `-mattn -mattn-recognize`, the
matcher has become too permissive and needs tightening — these are
the regression sentinels for false positives.

---

## How to run

**Nothing in this directory has been compiled as part of adding the
two newest files** (`fail_matmul-then-rownormalize.c` and
`fail_scattered-signature-known-false-positive.c`) — no
`riscv64-unknown-elf-gcc` toolchain was built or available in the
environment that wrote them. Their "why it fails" reasoning is a hand
trace against `attn_match` in `tree-ssa-attn.cc`, cross-checked
statement by statement, not a captured `PASS`/`FAIL` from a real
build. Build the toolchain as documented in
[`../../README.md`](../../README.md) and run the sweep below yourself
before treating any claim in this directory as verified.

```bash
GCC=$HOME/riscv-install/bin/riscv64-unknown-elf-gcc

for f in demo/failures/fail_*.c; do
    $GCC -mattn -mattn-recognize -O2 \
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
$GCC -mattn -mattn-recognize -O2 -fdump-tree-attnrec-details \
     -c demo/failures/fail_noexpf.c -o /dev/null
grep 'attnrec: loop' fail_noexpf.c.*attnrec*
# Expected line:
#   ;; attnrec: loop 1 rejected — missing softmax/sqrt
```

The reject reason printed by the pass corresponds one-to-one with the
"Matcher gate hit" line in each file's top-of-file comment.

---

## The ten failure cases

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
| [`fail_matmul-then-rownormalize.c`](./fail_matmul-then-rownormalize.c) | Ordinary matmul plus row-sum normalization: has the madd reduction, three load bases, a store, and a division, but no `expf` anywhere — it is not softmax. | `attn_match`: `if (!attn_has_softmax_and_scale (outer)) return false;` |
| [`fail_scattered-signature-known-false-positive.c`](./fail_scattered-signature-known-false-positive.c) | **Known false positive, not a verified reject** — an FIR filter, two unrelated elementwise loops, and an unrelated `expf`/division gain calculation, all in one function. Traced by hand against `attn_match`, every one of its eight conditions holds even though nothing in the file is attention. See the "Known false positive" section below. | None — this is the correctness hazard `plan.md` names, not a reject path. |

That is ten files in total: eight independent reject paths, plus one
realistic near-miss (`fail_matmul-then-rownormalize.c`) and one
documented false-positive hazard
(`fail_scattered-signature-known-false-positive.c`).

---

## Known false positive: `fail_scattered-signature-known-false-positive.c`

Every other file in this table is a *verified* reject: its top-of-file
comment traces which `attn_match` condition fails, and the file was
written specifically to fail exactly that one condition and no other.
`fail_scattered-signature-known-false-positive.c` is different on
purpose. It is built to satisfy all eight conditions at once, the way
`plan.md` describes the hazard: "A function with three unrelated
pointer-chasing loops, an unrelated `expf` call, and an unrelated
division anywhere else in the same function would also match." Tracing
`attn_match` by hand against that file (see its own top comment for
the condition-by-condition walkthrough) finds no failing check, which
means the expected behavior of the pass as it stands today is a false
positive — `attn` gets emitted over an FIR filter that has nothing to
do with attention.

This file's presence in the corpus does not mean the corpus itself is
broken. It means:

* the sweep in [§ How to run](#how-to-run) is expected to print `FAIL`
  for this one file until the matcher is fixed, and `OK` for the other
  nine;
* the fix belongs in `attn_match` (require the madd reduction, the
  softmax pair, and the load/store bases to resolve inside the same
  candidate loop, in real data-dependency order — `plan.md`, "Next
  steps" item 1), not in this test file;
* this file has not been run through a built toolchain as part of
  this change (no `riscv64-unknown-elf-gcc` was available in this
  environment) — the "every condition holds" claim above is a hand
  trace against `tree-ssa-attn.cc`, not a captured compiler run. Build
  the toolchain and run the sweep yourself to get an authoritative
  answer.

---

## The passing condition (mirror image of the above)

A function will be recognised by `attnrec` and rewritten into a
single `attn rd, rs1, rs2, rs3` instruction **if and only if** every
one of the following holds — they are exactly the negations of the
eight verified-reject rows above (`fail_loop-missing.c` through
`fail_unknown-trip-count.c`, plus `fail_matmul-then-rownormalize.c`,
which fails the same condition 5 as `fail_noexpf.c`).
`fail_scattered-signature-known-false-positive.c` is the counter-
example: per the hand trace in its own comment, it satisfies every
condition below without being attention, which is exactly the gap
this list does not close — the eight conditions are independently
necessary but not jointly sufficient to prove the matched code
actually computes SDPA.

1. The pass gate is open: compiled with `-mattn -mattn-recognize`,
   `-O2` (or higher) and `-ftree-loop-optimize` (on by default at
   O2). `-mattn` alone is not enough — it only makes the `attn`
   instruction and its builtin available, see
   [`../../docs/02-compiler-pass.md`](../../docs/02-compiler-pass.md).
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

The canonical reference intended to satisfy all eight conditions is
[`../sdpa_test.c`](../sdpa_test.c) — but, per the section above,
it is not the only file that does:
`fail_scattered-signature-known-false-positive.c` satisfies the same
eight conditions without computing attention, which is exactly why
"satisfies these eight conditions" cannot be the actual definition of
attention.

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
