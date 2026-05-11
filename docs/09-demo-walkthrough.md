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
3. **Regression sentinels** — every file under
   [`failures/`](../demo/failures/) is a near-attention program
   that must *not* trigger the matcher. They lock down the false
   positive surface of the pass.

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

The four files at the top level of `demo/` are the canonical
positive demonstration of the toolchain.

### 2.1 `sdpa_test.c`

Path: [`../demo/sdpa_test.c`](../demo/sdpa_test.c).

A fused-loop C implementation of scaled dot-product attention. The
body is written so it satisfies all eight passing conditions
documented in
[`../demo/failures/README.md`](../demo/failures/README.md#the-passing-condition-mirror-image-of-the-above) —
in particular it has a three-base load nest (Q, K, V), an inner
multiply-add reduction, an `expf` call, an `RDIV_EXPR`, and a
statically known trip count.

When compiled with the modified toolchain at `-O2 -mattn`, the
entire nest collapses into a single

```asm
attn    a3, a0, a1, a2
```

instruction. With `-mattn` omitted the same file compiles to the
ordinary loop nest — the pass gate refuses to run.

Build command:

```bash
$HOME/riscv-install/bin/riscv64-unknown-elf-gcc \
    -mattn -O2 \
    -fno-schedule-insns -fno-schedule-insns2 \
    -S demo/sdpa_test.c -o /tmp/sdpa_test.s
```

### 2.2 `sdpa_test.s`

Path: [`../demo/sdpa_test.s`](../demo/sdpa_test.s).

The reference `-S` output produced by the modified
`riscv64-unknown-elf-gcc -mattn -O2` on `sdpa_test.c`. Use it as
the ground truth when comparing a local rebuild:

```bash
diff -u demo/sdpa_test.s /tmp/sdpa_test.s | head -60
```

Modulo line numbers and temp register choice the structure should
match byte-for-byte. The `attn` line should appear in the same
position relative to the prologue.

### 2.3 `sdpa_test.c.179t.attnrec`

Path:
[`../demo/sdpa_test.c.179t.attnrec`](../demo/sdpa_test.c.179t.attnrec).

A committed snapshot of the GIMPLE IR taken immediately after the
`attnrec` pass (pass #179, run after Graphite) executed. It shows
the inserted `.RISCV_ATTN (...)` call site, the five gate checks the
pass logged on its way to recognising the loop, and why both the
original loop body and the IFN call coexist in the same dump (the
pass does not yet remove the original loops — see
[`02-compiler-pass.md` §7](02-compiler-pass.md#7-why-the-loop-body-stays-and-what-removing-it-would-take)).

Reproduce on your own machine with:

```bash
$HOME/riscv-install/bin/riscv64-unknown-elf-gcc \
    -mattn -O2 -fdump-tree-attnrec-details \
    -fno-schedule-insns -fno-schedule-insns2 \
    -c demo/sdpa_test.c -o /tmp/sdpa_test.o
ls sdpa_test.c.*attnrec*    # the dump appears next to the .c file
```

### 2.4 `verify_attn.sh`

Path: [`../demo/verify_attn.sh`](../demo/verify_attn.sh).

The end-to-end verification harness. Invoked as:

```bash
chmod +x demo/verify_attn.sh
./demo/verify_attn.sh demo/sdpa_test.c
```

It performs ten checks in sequence:

1. compiler is the modified `riscv64-unknown-elf-gcc`;
2. the source file is present and readable;
3. `-mattn` is accepted on the command line;
4. GAS assembles `attn a3, a0, a1, a2` back to the expected
   encoding (`0x0000000b`);
5. the `attnrec` pass is registered at slot #179;
6. with `-mattn` the gate opens and the mnemonic appears in the
   `-S` output;
7. without `-mattn` the gate stays shut and the mnemonic does
   *not* appear;
8. the GIMPLE dump contains `IFN_RISCV_ATTN`;
9. a non-attention loop is not falsely accelerated;
10. with vs without `-mattn` the resulting code is shorter (the
    fused instruction replaces the inner-loop body).

Each check prints `PASS` or `FAIL` and the script exits non-zero if
any check fails. This is the single command a maintainer runs after
any change to the pass.

### 2.5 The `failures/` sub-directory

Path: [`../demo/failures/`](../demo/failures/).

The negative-test sentinels. Covered in detail in
[§ 3](#3-files-in-demofailures) below.

---

## 3. Files in `demo/failures/`

The eight `fail_*.c` files in this directory each map to exactly
one early-return statement in
[`../gcc/gcc/tree-ssa-attn.cc`](../gcc/gcc/tree-ssa-attn.cc).
Together they cover every reject path in `attn_match` and
`pass_recognize_attn::execute`. The single `README.md` in the
directory contains the same cross-reference table.

### 3.1 The eight reject paths

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

### 3.2 How to sweep them in one shot

```bash
GCC=$HOME/riscv-install/bin/riscv64-unknown-elf-gcc

for f in demo/failures/fail_*.c; do
    $GCC -mattn -O2 \
         -fno-schedule-insns -fno-schedule-insns2 \
         -S "$f" -o /tmp/out.s 2>/dev/null
    if grep -q '\battn\b' /tmp/out.s; then
        echo "FAIL  $f  — attn emitted unexpectedly"
    else
        echo "OK    $f  — correctly rejected"
    fi
done
```

Every file must compile *without* an `attn` instruction in the
output. If even one prints `FAIL`, the matcher has become too
permissive and needs tightening.

### 3.3 The `failures/README.md`

Path:
[`../demo/failures/README.md`](../demo/failures/README.md).

Contains the table reproduced above, the sweep loop, the mirror-image
list of the eight passing conditions, and the convention for adding
a new failure case (file name, top-of-file comment shape, one
deliberate deviation per file).

---

## 4. Cross-references with the rest of `docs/`

* The pass that recognises `sdpa_test.c` is described narratively
  in [`02-compiler-pass.md`](02-compiler-pass.md). Every reject
  path in [§ 3.1](#31-the-eight-reject-paths) maps to a specific
  early-return statement walked through there.
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
| `demo/sdpa_test.c` | § 2.1 |
| `demo/sdpa_test.s` | § 2.2 |
| `demo/sdpa_test.c.179t.attnrec` | § 2.3 |
| `demo/verify_attn.sh` | § 2.4 |
| `demo/failures/README.md` | § 2.5, § 3.3 |
| `demo/failures/fail_loop-missing.c` | § 3.1 |
| `demo/failures/fail_main-not-nested.c` | § 3.1 |
| `demo/failures/fail_no-madd-reduction.c` | § 3.1 |
| `demo/failures/fail_extra-op-in-inner-loop.c` | § 3.1 |
| `demo/failures/fail_noexpf.c` | § 3.1 |
| `demo/failures/fail_only-two-bases.c` | § 3.1 |
| `demo/failures/fail_unfused-three-toplevel-loops.c` | § 3.1 |
| `demo/failures/fail_unknown-trip-count.c` | § 3.1 |
