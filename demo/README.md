# `demo/` — Reference `attn` demonstration

This directory holds the canonical, end-to-end demonstration of the
modified toolchain. Every artefact here exists to *show that the
pipeline runs* on a real, non-trivial piece of C code — and, in the
[`failures/`](./failures/) sub-directory, to show the matching set
of near-attention C programs that the pass deliberately rejects.

The shortest path from a fresh build to a verified `attn`
instruction is:

1. Build the toolchain — see
   [`../docs/03-build-and-run.md`](../docs/03-build-and-run.md) or
   the recipe in the top-level [`../README.md`](../README.md).
2. Run [`./verify_attn.sh`](./verify_attn.sh) on
   [`./sdpa_test.c`](./sdpa_test.c).
3. Compare your output against the committed
   [`./sdpa_test.s`](./sdpa_test.s) and
   [`./sdpa_test.c.179t.attnrec`](./sdpa_test.c.179t.attnrec).
4. Sweep the negative tests in
   [`./failures/`](./failures/) — every file there must compile
   *without* an `attn` instruction in the output.

If the three layers (assembler, GIMPLE pass, RTL backend) all agree
on `sdpa_test.c` and every `fail_*.c` file is correctly rejected,
the installation is functioning.

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

`verify_attn.sh` exercises every public surface of the modification
in sequence — compiler version, source presence, `-mattn` flag
acceptance, GAS encoding round-trip, pass registration, gate
behaviour with/without `-mattn`, mnemonic in the final assembly,
GIMPLE-dump inspection, a negative test on a non-attention loop, and
a baseline vs. `-mattn` size comparison — then prints a final
`PASS` / `FAIL` count. Any non-zero exit status means at least one
layer is broken.

### Sweep the negative tests in `failures/`

```bash
GCC=$HOME/riscv-install/bin/riscv64-unknown-elf-gcc
for f in demo/failures/fail_*.c; do
    $GCC -mattn -O2 -fno-schedule-insns -fno-schedule-insns2 \
         -S "$f" -o /tmp/out.s 2>/dev/null
    if grep -q '\battn\b' /tmp/out.s; then
        echo "FAIL  $f  — attn emitted unexpectedly"
    else
        echo "OK    $f  — correctly rejected"
    fi
done
```

Every file in [`failures/`](./failures/) must compile *without* an
`attn` instruction in the output. See
[`failures/README.md`](./failures/README.md) for which reject path
in `attn_match` each file exercises.

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
| [`sdpa_test.c`](./sdpa_test.c) | C source | Fused-loop C implementation of scaled dot-product attention. With `-mattn -O2` the compiler folds the full loop nest into a single `attn a3, a0, a1, a2`. This is the canonical *positive* test — every check in the project ultimately reduces to compiling this file. |
| [`sdpa_test.s`](./sdpa_test.s) | Assembly | Reference `-S` output produced by the modified `riscv64-unknown-elf-gcc -mattn -O2`. Use it to confirm a local build emits the same mnemonic at the same point in the prologue. |
| [`sdpa_test.c.179t.attnrec`](./sdpa_test.c.179t.attnrec) | GIMPLE dump | Snapshot of the GIMPLE IR taken immediately after the `attnrec` pass (#179) ran. Shows the `.RISCV_ATTN (...)` call site that replaced the loop body, and — because the pass does not yet delete the original loops — why both forms coexist in the output. Cross-referenced in [`../docs/02-compiler-pass.md` §7](../docs/02-compiler-pass.md#7-why-the-loop-body-stays-and-what-removing-it-would-take). |
| [`verify_attn.sh`](./verify_attn.sh) | Bash script | End-to-end verification harness: compiler version, source presence, GAS round-trip, positive/negative compiler tests, GIMPLE-dump inspection, baseline vs `-mattn` size comparison. Returns non-zero on any failed check. |
| [`failures/`](./failures/) | Sub-directory | Seven near-attention `.c` files that the matcher must reject for one specific reason each (missing loops, no `expf`, fewer than three load bases, unfused phases, unknown trip count, …). Negative-test sentinels against future false positives. See its own [`README.md`](./failures/README.md). |

---

## How this demo relates to the rest of the project

* The pass that recognises `sdpa_test.c` is implemented in
  [`../gcc/gcc/tree-ssa-attn.cc`](../gcc/gcc/tree-ssa-attn.cc) and
  walked through narratively in
  [`../docs/02-compiler-pass.md`](../docs/02-compiler-pass.md). The
  reject paths exercised by every file under [`failures/`](./failures/)
  map one-to-one onto early-return statements in that source.
* The instruction encoding is enumerated in
  [`../docs/01-instruction-spec.md`](../docs/01-instruction-spec.md);
  every numeric value there must agree with what
  [`./sdpa_test.s`](./sdpa_test.s) actually contains.
* The [`scripts/`](../scripts/) directory is a *generalisation* of
  this demo: it produces the analogue of every file in this
  directory for a *different* custom instruction. When adding your
  own mnemonic, treat the artefacts here as the shape of "done".

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
