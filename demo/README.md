# `demo/` — Reference `attn` demonstration

This directory contains the canonical, end-to-end demonstration of
the modified toolchain. Every file in this directory is *output*-
oriented: they exist to prove that the modifications described in
[`../docs/`](../docs/) actually behave as advertised on a non-trivial
piece of C code.

If you have never built this project before, the workflow you want
is:

1. Build the toolchain — see [§How to run](#how-to-run-this-demo)
   below or [`../docs/03-build-and-run.md`](../docs/03-build-and-run.md)
   for the long version.
2. Run [`./verify_attn.sh`](./verify_attn.sh) on
   [`./sdpa_test.c`](./sdpa_test.c).
3. Compare your output against the committed
   [`./sdpa_test.s`](./sdpa_test.s) and
   [`./sdpa_test.c.179t.attnrec`](./sdpa_test.c.179t.attnrec).

If all three layers (assembler / GIMPLE pass / RTL backend) agree,
you have a working installation.

---

## How to run this demo

> **Prerequisite.** A built and installed modified toolchain at
> `$HOME/riscv-install` (or wherever `--prefix` points). If you do
> not have this yet, follow the recipe in the top-level
> [`../README.md`](../README.md) under "How to run the program",
> then come back here.

### One-shot: full verification harness

```bash
chmod +x demo/verify_attn.sh
./demo/verify_attn.sh demo/sdpa_test.c
```

`verify_attn.sh` runs every check in sequence (compiler version,
source presence, GAS round-trip, GCC pattern emission, GIMPLE dump
inspection, negative test without `-mattn`) and prints a final
`PASS` / `FAIL` count.

### Manual, step-by-step

```bash
GCC=$HOME/riscv-install/bin/riscv64-unknown-elf-gcc

# 1. Positive test — with -mattn the compiler should emit `attn`.
$GCC -mattn -O2 \
     -fno-schedule-insns -fno-schedule-insns2 \
     -S demo/sdpa_test.c -o /tmp/sdpa_test.s
grep -n '\battn\b' /tmp/sdpa_test.s        # expect: one or more matches

# 2. Negative test — without -mattn the compiler must NOT emit it.
$GCC        -O2 \
     -fno-schedule-insns -fno-schedule-insns2 \
     -S demo/sdpa_test.c -o /tmp/sdpa_test_no_mattn.s
grep -n '\battn\b' /tmp/sdpa_test_no_mattn.s   # expect: no matches

# 3. Inspect the GIMPLE dump that the pass produced.
$GCC -mattn -O2 -fdump-tree-attnrec-details \
     -fno-schedule-insns -fno-schedule-insns2 \
     -c demo/sdpa_test.c -o /tmp/sdpa_test.o
cat sdpa_test.c.179t.attnrec   # generated next to the .c file
```

### Compare against the committed reference output

```bash
# Compare the assembly — modulo line numbers / temp register choice,
# the structure should match.
diff -u demo/sdpa_test.s /tmp/sdpa_test.s | head -60

# The committed GIMPLE dump is a snapshot from one specific build;
# don't expect a byte-for-byte match, but you should see the same
# IFN_RISCV_ATTN call site.
grep -n 'IFN_RISCV_ATTN' demo/sdpa_test.c.179t.attnrec
```

---

## Files in this directory

| File | Type | Purpose |
|------|------|---------|
| [`sdpa_test.c`](./sdpa_test.c) | C source | Plain C implementation of fused scaled dot-product attention. The compiler should fold its loop nest into a single `attn a3, a0, a1, a2`. This is the canonical input to every check that the project performs. |
| [`sdpa_test.s`](./sdpa_test.s) | Assembly | Reference assembly output produced by `riscv64-unknown-elf-gcc -mattn -O2 -S sdpa_test.c`. Use it to confirm your build is producing the expected mnemonic in the expected place. |
| [`sdpa_test.c.179t.attnrec`](./sdpa_test.c.179t.attnrec) | GIMPLE dump | Internal representation snapshot taken just *after* the `attnrec` pass (#179) ran. Useful for understanding what the pass actually replaced and why the surrounding loops are still present (see [`../docs/02-compiler-pass.md` §7](../docs/02-compiler-pass.md#7-why-the-loop-body-stays-and-what-removing-it-would-take)). |
| [`verify_attn.sh`](./verify_attn.sh) | Bash script | End-to-end verification harness: compiler version, source check, GAS encoding round-trip, positive/negative compiler tests, GIMPLE-dump inspection, summary. Returns non-zero on any failed check. |

---

## How this demo relates to the rest of the project

* The pass that recognises `sdpa_test.c` lives at
  [`../gcc/gcc/tree-ssa-attn.cc`](../gcc/gcc/tree-ssa-attn.cc) and
  is documented narratively in
  [`../docs/02-compiler-pass.md`](../docs/02-compiler-pass.md).
* The instruction encoding is enumerated in
  [`../docs/01-instruction-spec.md`](../docs/01-instruction-spec.md);
  every numeric value there must agree with what
  [`./sdpa_test.s`](./sdpa_test.s) actually contains.
* The [`scripts/`](../scripts/) directory is a *generalisation* of
  this demo — it produces the analogue of every file in this
  directory, but for a *different* custom instruction. If you are
  trying to add your own mnemonic, treat the four files here as the
  shape of "done".

---

## Troubleshooting

If `verify_attn.sh` reports `FAIL` on any step, do **not** start
patching things at random. Each failure mode has a known root cause
documented in [`../docs/05-troubleshooting.md`](../docs/05-troubleshooting.md):

* Step 3 fails with `unrecognized opcode 'attn'` → binutils was not
  rebuilt after the opcode-table edit. See Issue 1.
* Step 4 fails (no `attn` in the assembly) → the pass is not running.
  Check the GIMPLE dump from step 3 and see Issues 5–7.
* Step 4 succeeds but the GIMPLE dump is empty → the pass *is*
  registered, but the gate did not pass. Confirm `-mattn -O2` and
  see Issue 8.
