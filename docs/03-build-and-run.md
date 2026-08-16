# 03 — Building the Toolchain and Running the Verification Suite

> **Audience.** Anyone who wants to reproduce the build from a clean
> checkout, plus the same person ten minutes later who wants to know
> how to confirm that each layer of the modification is functioning.
>
> If something fails during the build, jump to
> [`05-troubleshooting.md`](05-troubleshooting.md) and find
> the symptom; nine times out of ten it is one of the eleven issues
> documented there.

> **Verification status of this document.** Every command below was
> written and checked by reading the compiler and assembler sources
> it exercises, not by running them: this environment has no host
> C compiler, no WSL, and no way to build GCC. Three pieces of
> deferred work — the `__builtin_riscv_attn` builtin, the
> `-mattn`/`-mattn-recognize` gate split, and the negative-guard
> sweep in `demo/failures/` — are batched here so that whoever has
> a real Linux box builds all three in **one** `make` and runs
> **one** checklist (§10, plus [`demo/verify_attn.sh`](../demo/verify_attn.sh))
> instead of three separate build/verify cycles. See §11 for where
> to record what that run actually printed.

---

## Table of contents

1. [Prerequisites](#1-prerequisites)
2. [Source layout and getting the code](#2-source-layout-and-getting-the-code)
3. [Configuring the build](#3-configuring-the-build)
4. [Building (45–90 minutes)](#4-building-4590-minutes)
5. [Layer-1 verification — the assembler accepts `attn`](#5-layer-1-verification--the-assembler-accepts-attn)
6. [Layer-2 verification — the builtin emits `attn` (primary path)](#6-layer-2-verification--the-builtin-emits-attn-primary-path)
7. [Gate checks — builtin without `-mattn`, recognizer without `-mattn-recognize`](#7-gate-checks--builtin-without--mattn-recognizer-without--mattn-recognize)
8. [Inspecting the GIMPLE dump](#8-inspecting-the-gimple-dump)
9. [Incremental rebuilds while developing](#9-incremental-rebuilds-while-developing)
10. [Test matrix — what should pass](#10-test-matrix--what-should-pass)
11. [Recording the actual verification run](#11-recording-the-actual-verification-run)

---

## 1. Prerequisites

| Requirement | Tested value |
|-------------|--------------|
| Host OS     | Ubuntu 24.04 LTS, or WSL2 with Ubuntu 24.04 |
| Free disk   | ≥ 12 GiB (the build tree itself is ~7 GiB) |
| RAM         | ≥ 8 GiB recommended (parallel `make` peaks high) |
| Build time  | 45–90 minutes on 8 cores |
| Permissions | sudo for the apt step only |

Install build dependencies:

```bash
sudo apt-get update
sudo apt-get install -y \
    autoconf automake autotools-dev curl python3 python3-pip python3-tomli \
    libmpc-dev libmpfr-dev libgmp-dev gawk build-essential bison flex \
    texinfo gperf libtool patchutils bc zlib1g-dev libexpat-dev ninja-build \
    git cmake libglib2.0-dev libslirp-dev libncurses-dev
```

Sanity-check that `makeinfo` is on the path — `texinfo` installs a
binary named `makeinfo`, not `texinfo`:

```bash
which makeinfo && makeinfo --version
```

If this prints nothing, the configure step in §3 will fail with a
confusing error about Texinfo. Re-install or update `texinfo` until
the check passes.

---

## 2. Source layout and getting the code

The repository (`Yash-Awasthi/CD`) is a *fork* of
`riscv-gnu-toolchain`. It contains a fully populated `binutils/` and
`gcc/` tree alongside the project documentation in `docs/` and the
new `_new` documentation set at the repository root.

There are two equivalent ways to obtain a buildable tree.

### 2.1 Clone the modified fork directly (recommended)

```bash
git clone https://github.com/Yash-Awasthi/CD.git riscv-attn
cd riscv-attn
```

The result is a self-contained `riscv-gnu-toolchain` checkout that
already includes every modification. No further patching is needed.

### 2.2 Clone upstream and overlay the modifications (advanced)

If you would prefer to start from the unmodified upstream and apply
only the project's deltas, the workflow is:

```bash
git clone https://github.com/riscv-collab/riscv-gnu-toolchain.git
cd riscv-gnu-toolchain
git submodule update --init gcc binutils newlib

# Overlay the modified gcc/ and binutils/ subtrees from the fork
git clone https://github.com/Yash-Awasthi/CD.git ../CD
cp -r ../CD/gcc/*       gcc/
cp -r ../CD/binutils/*  binutils/

# Copy the SDPA test program if you have one (e.g. sdpa_test.c)
# It lives in custom_attn/test in the older tree; in the
# Yash-Awasthi/CD repo it sits at the root.
```

This route is useful for studying which exact files differ from
upstream, but for ordinary use the direct clone in 2.1 is simpler.

---

## 3. Configuring the build

Pick an installation prefix. The build will install the cross
toolchain underneath this directory; you will later add its `bin/`
to `PATH`.

```bash
mkdir -p $HOME/riscv-install
cd ~/riscv-attn          # the directory of the cloned tree

./configure \
    --prefix=$HOME/riscv-install \
    --with-arch=rv64gc \
    --with-abi=lp64d \
    --disable-gdb
```

Each flag, briefly:

| flag | effect |
|------|--------|
| `--prefix=$HOME/riscv-install` | where the binaries land |
| `--with-arch=rv64gc` | target ISA: 64-bit base + IMAFD + Compressed |
| `--with-abi=lp64d`   | calling convention: 64-bit longs/pointers, doubles in F regs |
| `--disable-gdb`      | skip building gdb — saves ~10 minutes; not needed for our work |

You can also set `--enable-multilib` if you want both rv32 and rv64
in the same install, but this roughly doubles the build time and is
not needed for `attn` validation.

---

## 4. Building (45–90 minutes)

```bash
make -j$(nproc) 2>&1 | tee build.log
```

You can keep a parallel terminal tailing `build.log` and checking
for compiler errors:

```bash
grep -n 'error:' build.log | head -20
```

A clean build ends with lines that look like:

```
make[1]: Leaving directory '.../build-gcc-newlib-stage2'
mkdir -p stamps/ && touch stamps/build-gcc-newlib-stage2
```

After the build finishes, sanity-check the installed compiler:

```bash
$HOME/riscv-install/bin/riscv64-unknown-elf-gcc --version
# Expected (the dirty suffix marks our local modifications):
# riscv64-unknown-elf-gcc (g5115c7e447f-dirty) 15.2.0

echo 'int main(void){return 0;}' > /tmp/t.c
$HOME/riscv-install/bin/riscv64-unknown-elf-gcc /tmp/t.c -o /tmp/t.elf \
    && echo "BASELINE OK"
```

For convenience, optionally:

```bash
export PATH="$HOME/riscv-install/bin:$PATH"
```

---

## 5. Layer-1 verification — the assembler accepts `attn`

The first thing to confirm is that the encoding work in `binutils/`
took effect: the assembler should accept `attn` as a mnemonic, and
`objdump` should disassemble it back to the same mnemonic.

Create a minimal assembly file:

```bash
cat > /tmp/attn_asm.S << 'EOF'
    .text
    .globl _start
_start:
    attn  x0, a5, a4, a3
EOF
```

Assemble and disassemble it:

```bash
$HOME/riscv-install/bin/riscv64-unknown-elf-as \
    /tmp/attn_asm.S -o /tmp/attn_asm.o

$HOME/riscv-install/bin/riscv64-unknown-elf-objdump -d /tmp/attn_asm.o
```

Expected output (the leading hex is the encoded instruction word):

```
   0:   68e7800b    attn    zero,a5,a4,a3
```

Verification of the encoded word:
`rd = zero (x0 = 00000)`, `funct3 = 000`, `rs1 = a5 (x15 = 01111)`,
`rs2 = a4 (x14 = 01110)`, `funct2 = 00`, `rs3 = a3 (x13 = 01101)`,
`opcode = 0001011`, giving `0x68e7800b`. The exact hex printed by
your `objdump` may differ very slightly between binutils builds
(register-number bits identical; sub-field bits identical); the
critical observation is that the mnemonic decodes as `attn`.

Two things to verify in this output:

* the **mnemonic** is `attn` (not `.insn` or a raw hex word — those
  would indicate that `binutils/opcodes/riscv-opc.c` is missing the
  table entry);
* the **register fields** decode as expected (here `rd = zero`,
  `rs1 = a5`, `rs2 = a4`, `rs3 = a3`).

If either is wrong, see
[`05-troubleshooting.md` Issues 1–2](05-troubleshooting.md).

---

## 6. Layer-2 verification — the builtin emits `attn` (primary path)

The primary, non-experimental way to get `attn` out of the compiler
is the explicit builtin: `__builtin_riscv_attn(rd, rs1, rs2, rs3)`,
declared in `gcc/gcc/config/riscv/riscv-builtins.cc` and gated on
plain `-mattn`. The repository ships `demo/sdpa_builtin.c`, which
wraps it in a portable `attn_sdpa()` helper (`demo/attn.h`) — no
pattern matching is involved, the call is written directly at the
source site. Compile it to assembly:

```bash
$HOME/riscv-install/bin/riscv64-unknown-elf-gcc \
    -mattn -O2 \
    -S sdpa_builtin.c -o sdpa_builtin.s
```

Search for the instruction:

```bash
grep -n '\battn\b' sdpa_builtin.s && echo "PASS — attn emitted" || echo "FAIL"
```

Expected: a line containing `attn` (register allocation of the four
operands may vary with the surrounding code).

No scheduler-disabling flags are needed here. `riscv_attn` carries
`type "ghost"` in `riscv.md`, so `riscv_sched_variable_issue` has a
real answer for it and both scheduling passes run normally. See
[Issue 7](05-troubleshooting.md#issue-7--ice-in-riscv_sched_variable_issue)
for the ICE this used to trigger.

The repository also ships `sdpa_test.c`, a hand-fused loop nest that
the *experimental* idiom recognizer can be made to rewrite into the
same instruction — see §7 below and
[`02-compiler-pass.md`](02-compiler-pass.md) for why that path is
opt-in and separately gated.

---

## 7. Gate checks — builtin without `-mattn`, recognizer without `-mattn-recognize`

Two independent gates need checking, because two independent
surfaces share the `attn` mnemonic:

### 7.1 The builtin errors without `-mattn`

`__builtin_riscv_attn` is only registered as a known function when
`TARGET_ATTN` is set (`AVAIL (attn, TARGET_ATTN)` in
`riscv-builtins.cc`). Without `-mattn`, GCC does not know the name,
and calling an unrecognised `__builtin_*` identifier is a
compile-time error, not merely a warning:

```bash
$HOME/riscv-install/bin/riscv64-unknown-elf-gcc \
    -O2 -S sdpa_builtin.c -o sdpa_builtin_no_mattn.s
echo "exit: $?"
```

Expected: non-zero exit, with a diagnostic naming
`__builtin_riscv_attn` (typically "implicit declaration of
function"). If this instead compiles cleanly, the availability
predicate in `riscv-builtins.cc` is not doing its job.

### 7.2 `-mattn` alone does not run the recognizer; `-mattn -mattn-recognize` does

This is the split that makes the recognizer opt-in. Compile
`sdpa_test.c` — the file the recognizer is meant to match — first
with `-mattn` alone, then with both flags:

```bash
$HOME/riscv-install/bin/riscv64-unknown-elf-gcc \
    -mattn -O2 -S sdpa_test.c -o sdpa_test_mattn_only.s
grep -c '\battn\b' sdpa_test_mattn_only.s   # expect: 0

$HOME/riscv-install/bin/riscv64-unknown-elf-gcc \
    -mattn -mattn-recognize -O2 -S sdpa_test.c -o sdpa_test_recognize.s
grep -c '\battn\b' sdpa_test_recognize.s    # expect: 1 or more
```

If the first `grep` finds anything, the gate in
`pass_recognize_attn::gate()` is not checking
`TARGET_ATTN_RECOGNIZE` correctly — see
[§4 of `02-compiler-pass.md`](02-compiler-pass.md#4-the-pass-class--boilerplate).
If the second `grep` finds nothing, the matcher itself rejected
`sdpa_test.c`; dump the GIMPLE (§8) to see which check failed.

---

## 8. Inspecting the GIMPLE dump

GCC's `-fdump-tree-NAME-details` flag emits a textual dump of the
GIMPLE IR after each pass. For our pass:

```bash
$HOME/riscv-install/bin/riscv64-unknown-elf-gcc \
    -mattn -mattn-recognize -O2 \
    -fdump-tree-attnrec-details \
    -c sdpa_test.c -o sdpa_test.o

cat sdpa_test.c.*attnrec*
```

The dump shows, for each loop the pass examined, which of the five
matching conditions passed or failed and (on success) the
`__builtin_riscv_attn` call that was emitted. The format is described in
[§8 of `02-compiler-pass.md`](02-compiler-pass.md#8-reading-the-gimple-dump).

This dump is *the* primary debugging tool while iterating on the
pass. Make a habit of producing it whenever you change the matching
logic.

---

## 9. Incremental rebuilds while developing

Full rebuilds take an hour. Incremental rebuilds, when correctly
invoked, take seconds. The two most common scenarios:

### 9.1 You changed `tree-ssa-attn.cc`

```bash
# Force only the changed object to be rebuilt
rm -f $(find ~/riscv-attn -name 'tree-ssa-attn.o')

cd ~/riscv-attn
make -j$(nproc) 2>&1 | tee rebuild.log
```

GCC's build is staged: there is a `build-gcc-newlib-stage1` and a
`build-gcc-newlib-stage2`. The `find` above deletes the stale
object in *both* stages so that `make` recompiles `tree-ssa-attn.cc`
in each, then re-archives `libbackend.a` and re-links the GCC
binary. End-to-end this is typically under a minute.

### 9.2 You changed `riscv.md` (the `define_insn`)

`riscv.md` is consumed by GCC's `genemit`/`genrecog` code-generator
machinery, so a great many object files implicitly depend on it.
The safest invocation:

```bash
rm -f $(find ~/riscv-attn -name '*.o' \
        -newer ~/riscv-attn/gcc/gcc/config/riscv/riscv.md)
make -j$(nproc)
```

This deletes any object file older than the modified `riscv.md`,
forcing the ones that actually depend on it to be regenerated.

### 9.3 You added a brand-new file under `gcc/gcc/`

Adding a file is more disruptive than editing one, because
`gcc/gcc/Makefile.in` has to be regenerated. The recipe:

```bash
# Force the per-target Makefile to regenerate from Makefile.in
find ~/riscv-attn -path '*/gcc/Makefile' -delete
make -j$(nproc)
```

This is what happens when you follow
[`06-extending-toolchain.md`](06-extending-toolchain.md) to
add a new pass source file.

---

## 10. Test matrix — what should pass

When everything is in order, the following table of checks all
return `PASS`. This is a useful CI-style script to keep around — it
is exactly what [`demo/verify_attn.sh`](../demo/verify_attn.sh)
automates as the "one checklist" referred to at the top of this
document.

| # | Check | Command | Expected |
|---|-------|---------|----------|
| 1 | Compiler version | `riscv64-unknown-elf-gcc --version` | `15.2.0`, suffix `dirty` |
| 2 | Hello-world links | `gcc /tmp/t.c -o /tmp/t.elf` | exit 0 |
| 3 | Assembler accepts `attn` | `as attn_asm.S` | exit 0 |
| 4 | objdump prints `attn` | `objdump -d attn_asm.o` | line contains `attn ` |
| 5 | Builtin emits `attn` (primary path) | `gcc -mattn -O2 -S sdpa_builtin.c` | `grep '\battn\b' sdpa_builtin.s` matches |
| 6 | Builtin without `-mattn` errors | `gcc -O2 -S sdpa_builtin.c` | non-zero exit, diagnostic names `__builtin_riscv_attn` |
| 7 | `-mattn` alone does not recognize | `gcc -mattn -O2 -S sdpa_test.c` | `grep -c '\battn\b'` is `0` |
| 8 | `-mattn -mattn-recognize` does recognize | `gcc -mattn -mattn-recognize -O2 -S sdpa_test.c` | `grep -c '\battn\b'` is `>0` |
| 9 | GIMPLE dump exists | `gcc -mattn -mattn-recognize -O2 -fdump-tree-attnrec-details -c sdpa_test.c` | `sdpa_test.c.*attnrec*` file present |
| 10 | Dump records emission | `cat sdpa_test.c.*attnrec*` | string `__builtin_riscv_attn` appears |
| 11 | Nine of ten `demo/failures/fail_*.c` rejected | `gcc -mattn -mattn-recognize -O2 -S fail_*.c` for each of the 10 files | `grep '\battn\b'` does *not* match, for 9 of 10 — the tenth, `fail_scattered-signature-known-false-positive.c`, is a documented, currently-unfixed false positive and is expected to match; see [`../demo/failures/README.md`](../demo/failures/README.md#known-false-positive-fail_scattered-signature-known-false-positivec) |

If checks 1–4 pass but 5 fails, the builtin is not registered
correctly — check `AVAIL (attn, TARGET_ATTN)` and the
`DIRECT_NO_TARGET_BUILTIN (attn, ...)` row in `riscv-builtins.cc`.
If 5 passes but 6 fails, the availability predicate is firing
unconditionally (the builtin is visible even without `-mattn`).
If 7 fails, `pass_recognize_attn::gate()` is still keyed on
`TARGET_ATTN` instead of `TARGET_ATTN_RECOGNIZE` — see
[§4 of `02-compiler-pass.md`](02-compiler-pass.md#4-the-pass-class--boilerplate).
If 8 fails, the recognizer's gate is correct but the matcher itself
rejected `sdpa_test.c`; dump the GIMPLE (check 9/10) for the reason.
If check 11 fails for any file, the matcher has regressed toward a
false positive — see [`demo/failures/README.md`](../demo/failures/README.md)
for which reject path that file is supposed to exercise.

---

## 11. Recording the actual verification run

**Status: not yet run.** Everything above was derived by reading
`gcc/gcc/config/riscv/riscv-builtins.cc`, `riscv.opt`, `riscv.md`,
and `tree-ssa-attn.cc`, and reasoning about what a correct build
does — it has not been executed against a real, built
`riscv64-unknown-elf-gcc` anywhere. This environment has no host C
compiler, no WSL, and no cross toolchain, so a build was never
attempted here.

When someone with a Linux box (see §1) runs the batched build and
the checklist in §10 — most conveniently via
`./demo/verify_attn.sh demo/sdpa_builtin.c` plus the
`demo/failures/` sweep — replace this paragraph with the literal
`PASS`/`FAIL` summary line the script printed, the GCC version
string from check 1, and the date of the run. Do not summarize or
round the result; if something failed, say which check and paste
the diagnostic. A `PASS` claim in this section that nobody actually
observed is worse than an honest "not yet run".

---

**Next:** [`04-patches-and-files.md`](04-patches-and-files.md) —
the exact list of files modified, the diff applied to each, and the
reasoning behind every edit.
