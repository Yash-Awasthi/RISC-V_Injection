# 03 — Building the Toolchain and Running the Verification Suite

> **Audience.** Anyone who wants to reproduce the build from a clean
> checkout, plus the same person ten minutes later who wants to know
> how to confirm that each layer of the modification is functioning.
>
> If something fails during the build, jump to
> [`05_TROUBLESHOOTING_new.md`](05_TROUBLESHOOTING_new.md) and find
> the symptom; nine times out of ten it is one of the eleven issues
> documented there.

---

## Table of contents

1. [Prerequisites](#1-prerequisites)
2. [Source layout and getting the code](#2-source-layout-and-getting-the-code)
3. [Configuring the build](#3-configuring-the-build)
4. [Building (45–90 minutes)](#4-building-4590-minutes)
5. [Layer-1 verification — the assembler accepts `attn`](#5-layer-1-verification--the-assembler-accepts-attn)
6. [Layer-2 verification — the compiler emits `attn` automatically](#6-layer-2-verification--the-compiler-emits-attn-automatically)
7. [Negative test — `attn` is *not* emitted without `-mattn`](#7-negative-test--attn-is-not-emitted-without--mattn)
8. [Inspecting the GIMPLE dump](#8-inspecting-the-gimple-dump)
9. [Incremental rebuilds while developing](#9-incremental-rebuilds-while-developing)
10. [Test matrix — what should pass](#10-test-matrix--what-should-pass)

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

# Copy the SDPA test program if you have one (e.g. finale.c)
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
[`05_TROUBLESHOOTING_new.md` Issues 1–2](05_TROUBLESHOOTING_new.md).

---

## 6. Layer-2 verification — the compiler emits `attn` automatically

Now the more interesting test: does the modified GCC, when compiling
plain C with `-mattn -O2`, *automatically* emit our instruction?

The repository ships a test source `finale.c` containing a fused
SDPA implementation (one outer `i`-loop wrapping all four phases —
the form the matcher recognises). Compile it to assembly:

```bash
$HOME/riscv-install/bin/riscv64-unknown-elf-gcc \
    -mattn -O2 \
    -fno-schedule-insns -fno-schedule-insns2 \
    -S finale.c -o finale.s
```

Search for the instruction:

```bash
grep -n '\battn\b' finale.s && echo "PASS — attn emitted" || echo "FAIL"
```

Expected:

```
88:        attn    a3,a0,a1,a2
PASS — attn emitted
```

Why the `-fno-schedule-insns` flags? Until the modified
`riscv.md` has propagated through every stage of the cross-build,
the RTL scheduler may still be using a stale opinion about the
instruction's `type` attribute and assert. The two flags disable
both rounds of insn scheduling for the C compile and are *belt
and braces* — once you have rebuilt with `type "ghost"` set, you
should be able to remove them. They are kept in the documented
recipe because they make the verification reproducible across
partial rebuilds.

---

## 7. Negative test — `attn` is *not* emitted without `-mattn`

It is just as important to confirm that **omitting** `-mattn`
suppresses the instruction. This is a direct check of the gate:

```bash
$HOME/riscv-install/bin/riscv64-unknown-elf-gcc \
    -O2 -S finale.c -o finale_no_mattn.s

if grep -q '\battn\b' finale_no_mattn.s ; then
    echo "FAIL — attn emitted despite no -mattn (gate broken)"
else
    echo "GATE OK — attn correctly suppressed"
fi
```

Expected: `GATE OK`.

If this fails, the gate condition in
`pass_recognize_attn::gate()` is wrong, or `TARGET_ATTN` is being
forced on by some other code path. See
[§4 of `02_COMPILER_PASS_new.md`](02_COMPILER_PASS_new.md#4-the-pass-class--boilerplate)
for what the gate must look like.

---

## 8. Inspecting the GIMPLE dump

GCC's `-fdump-tree-NAME-details` flag emits a textual dump of the
GIMPLE IR after each pass. For our pass:

```bash
$HOME/riscv-install/bin/riscv64-unknown-elf-gcc \
    -mattn -O2 \
    -fdump-tree-attnrec-details \
    -c finale.c -o finale.o

cat finale.c.*attnrec*
```

The dump shows, for each loop the pass examined, which of the five
matching conditions passed or failed and (on success) the
`IFN_RISCV_ATTN` call that was emitted. The format is described in
[§8 of `02_COMPILER_PASS_new.md`](02_COMPILER_PASS_new.md#8-reading-the-gimple-dump).

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
[`06_EXTENDING_TOOLCHAIN_new.md`](06_EXTENDING_TOOLCHAIN_new.md) to
add a new pass source file.

---

## 10. Test matrix — what should pass

When everything is in order, the following table of checks all
return `PASS`. This is a useful CI-style script to keep around.

| # | Check | Command | Expected |
|---|-------|---------|----------|
| 1 | Compiler version | `riscv64-unknown-elf-gcc --version` | `15.2.0`, suffix `dirty` |
| 2 | Hello-world links | `gcc /tmp/t.c -o /tmp/t.elf` | exit 0 |
| 3 | Assembler accepts `attn` | `as attn_asm.S` | exit 0 |
| 4 | objdump prints `attn` | `objdump -d attn_asm.o` | line contains `attn ` |
| 5 | Compiler emits `attn` (positive) | `gcc -mattn -O2 -S finale.c` | `grep '\battn\b' finale.s` matches |
| 6 | Compiler suppresses `attn` (negative) | `gcc -O2 -S finale.c` | `grep '\battn\b'` does *not* match |
| 7 | GIMPLE dump exists | `gcc -mattn -O2 -fdump-tree-attnrec-details -c finale.c` | `finale.c.*attnrec*` file present |
| 8 | Dump records emission | `cat finale.c.*attnrec*` | string `IFN_RISCV_ATTN` appears |

If checks 1–4 pass but 5 fails, the pass is built but its gate or
matching is broken.
If 5 passes but 6 fails, the gate is broken (the pass is firing
unconditionally).
If 7 passes but 8 fails, the pass is being scheduled but rejecting
all loops — read the dump for the reason.

---

**Next:** [`04_PATCHES_AND_FILES_new.md`](04_PATCHES_AND_FILES_new.md) —
the exact list of files modified, the diff applied to each, and the
reasoning behind every edit.
