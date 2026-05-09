# Build Guide — RISC-V Custom `attn` Instruction

**Author:** Yash Awasthi
**Toolchain:** riscv-gnu-toolchain (GCC 15.2.0, Binutils 2.46)
**Host:** Ubuntu 24.04 / WSL2

---

## 0. Prerequisites

```bash
sudo apt-get install autoconf automake autotools-dev curl python3 python3-pip \
  python3-tomli libmpc-dev libmpfr-dev libgmp-dev gawk build-essential bison \
  flex texinfo gperf libtool patchutils bc zlib1g-dev libexpat-dev ninja-build \
  git cmake libglib2.0-dev libslirp-dev libncurses-dev
```

Verify `makeinfo` is present (texinfo installs it, not a binary called `texinfo`):
```bash
which makeinfo && makeinfo --version
```

Disk space: at least **12 GB** free.
Build time: ~45–90 minutes depending on CPU cores.

---

## 1. Clone and Initialize

```bash
# Clone original upstream toolchain
git clone https://github.com/riscv-collab/riscv-gnu-toolchain.git
cd riscv-gnu-toolchain

# Initialize only the three submodules needed for bare-metal target
git submodule update --init gcc binutils newlib
```

---

## 2. Overwrite with Modified Sources

```bash
# Clone the modified toolchain
git clone https://github.com/Yash-Awasthi/riscv-attn.git

# Overwrite gcc and binutils with modified versions
cp -r riscv-attn/gcc/* riscv-gnu-toolchain/gcc/
cp -r riscv-attn/binutils/* riscv-gnu-toolchain/binutils/

# Copy test file
cp riscv-attn/finale.c riscv-gnu-toolchain/
```

---

## 3. Configure

```bash
cd riscv-gnu-toolchain
mkdir -p $HOME/riscv-install

./configure \
    --prefix=$HOME/riscv-install \
    --with-arch=rv64gc \
    --with-abi=lp64d \
    --disable-gdb
```

The `--disable-gdb` flag skips GDB submodule which is not needed and
significantly reduces build time.

---

## 4. Build

```bash
make -j$(nproc) 2>&1 | tee build.log
```

Monitor for errors:
```bash
grep -n 'error:' build.log | head -20
```

Success indicator — last lines of build.log will show:
```
make[1]: Leaving directory '.../build-gcc-newlib-stage2'
mkdir -p stamps/ && touch stamps/build-gcc-newlib-stage2
```

---

## 5. Verify Installation

```bash
$HOME/riscv-install/bin/riscv64-unknown-elf-gcc --version
# Expected: riscv64-unknown-elf-gcc (g5115c7e447f-dirty) 15.2.0

echo 'int main(){return 0;}' > /tmp/t.c
$HOME/riscv-install/bin/riscv64-unknown-elf-gcc /tmp/t.c -o /tmp/t.elf \
    && echo "BASELINE OK"
```

---

## 6. Verify Assembler (attn encodes correctly)

```bash
cat > /tmp/attn_asm.S << 'EOF'
    .text
    .globl _start
_start:
    attn x0, a5, a4, a3
EOF

$HOME/riscv-install/bin/riscv64-unknown-elf-as \
    /tmp/attn_asm.S -o /tmp/attn_asm.o

$HOME/riscv-install/bin/riscv64-unknown-elf-objdump -d /tmp/attn_asm.o
```

Expected output:
```
   0:   00e7800b    attn    zero,a5,a4,a3
```

---

## 7. Verify Compiler Pass (attn emitted automatically)

```bash
# Compile finale.c with -mattn flag
$HOME/riscv-install/bin/riscv64-unknown-elf-gcc \
    -mattn -O2 \
    -fno-schedule-insns -fno-schedule-insns2 \
    -S finale.c -o finale.s

# Check the instruction was emitted
grep -n '\battn\b' finale.s && echo "PASS — attn emitted" || echo "FAIL"
```

Expected:
```
88:     attn    a3,a0,a1,a2
PASS — attn emitted
```

---

## 8. Verify Gate (attn NOT emitted without -mattn)

```bash
$HOME/riscv-install/bin/riscv64-unknown-elf-gcc \
    -O2 -S finale.c -o finale_no_mattn.s

grep -c '\battn\b' finale_no_mattn.s \
    && echo "FALSE POSITIVE — bad" \
    || echo "GATE OK — attn correctly suppressed"
```

---

## 9. View GIMPLE Dump (pass internals)

```bash
$HOME/riscv-install/bin/riscv64-unknown-elf-gcc \
    -mattn -O2 \
    -fdump-tree-attnrec-details \
    -c finale.c -o finale.o

cat finale.c.*attnrec*
```

The dump shows which loops were examined, which checks passed or failed,
and the emitted `IFN_RISCV_ATTN` call at the GIMPLE level.

---

## 10. Rebuilding After Source Changes

If you modify `tree-ssa-attn.cc` and want to rebuild only that file:

```bash
# Delete the stale object in both build stages
rm -f $(find ~/riscv-gnu-toolchain -name 'tree-ssa-attn.o')

# Rebuild (make is incremental — only recompiles changed files)
cd ~/riscv-gnu-toolchain
make -j$(nproc) 2>&1 | tee rebuild.log
```

If you modify `riscv.md` (the define_insn):
```bash
rm -f $(find ~/riscv-gnu-toolchain -name '*.o' \
    -newer ~/riscv-gnu-toolchain/gcc/gcc/config/riscv/riscv.md)
make -j$(nproc)
```

---

## 11. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `attn` not recognized by assembler | Missing opcode table entry | Check `riscv-opc.c` has `"d,s,t,r"` format entry |
| `unrecognized opcode 'attn'` | MASK/MATCH mismatch | Verify `MASK_ATTN = 0x0600707f` in `riscv-opc.h` |
| ICE in `scev_initialize` | SCEV called twice | Remove `scev_initialize()` from pass — already active at pass #179 |
| ICE in `propagate_necessity` (DCE) | Broken vdef chain | Use `gimple_set_has_volatile_ops(call, true)` on IFN call |
| ICE in `riscv_sched_variable_issue` | Unknown instruction type | Set `type "ghost"` in `define_insn` |
| `tree-ssa-attn.o: No such file` | Wrong file location | File must be at `gcc/gcc/tree-ssa-attn.cc` not `gcc/tree-ssa-attn.cc` |
| `build_rdg` undeclared | Not a public API | It is a method of `loop_distribution` class — do not use directly |
| `mark_virtual_operands_for_renaming` undeclared | Missing header | Add `#include "tree-into-ssa.h"` |
| `operand_equal_p` undeclared | Missing header | Add `#include "fold-const.h"` before `tree-data-ref.h` |
| Pass fires but attn not in asm | Scheduler crash | Add `-fno-schedule-insns -fno-schedule-insns2` or fix `type` attribute |
