# RISC-V Custom `attn` Instruction — Automatic Attention Fusion

A modified `riscv-gnu-toolchain` that automatically recognizes scaled
dot-product attention (SDPA) loop patterns in standard C code and replaces
them with a single custom RISC-V instruction using a GCC middle-end pass.

```
Attention(Q, K, V) = softmax(Q × Kᵀ / √d) × V
```

One instruction encodes the full operation — matrix multiply, scale,
softmax, and the final matrix multiply.

---

## Instruction Encoding

| Field   | Value              |
|---------|--------------------|
| Format  | R4-type            |
| Opcode  | `0x0b` (custom-0)  |
| funct3  | `0x0`              |
| funct2  | `0x00`             |
| MATCH   | `0x0000000b`       |
| MASK    | `0x0600707f`       |

**Operands:**
- `rd`  — pointer to O (output matrix)
- `rs1` — pointer to Q (query matrix)
- `rs2` — pointer to K (key matrix)
- `rs3` — pointer to V (value matrix)

---

## How It Works

The GCC middle-end pass `attnrec` (registered at pass #179, after Graphite)
scans every loop nest in the compiled function. It accepts a loop as an
attention candidate when all four checks pass:

1. Inner loop contains a multiply-add reduction (dot product)
2. Function contains `expf` calls and a division (softmax signature)
3. Three distinct load base pointers exist (Q, K, V)
4. One store base pointer exists (O)

When the pattern matches, the pass emits `IFN_RISCV_ATTN` at the GIMPLE
level, which the RISC-V backend lowers to the `attn` instruction via the
`define_insn` in `riscv.md`.

---

## Setup

### Step 1 — Clone the original toolchain

```bash
git clone https://github.com/riscv-collab/riscv-gnu-toolchain.git
cd riscv-gnu-toolchain
git submodule update --init gcc binutils newlib
```

### Step 2 — Overwrite gcc and binutils with modified versions

```bash
git clone https://github.com/Yash-Awasthi/riscv-attn.git
cp -r riscv-attn/gcc/* riscv-gnu-toolchain/gcc/
cp -r riscv-attn/binutils/* riscv-gnu-toolchain/binutils/
cp riscv-attn/finale.c riscv-gnu-toolchain/
```

### Step 3 — Install prerequisites

```bash
sudo apt-get install autoconf automake autotools-dev curl python3 \
  libmpc-dev libmpfr-dev libgmp-dev gawk build-essential bison flex \
  texinfo gperf libtool patchutils bc zlib1g-dev libexpat-dev ninja-build \
  git cmake libglib2.0-dev libslirp-dev libncurses-dev
```

### Step 4 — Build

```bash
cd riscv-gnu-toolchain
mkdir -p $HOME/riscv-install
./configure --prefix=$HOME/riscv-install \
            --with-arch=rv64gc --with-abi=lp64d \
            --disable-gdb
make -j$(nproc)
```

Build time: 45–90 minutes depending on your machine.

### Step 5 — Test

```bash
$HOME/riscv-install/bin/riscv64-unknown-elf-gcc \
    -mattn -O2 \
    -fno-schedule-insns -fno-schedule-insns2 \
    -S finale.c -o finale.s

grep 'attn' finale.s
```

Expected output:
```
      attn    a3,a0,a1,a2
```

---

## What Was Modified

| File | Purpose |
|---|---|
| `gcc/gcc/tree-ssa-attn.cc` | GCC pass — attnrec — SDPA pattern matcher (~500 lines) |
| `gcc/gcc/config/riscv/riscv.md` | R4-type `define_insn` for `attn` |
| `gcc/gcc/config/riscv/riscv.opt` | `-mattn` compiler flag |
| `gcc/gcc/internal-fn.def` | `IFN_RISCV_ATTN` declaration |
| `gcc/gcc/internal-fn.cc` | IFN expander — lowers to RTL |
| `gcc/gcc/passes.def` | Pass registration after Graphite `POP_INSERT_PASSES` |
| `gcc/gcc/tree-pass.h` | Pass factory declaration |
| `gcc/gcc/Makefile.in` | Build integration |
| `binutils/include/opcode/riscv-opc.h` | `MATCH_ATTN` / `MASK_ATTN` / `DECLARE_INSN` |
| `binutils/opcodes/riscv-opc.c` | Opcode table entry (R4-type, format `d,s,t,r`) |

---

## Why the Loop Body Remains in Assembly

The compiler emits `attn` but retains the original loop body because it
cannot prove the custom hardware instruction produces a correct result
without a hardware model. GCC matched the pattern — it did not prove
semantic equivalence.

Full dead-code elimination requires:
- Implementing `attn` semantics in the Spike RISC-V simulator (`Phase 4`)
- Running both versions on identical inputs and confirming output match

This is standard hardware-software co-design practice.

---

## Documentation

See `docs/` for detailed reference:

| File | Contents |
|---|---|
| `docs/DOCUMENTATION.md` | Full explanation of every component |
| `docs/BUILD_GUIDE.md` | Step-by-step build guide |
| `docs/OPCODE_FIELDS.md` | Encoding reference and bit-level diagram |
| `docs/MANUAL_PATCHES.md` | Exact changes made to each file |
| `docs/KNOWN_ISSUES.md` | Build errors encountered and fixes applied |
| `docs/GENERIC_TEMPLATE.md` | Template for adding other custom instructions |

---

## Project Structure

```
riscv-attn/
├── README.md
├── finale.c                    — test input: fused SDPA in C
├── gcc/
│   └── gcc/
│       ├── tree-ssa-attn.cc    — attnrec pass (pattern matcher + IFN emitter)
│       ├── internal-fn.def     — IFN_RISCV_ATTN declaration
│       ├── internal-fn.cc      — IFN expander
│       ├── passes.def          — pass registration
│       ├── tree-pass.h         — pass factory declaration
│       ├── Makefile.in         — build integration
│       └── config/riscv/
│           ├── riscv.md        — define_insn for attn (R4-type)
│           ├── riscv.opt       — -mattn flag
│           └── riscv.cc        — option validation warning
└── binutils/
    ├── include/opcode/
    │   └── riscv-opc.h         — MATCH_ATTN, MASK_ATTN, DECLARE_INSN
    └── opcodes/
        └── riscv-opc.c         — opcode table entry
```
