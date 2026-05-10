# 01 — `attn` Instruction Specification

> **Audience.** Anyone who needs an authoritative reference for what
> the `attn` instruction *is*: its binary encoding, its register
> conventions, its arithmetic semantics, and how the toolchain
> exposes it. This is written in the style of a short ISA-extension
> specification, suitable for citation in a paper.

---

## Table of contents

1. [Mnemonic and format summary](#1-mnemonic-and-format-summary)
2. [Bit-field layout](#2-bit-field-layout)
3. [Encoding constants (`MATCH` and `MASK`)](#3-encoding-constants-match-and-mask)
4. [Operand convention and ABI](#4-operand-convention-and-abi)
5. [Bit-level worked example](#5-bit-level-worked-example)
6. [Architectural semantics](#6-architectural-semantics)
7. [Toolchain entries that realise the spec](#7-toolchain-entries-that-realise-the-spec)
8. [The `-mattn` compile-time flag](#8-the--mattn-compile-time-flag)
9. [Quick-reference card](#9-quick-reference-card)

---

## 1. Mnemonic and format summary

| property | value |
|----------|-------|
| Mnemonic | `attn` |
| Format   | **R4-type** (four register operands; same as RISC-V FMADD) |
| Width    | 32 bits |
| Opcode slot | `custom-0` (`opcode[6:0] = 0b0001011 = 0x0b`) |
| `funct3` | `0b000` |
| `funct2` | `0b00` |
| Number of integer-register operands | 4 (`rd`, `rs1`, `rs2`, `rs3`) |
| Required ISA | RV64GC (validated); the encoding is XLEN-agnostic |
| Privilege | unprivileged |

The choice of R4-type rather than R-type is deliberate. The original
proof-of-concept used R-type and packed three matrix pointers into
two stack-allocated descriptor structs (one for dimensions, one for
the Q/K/V/O pointers). That worked, but it required the compiler
to materialise structs on the stack and burned cycles on each
invocation. R4-type lets us pass **four pointers directly in
registers**, eliminating the descriptor structs entirely; this is
the form documented here and shipped in this repository.

---

## 2. Bit-field layout

```
 31    27 26 25 24    20 19    15 14   12 11    7 6        0
+--------+-----+--------+--------+-------+--------+----------+
|  rs3   | f2  |  rs2   |  rs1   | funct3|  rd    |  opcode  |
|        | 00  |        |        |  000  |        | 0001011  |
+--------+-----+--------+--------+-------+--------+----------+
   5        2     5         5       3       5         7         = 32 bits
```

| field    | bits      | width | fixed value | hex   | role for `attn`                         |
|----------|-----------|-------|-------------|-------|-----------------------------------------|
| `opcode` | `[6:0]`   | 7     | `0001011`   | `0x0b`| identifies the `custom-0` slot          |
| `rd`     | `[11:7]`  | 5     | varies      | —     | integer register holding **O** pointer  |
| `funct3` | `[14:12]` | 3     | `000`       | `0x0` | sub-operation                           |
| `rs1`    | `[19:15]` | 5     | varies      | —     | integer register holding **Q** pointer  |
| `rs2`    | `[24:20]` | 5     | varies      | —     | integer register holding **K** pointer  |
| `funct2` | `[26:25]` | 2     | `00`        | `0x0` | R4-type discriminator for `attn`        |
| `rs3`    | `[31:27]` | 5     | varies      | —     | integer register holding **V** pointer  |

Three of the seven fields (`opcode`, `funct3`, `funct2`) are fixed
constants that *identify the instruction*. The remaining four
(`rd`, `rs1`, `rs2`, `rs3`) are register-number fields that vary
per use site.

---

## 3. Encoding constants (`MATCH` and `MASK`)

A RISC-V toolchain identifies an instruction by computing
`(insn & MASK) == MATCH`. For `attn`:

```
MATCH_ATTN  = 0x0000000b
MASK_ATTN   = 0x0600707f
```

These are derived directly from the bit-field layout in §2:

```python
opcode = 0b0001011               #         0x0b      (bits 6:0)
funct3 = 0b000   << 12           #         0x0       (bits 14:12)
funct2 = 0b00    << 25           #         0x0       (bits 26:25)

MATCH  = opcode | funct3 | funct2          # 0x0000000b
MASK   = 0x7F            \
       | (0x7  << 12)    \
       | (0x3  << 25)                      # 0x0600707f
```

Reading the `MASK` bit by bit:

```
 31    27 26 25 24    20 19    15 14   12 11    7 6        0
+--------+-----+--------+--------+-------+--------+----------+
|  00000 | 11  | 00000  | 00000  |  111  | 00000  | 1111111  |
+--------+-----+--------+--------+-------+--------+----------+
   ↑       ↑       ↑        ↑       ↑       ↑         ↑
   rs3     f2     rs2      rs1   funct3    rd       opcode
  (free) (lock)  (free)   (free) (lock)   (free)    (lock)
```

Bits set to 1 in the mask are the bits the decoder *checks*; bits
set to 0 are register fields that the decoder *reads* without
restriction. Three locked sub-fields, four free register slots —
exactly what R4-type prescribes.

---

## 4. Operand convention and ABI

Assembly syntax:

```asm
attn   rd, rs1, rs2, rs3
```

Semantic role of each operand:

| position | architectural register | logical name | data referenced |
|----------|------------------------|--------------|------------------|
| `rd`     | any of `x0`–`x31`      | **O** pointer| output matrix (written by the hardware) |
| `rs1`    | any of `x0`–`x31`      | **Q** pointer| query matrix             |
| `rs2`    | any of `x0`–`x31`      | **K** pointer| key matrix               |
| `rs3`    | any of `x0`–`x31`      | **V** pointer| value matrix             |

**All four operands are integer registers**, each holding a 64-bit
virtual address (on RV64; 32-bit on RV32) of a matrix that lives in
memory. The matrices themselves are arrays of single-precision
IEEE-754 binary32 floating-point values, stored row-major.

The shape arguments (`N` and `d`) are *not* operands of `attn`. The
proposed accelerator is expected either to:

* read them from architecturally visible CSRs configured before the
  instruction, or
* be specialised for a fixed shape known at chip-fabrication time
  (typical of ASIC accelerators), or
* infer them from the matrices' allocated buffer sizes via metadata
  in TLB/DMA descriptors.

The compiler-side prototype in this repository simply assumes the
hardware "knows" the shape; the test program `sdpa_test.c` uses fixed
`N = d = 32`.

A representative emitted instruction:

```asm
attn   a3, a0, a1, a2     # GCC's typical assignment under -O2
                          # rd  = a3 = O
                          # rs1 = a0 = Q
                          # rs2 = a1 = K
                          # rs3 = a2 = V
```

---

## 5. Bit-level worked example

Take the instruction `attn a3, a0, a1, a2`. The ABI register
numbers are:

| name | x-number | binary 5-bit |
|------|----------|--------------|
| `a0` | x10      | `01010`      |
| `a1` | x11      | `01011`      |
| `a2` | x12      | `01100`      |
| `a3` | x13      | `01101`      |

Substitute into the R4-type layout:

```
 31    27 26 25 24    20 19    15 14   12 11    7 6        0
+--------+-----+--------+--------+-------+--------+----------+
| 01100  |  00 | 01011  | 01010  |  000  | 01101  | 0001011  |
+--------+-----+--------+--------+-------+--------+----------+
  rs3=a2  f2=0  rs2=a1   rs1=a0  f3=0    rd=a3    custom-0
```

Concatenated: `0110_0000_1011_0101_0000_0110_1000_1011`,
hex `0x60b5068b`.

Verification using the decoder identity
`(insn & MASK_ATTN) == MATCH_ATTN`:

```
  insn   = 0x60b5068b = 0110_0000_1011_0101_0000_0110_1000_1011
  MASK   = 0x0600707f = 0000_0110_0000_0000_0111_0000_0111_1111
  insn
   &MASK = 0x0000000b = 0000_0000_0000_0000_0000_0000_0000_1011
  MATCH  = 0x0000000b
```

The AND with `MASK_ATTN` zeroes every bit position that is *not*
part of a locked sub-field — which is to say, every register-number
bit. What remains are the three locked sub-fields
(`opcode`, `funct3`, `funct2`), and they read out as
`0x0b`, `0x0`, `0x0` respectively, exactly the value of
`MATCH_ATTN`. The decoder therefore classifies the word as `attn`.

The standard `objdump` output for this instruction:

```
   0:   60b5068b    attn   a3,a0,a1,a2
```

---

## 6. Architectural semantics

The behaviour of `attn` is informally specified by the equation it
encodes:

$$
O \;=\; \mathrm{softmax}\!\left( \frac{Q K^{\top}}{\sqrt{d_k}} \right) V
$$

with the following conventions:

* `Q`, `K`, `V`, `O` are all `[N × d]` row-major float32 matrices in
  memory, addressed by the four integer-register operands.
* `softmax` is applied **row-wise** to the `[N × N]` intermediate
  similarity matrix `S = Q · Kᵀ / √d` and is numerically stable
  (subtract the row maximum before exponentiating).
* The scaling factor `1/√d` is applied before the softmax.
* On completion of the instruction, `O` is fully written; the input
  matrices `Q`, `K`, `V` are left unchanged.

This specification is **architectural**, not micro-architectural. A
conforming hardware implementation might:

* compute the four phases sequentially in a dedicated functional
  unit;
* compute them in a tiled / FlashAttention-style streaming order
  to avoid materialising the full `[N × N]` similarity matrix;
* dispatch them to a coprocessor with its own DMA engine.

For the simulator-side prototype that will form Phase 4 of the
project (see [`07-research-context.md` §5](07-research-context.md#5-future-work)),
a reasonable starting point is the straightforward four-phase
implementation in `riscv-isa-sim/riscv/insns/attn.h` reading the
four pointers from `rs1/rs2/rs3` and writing through `rd`.

### Status flags, exceptions, memory ordering

The current specification of `attn`:

* does **not** raise a RISC-V exception;
* does **not** modify the floating-point status register
  (`fcsr`) — invalid/inexact arithmetic that arises in the softmax
  is suppressed at the architectural level, mirroring how
  inference-grade accelerators commonly behave;
* is **memory-ordered** with respect to surrounding loads and
  stores in the standard RVWMO (RISC-V Weak Memory Ordering)
  sense: prior stores to `Q`, `K`, `V` are visible to the
  instruction, and subsequent loads from `O` see its writes,
  without the programmer needing to issue a `fence`.

If a future revision wishes to surface FP exceptions or to support
masked / variable-length sequences, the `funct2` field provides
two unused bit patterns that can encode flavour variants.

---

## 7. Toolchain entries that realise the spec

The encoding above is reified by the following entries in the
modified toolchain.

### 7.1 binutils — encoding registry

`binutils/include/opcode/riscv-opc.h`:

```c
#define MATCH_ATTN  0x0000000b
#define MASK_ATTN   0x0600707f
DECLARE_INSN(attn, MATCH_ATTN, MASK_ATTN)
```

`binutils/opcodes/riscv-opc.c`:

```c
{"attn", 0, INSN_CLASS_I, "d,s,t,r", MATCH_ATTN, MASK_ATTN, match_opcode, 0},
```

| field of the table row | value           | meaning                                 |
|------------------------|------------------|-----------------------------------------|
| `name`                 | `"attn"`        | mnemonic the assembler reads            |
| `xlen`                 | `0`             | works on rv32 *and* rv64                |
| `isa`                  | `INSN_CLASS_I`  | base integer ISA — no FP unit needed    |
| `operand_string`       | `"d,s,t,r"`     | rd, rs1, rs2, **rs3** (the `r` is R4-type) |
| `match` / `mask`       | `MATCH_ATTN` / `MASK_ATTN` | encoding constants            |
| `match_func`           | `match_opcode`  | standard `(insn & mask) == match` check |
| `pinfo`                | `0`             | no special flags                        |

`"d,s,t,r"` is the binutils convention for "destination, source-1,
source-2, source-3". The same string is used by `fmadd`.

### 7.2 GCC — backend RTL pattern

`gcc/gcc/config/riscv/riscv.md`:

```scheme
(define_insn "riscv_attn"
  [(set (mem:BLK (match_operand:DI 0 "register_operand" "r"))
        (unspec:BLK
          [(mem:BLK (match_operand:DI 1 "register_operand" "r"))
           (mem:BLK (match_operand:DI 2 "register_operand" "r"))
           (mem:BLK (match_operand:DI 3 "register_operand" "r"))]
          UNSPEC_RISCV_ATTN))]
  "TARGET_ATTN"
  "attn\t%0,%1,%2,%3"
  [(set_attr "type" "ghost")
   (set_attr "mode" "DI")])
```

Three things to note:

* **`mem:BLK`** on every operand — `BLK` is the "block-of-memory"
  mode in RTL. It tells GCC that all four pointers refer to memory
  regions of *unbounded* size, so the optimiser must not perform
  CSE, hoisting, or dead-store elimination across the instruction.
* **`UNSPEC_RISCV_ATTN`** — declares this is an opaque, target-defined
  operation that GCC must not try to simplify or substitute.
* **`type "ghost"`** — RISC-V's RTL scheduler asserts that every
  instruction it sees has a known `type` attribute and a DFA
  reservation. `ghost` is the canonical "do not schedule this; treat
  it as a barrier" type, used for things like prologue markers. We
  use it because we are not committing to a pipeline model for the
  hardware accelerator yet.

### 7.3 GCC — internal function

`gcc/gcc/internal-fn.def`:

```c
DEF_INTERNAL_FN (RISCV_ATTN, ECF_NOTHROW, NULL)
```

`gcc/gcc/internal-fn.cc` (excerpt):

```c
static void
expand_RISCV_ATTN (internal_fn, gcall *stmt)
{
  rtx out = expand_normal (gimple_call_arg (stmt, 0));
  rtx q   = expand_normal (gimple_call_arg (stmt, 1));
  rtx k   = expand_normal (gimple_call_arg (stmt, 2));
  rtx v   = expand_normal (gimple_call_arg (stmt, 3));
  out = force_reg (Pmode, out);
  q   = force_reg (Pmode, q);
  k   = force_reg (Pmode, k);
  v   = force_reg (Pmode, v);
  emit_insn (gen_riscv_attn (out, q, k, v));
}
```

The expander is intentionally trivial: it takes the four GIMPLE
call arguments, materialises each as a Pmode register (`Pmode` is
`DImode` on RV64 and `SImode` on RV32), and hands them to
`gen_riscv_attn`, the helper auto-generated by GCC from the
`define_insn` above.

`ECF_NOTHROW` (and *not* `ECF_LEAF`) tells GCC that the call cannot
raise C++ exceptions but otherwise should be treated as having
arbitrary memory effects. `ECF_LEAF` was tried and removed —
see [`05-troubleshooting.md` Issue 6](05-troubleshooting.md#issue-6--ice-in-propagate_necessity-dce).

### 7.4 The path from C source to the bit pattern

Putting the four entries together:

```
sdpa_test.c                           ┐
                                   │ GCC front end
      ▼                            │
GIMPLE  loop nest                  │
                                   │ GIMPLE optimisation passes
      ▼                            │
attnrec pass — recognises pattern, │ this project
emits IFN_RISCV_ATTN gimple call   │
                                   │
      ▼                            │
expand_RISCV_ATTN — RTL expander   │
                                   │
      ▼                            │
RTL insn matches "riscv_attn"      │
define_insn in riscv.md            │
                                   │
      ▼                            │ GCC backend
"attn  %0,%1,%2,%3" written        │
into the .s file                   ┘
                                   ┐
      ▼                            │
GAS opcode table entry             │ binutils
("attn", "d,s,t,r", MATCH, MASK)   │
encodes the mnemonic into          │
0x0000000b | (regs in their slots) │
                                   ┘
      ▼
ELF object code
```

---

## 8. The `-mattn` compile-time flag

Declared in `gcc/gcc/config/riscv/riscv.opt`:

```
mattn
Target Var(TARGET_ATTN) Init(0)
Enable the custom fused-attention instruction.
```

This produces the macro `TARGET_ATTN` (an integer that is non-zero
when the user passed `-mattn`) and the long-form description that
appears in `gcc --help=target`.

`TARGET_ATTN` gates two things:

1. **The `attnrec` pass** — its `gate()` method returns false if
   `TARGET_ATTN == 0`, so the pass never runs.
2. **The `define_insn`** — its predicate is `"TARGET_ATTN"`, so even
   if some other code path tried to emit `riscv_attn`, the matcher
   would refuse outside of `-mattn`.

A soft warning is emitted (in `riscv.cc`'s `riscv_option_override`)
if the user combines `-mattn` with `-march=rv32...`, because while
the encoding is XLEN-agnostic the pass has only been validated on
RV64.

Usage examples:

```bash
# enabled — pass runs, attn may be emitted
riscv64-unknown-elf-gcc -mattn -O2 -S sdpa_test.c -o sdpa_test.s

# disabled — pass gate returns false, no attn emitted
riscv64-unknown-elf-gcc       -O2 -S sdpa_test.c -o sdpa_test.s

# emitting GIMPLE dump to inspect the pass behaviour
riscv64-unknown-elf-gcc -mattn -O2 -fdump-tree-attnrec-details \
    -c sdpa_test.c -o sdpa_test.o
cat sdpa_test.c.*attnrec*
```

---

## 9. Quick-reference card

```
Mnemonic     :  attn rd, rs1, rs2, rs3
Format       :  R4-type, 32-bit
Slot         :  custom-0   (opcode[6:0] = 0001011 = 0x0b)
funct3       :  000
funct2       :  00
MATCH        :  0x0000000b
MASK         :  0x0600707f
Operands     :  rd  = O  pointer (output)
                rs1 = Q  pointer (query)
                rs2 = K  pointer (key)
                rs3 = V  pointer (value)
Data type    :  IEEE-754 binary32 (float), row-major matrices
Compile flag :  -mattn
GCC version  :  15.2.0
Validated on :  rv64gc / lp64d, GNU/Newlib bare-metal, Ubuntu 24.04
```

---

**Next:** [`02-compiler-pass.md`](02-compiler-pass.md) —
how the compiler decides, automatically, that a given loop nest is
"attention" and replaces it with this instruction.
