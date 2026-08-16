# 09 — `demo/` walkthrough

This page is the documentation-set view of the [`demo/`](../demo/)
directory. It enumerates every file under `demo/` and `demo/failures/`,
explains the role each one plays in the verification story, and
shows the exact command lines a reader would type to reproduce each
artefact locally.

For the short, command-first version of the same content, see
[`../demo/README.md`](../demo/README.md) and
[`../demo/failures/README.md`](../demo/failures/README.md). This
file is the long-form companion: it does not assume you have already
read those.

---

## 1. What `demo/` is for

`demo/` is the end-to-end reference for the `attn` instruction. It
serves three independent purposes:

1. **Reproducibility** — anyone with a freshly built toolchain can
   re-compile [`sdpa_test.c`](../demo/sdpa_test.c) and compare the
   output against the committed
   [`sdpa_test.s`](../demo/sdpa_test.s) and
   [`sdpa_test.c.179t.attnrec`](../demo/sdpa_test.c.179t.attnrec).
2. **Acceptance test** — [`verify_attn.sh`](../demo/verify_attn.sh)
   exercises every public surface of the modification (assembler,
   pass gate, GIMPLE dump, RTL backend) and prints a single
   PASS / FAIL summary.
3. **Regression sentinels** — nine of the ten files under
   [`failures/`](../demo/failures/) are near-attention programs
   that must *not* trigger the matcher, locking down its false
   positive surface. The tenth is a documented exception: a known,
   currently-unfixed false positive kept in the corpus on purpose
   as a sentinel for the fix (§3.2 below).

The relationship to the other top-level directories:

* [`gcc/`](../gcc/) and [`binutils/`](../binutils/) contain the
  modified toolchain that makes `demo/` work.
* [`scripts/`](../scripts/) is the generic pipeline that lets you
  reproduce the *shape* of `demo/` for a different custom
  instruction.
* [`docs/`](.) — you are here — is the prose explanation of how the
  pieces fit together.

---

## 2. Files in `demo/`

`demo/` now demonstrates two independent paths to the same `attn`
instruction: the explicit builtin (`attn.h`, `sdpa_builtin.c` —
primary, non-experimental, gated by plain `-mattn`) and the
idiom-recognizing `attnrec` pass (`sdpa_test.c` — experimental, off
by default, needs `-mattn -mattn-recognize`).

### 2.1 `attn.h` and `sdpa_builtin.c` — the explicit builtin path

Path: [`../demo/attn.h`](../demo/attn.h),
[`../demo/sdpa_builtin.c`](../demo/sdpa_builtin.c).

`attn.h` defines the three register-block structs the instruction's
ABI requires (`attn_ptrs`, `attn_dims`, `attn_cfg` — normatively
specified in
[§4 of `01-instruction-spec.md`](01-instruction-spec.md#4-operand-convention-and-abi))
and an inline `attn_sdpa()` that fills them and calls
`__builtin_riscv_attn` directly. On a non-`__riscv` host the same
function falls back to a portable triple-loop reference
implementation, so the header compiles and runs anywhere.
`sdpa_builtin.c` is a short caller that states the operation
directly instead of relying on `attnrec` to recognise a hand-fused
loop shape.

```bash
$HOME/riscv-install/bin/riscv64-unknown-elf-gcc \
    -mattn -O2 \
    -S demo/sdpa_builtin.c -o /tmp/sdpa_builtin.s
grep -n '\battn\b' /tmp/sdpa_builtin.s
```

This is the path `verify_attn.sh` (§2.4) treats as primary, and the
one to prefer: it has no pattern-matching soundness question,
because the programmer states the four operands directly.

### 2.2 `sdpa_test.c` — the idiom-recognition path

Path: [`../demo/sdpa_test.c`](../demo/sdpa_test.c).

A fused-loop C implementation of scaled dot-product attention. The
body is written so it satisfies all eight passing conditions
documented in
[`../demo/failures/README.md`](../demo/failures/README.md#the-passing-condition-mirror-image-of-the-above) —
in particular it has a three-base load nest (Q, K, V), an inner
multiply-add reduction, an `expf` call, an `RDIV_EXPR`, and a
statically known trip count.

When compiled with the modified toolchain at `-O2 -mattn
-mattn-recognize`, the entire nest collapses into a single

```asm
attn    a3, a0, a1, a2
```

instruction. `-mattn-recognize` is required in addition to `-mattn`:
with `-mattn` alone the instruction and builtin are available but
the recognizer pass does not run, and the file compiles to the
ordinary loop nest.

```bash
$HOME/riscv-install/bin/riscv64-unknown-elf-gcc \
    -mattn -mattn-recognize -O2 \
    -S demo/sdpa_test.c -o /tmp/sdpa_test.s
```

**Known limitation.** The call this path emits passes the raw `O`,
`Q`, `K`, `V` pointers directly, not the block ABI `attn.h` builds —
it does not conform to §4 of `01-instruction-spec.md`. This is
called out in `tree-ssa-attn.cc`'s own file header and in
[`02-compiler-pass.md`](02-compiler-pass.md), not hidden.

### 2.3 `sdpa_test.s`

Path: [`../demo/sdpa_test.s`](../demo/sdpa_test.s).

The reference `-S` output produced by the modified
`riscv64-unknown-elf-gcc -mattn -mattn-recognize -O2` on
`sdpa_test.c`. Use it as the ground truth when comparing a local
rebuild:

```bash
diff -u demo/sdpa_test.s /tmp/sdpa_test.s | head -60
```

Modulo line numbers and temp register choice the structure should
match byte-for-byte. The `attn` line should appear in the same
position relative to the prologue.

### 2.4 `sdpa_test.c.179t.attnrec`

Path:
[`../demo/sdpa_test.c.179t.attnrec`](../demo/sdpa_test.c.179t.attnrec).

A committed snapshot of the GIMPLE IR taken immediately after the
`attnrec` pass (pass #179, run after Graphite) executed. It shows
the inserted call to `__builtin_riscv_attn`, the five gate checks
the pass logged on its way to recognising the loop, and why both the
original loop body and the call coexist in the same dump (the pass
does not yet remove the original loops — see
[`02-compiler-pass.md` §7](02-compiler-pass.md#7-why-the-loop-body-stays-and-what-removing-it-would-take)).

Reproduce on your own machine with:

```bash
$HOME/riscv-install/bin/riscv64-unknown-elf-gcc \
    -mattn -mattn-recognize -O2 -fdump-tree-attnrec-details \
    -c demo/sdpa_test.c -o /tmp/sdpa_test.o
ls sdpa_test.c.*attnrec*    # the dump appears next to the .c file
```

### 2.5 `verify_attn.sh`

Path: [`../demo/verify_attn.sh`](../demo/verify_attn.sh).

The end-to-end verification harness. Defaults to `sdpa_builtin.c`
when run with no argument; invoked explicitly as:

```bash
chmod +x demo/verify_attn.sh
./demo/verify_attn.sh demo/sdpa_builtin.c
```

It performs ten checks in sequence:

1. compiler is the modified `riscv64-unknown-elf-gcc`, version
   15.2.0;
2. the source file is present and readable;
3. `-mattn` is accepted on the command line;
4. GAS assembles `attn a3, a0, a1, a2` back to the expected
   encoding (`0x60b5068b`);
5. `__builtin_riscv_attn` emits `attn` under plain `-mattn` alone
   (the primary path, `sdpa_builtin.c`);
6. calling the builtin without `-mattn` is a compile-time error;
7. with **both** `-mattn` and `-mattn-recognize`, the `attnrec`
   pass fires on `sdpa_test.c` and `attn` appears in the `-S`
   output;
8. the GIMPLE dump for that run contains the recognizer's
   replaced-loop record;
9. `-mattn` alone (no `-mattn-recognize`) does **not** transform
   `sdpa_test.c` — zero `attn` emitted — and `-mattn
   -mattn-recognize` together does;
10. baseline vs `-mattn -mattn-recognize` line/branch-count
    comparison on `sdpa_test.c`.

Each check prints `PASS` or `FAIL` and the script exits non-zero if
any check fails. This is the single command a maintainer runs after
any change to the pass or the builtin wiring.

### 2.6 The `failures/` sub-directory

Path: [`../demo/failures/`](../demo/failures/).

The negative-test sentinels. Covered in detail in
[§ 3](#3-files-in-demofailures) below.

---

## 3. Files in `demo/failures/`

Ten `fail_*.c` files live in this directory. Eight map to exactly
one early-return statement in
[`../gcc/gcc/tree-ssa-attn.cc`](../gcc/gcc/tree-ssa-attn.cc), covering
every reject path in `attn_match` and `pass_recognize_attn::execute`.
A ninth, `fail_matmul-then-rownormalize.c`, is a realistic
non-attention kernel (matmul + row-sum normalize) that shares most
of the matcher's surface features and fails the same softmax check
as `fail_noexpf.c`. The tenth,
`fail_scattered-signature-known-false-positive.c`, is not a reject
case at all — it is a documented, currently-unfixed false positive
(see §3.3). The single `README.md` in the directory contains the
same cross-reference table, plus the false-positive writeup.

These files guard the experimental `-mattn-recognize` recognizer
only. They say nothing about `__builtin_riscv_attn` under plain
`-mattn` (§2.1): the builtin takes its arguments literally at the
call site, so there is no pattern for a matcher to guess wrong.

### 3.1 The eight verified reject paths

| File | Reject cause | Reject statement |
|------|--------------|------------------|
| [`fail_loop-missing.c`](../demo/failures/fail_loop-missing.c) | Body is hand-unrolled — no loops. | `execute()`: `if (number_of_loops (fun) <= 1) return 0;` |
| [`fail_main-not-nested.c`](../demo/failures/fail_main-not-nested.c) | Single flat loop in `main`, no inner nest. | `attn_match`: `if (!outer->inner) return false;` |
| [`fail_no-madd-reduction.c`](../demo/failures/fail_no-madd-reduction.c) | Inner reduction is `fabsf(a-b)` not `a*b`. | `attn_match`: `attn_find_madd_reduction` returns `NULL`. |
| [`fail_extra-op-in-inner-loop.c`](../demo/failures/fail_extra-op-in-inner-loop.c) | Extra `+ bias[d]` poisons the madd PHI shape. | Same as above — PHI back-edge is `PLUS(MULT, …)` not `PLUS(MULT)`. |
| [`fail_noexpf.c`](../demo/failures/fail_noexpf.c) | Softmax replaced with ReLU + reciprocal — no `expf`, no `RDIV_EXPR`. | `attn_match`: `if (!attn_has_softmax_and_scale (outer)) return false;` |
| [`fail_only-two-bases.c`](../demo/failures/fail_only-two-bases.c) | V aliased to K → only two distinct load bases. | `attn_match`: `if (load_bases.length () < 3 || !store_base) return false;` |
| [`fail_unfused-three-toplevel-loops.c`](../demo/failures/fail_unfused-three-toplevel-loops.c) | QK^T, softmax, SV are three sibling top-level nests. | Same as above — every per-loop attempt sees fewer than three bases. |
| [`fail_unknown-trip-count.c`](../demo/failures/fail_unknown-trip-count.c) | Outer bound is `volatile` plus data-dependent `break`. | `attn_match`: `if (n == chrec_dont_know) return false;` |
| [`fail_matmul-then-rownormalize.c`](../demo/failures/fail_matmul-then-rownormalize.c) | Ordinary matmul plus row-sum normalization: has the madd reduction, three load bases, a store, and a division, but no `expf` anywhere. | Same as `fail_noexpf.c` — `attn_match`: `if (!attn_has_softmax_and_scale (outer)) return false;` |

### 3.2 The known false positive:  `fail_scattered-signature-known-false-positive.c`

This file is deliberately built to satisfy all eight matching
conditions — an FIR filter, two unrelated elementwise loops, and an
unrelated `expf`/division gain calculation, all in one function —
without computing anything resembling attention. A hand trace
against `attn_match` (see the file's own top comment) finds no
failing check: as the matcher stands today, `attn` is expected to be
emitted for this file, the opposite of every other file in this
directory. It is the concrete instantiation of the correctness
hazard `plan.md` names as the recognizer's open, top-priority issue,
kept in the corpus as a regression sentinel rather than fixed by
narrowing the test. See
[`../demo/failures/README.md`](../demo/failures/README.md#known-false-positive-fail_scattered-signature-known-false-positivec)
for the full writeup.

### 3.3 How to sweep them in one shot

```bash
GCC=$HOME/riscv-install/bin/riscv64-unknown-elf-gcc

for f in demo/failures/fail_*.c; do
    $GCC -mattn -mattn-recognize -O2 \
         -S "$f" -o /tmp/out.s 2>/dev/null
    if grep -q '\battn\b' /tmp/out.s; then
        echo "FAIL  $f  — attn emitted unexpectedly"
    else
        echo "OK    $f  — correctly rejected"
    fi
done
```

Nine of the ten files must compile *without* an `attn` instruction
in the output. `fail_scattered-signature-known-false-positive.c` is
the documented exception (§3.2) and is expected to print `FAIL`
until the matcher is fixed — that single `FAIL` does not mean the
corpus is broken. `-mattn-recognize` must be passed alongside
`-mattn`; without it the recognizer's gate never opens and every
file prints `OK` regardless of its actual shape, defeating the point
of the sweep.

### 3.4 The `failures/README.md`

Path:
[`../demo/failures/README.md`](../demo/failures/README.md).

Contains the table reproduced above, the sweep loop, the
false-positive writeup, the mirror-image list of the eight matching
conditions, and the convention for adding a new failure case (file
name, top-of-file comment shape, one deliberate deviation per file).

---

## 4. Cross-references with the rest of `docs/`

* The pass that recognises `sdpa_test.c` is described narratively
  in [`02-compiler-pass.md`](02-compiler-pass.md). Every reject
  path in [§ 3.1](#31-the-eight-verified-reject-paths) maps to a
  specific early-return statement walked through there.
* The instruction encoding emitted in `sdpa_test.s` is specified
  in [`01-instruction-spec.md`](01-instruction-spec.md). Every
  numeric value there must agree with what `sdpa_test.s` actually
  contains.
* The build recipe used by `verify_attn.sh` is the same one
  documented in [`03-build-and-run.md`](03-build-and-run.md).
* If `verify_attn.sh` reports `FAIL` on any step, the root cause
  is almost certainly already catalogued in
  [`05-troubleshooting.md`](05-troubleshooting.md).
* If you want to reproduce the *shape* of `demo/` for a different
  custom instruction, [`06-extending-toolchain.md`](06-extending-toolchain.md)
  is the recipe and [`10-scripts-pipeline.md`](10-scripts-pipeline.md)
  documents the driver that executes it.

---

## 5. Coverage check

The following table shows that every file in `demo/` and
`demo/failures/` is referenced at least once in this document.

| Path | Referenced in this file |
|------|-------------------------|
| `demo/README.md` | § 1, § 2 (preamble) |
| `demo/attn.h` | § 2.1 |
| `demo/sdpa_builtin.c` | § 2.1 |
| `demo/sdpa_test.c` | § 2.2 |
| `demo/sdpa_test.s` | § 2.3 |
| `demo/sdpa_test.c.179t.attnrec` | § 2.4 |
| `demo/verify_attn.sh` | § 2.5 |
| `demo/failures/README.md` | § 2.6, § 3.4 |
| `demo/failures/fail_loop-missing.c` | § 3.1 |
| `demo/failures/fail_main-not-nested.c` | § 3.1 |
| `demo/failures/fail_no-madd-reduction.c` | § 3.1 |
| `demo/failures/fail_extra-op-in-inner-loop.c` | § 3.1 |
| `demo/failures/fail_noexpf.c` | § 3.1 |
| `demo/failures/fail_only-two-bases.c` | § 3.1 |
| `demo/failures/fail_unfused-three-toplevel-loops.c` | § 3.1 |
| `demo/failures/fail_unknown-trip-count.c` | § 3.1 |
| `demo/failures/fail_matmul-then-rownormalize.c` | § 3.1 |
| `demo/failures/fail_scattered-signature-known-false-positive.c` | § 3.2 |
