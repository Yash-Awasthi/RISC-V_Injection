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

The choice of R4-type rather than R-type is deliberate: it gives
`attn` four independent register operands instead of two, room
enough to split the ABI into four small blocks (output, QKV, dims,
scale — see §4) rather than cramming everything into one or two
descriptor structs. The encoding only fixes the register *count*;
what each register points at is an ABI convention, defined once in
§4.

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
| `rd`     | `[11:7]`  | 5     | varies      | —     | integer register holding the **output matrix** pointer directly (§4) |
| `funct3` | `[14:12]` | 3     | `000`       | `0x0` | sub-operation                           |
| `rs1`    | `[19:15]` | 5     | varies      | —     | integer register holding a pointer to the **attn_ptrs block** (§4) |
| `rs2`    | `[24:20]` | 5     | varies      | —     | integer register holding a pointer to the **attn_dims block** (§4) |
| `funct2` | `[26:25]` | 2     | `00`        | `0x0` | R4-type discriminator for `attn`        |
| `rs3`    | `[31:27]` | 5     | varies      | —     | integer register holding a pointer to the **attn_cfg block** (§4) |

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

> This section is the single normative definition of the `attn`
> operand ABI. Every other file in this repository that describes
> what `rd`, `rs1`, `rs2`, `rs3` point at refers back to this
> section instead of restating it.

Assembly syntax:

```asm
attn   rd, rs1, rs2, rs3
```

**All four operands are integer registers**, each holding a 64-bit
virtual address (on RV64; 32-bit on RV32). `rd` points directly at
the output matrix; `rs1`, `rs2`, `rs3` each point at a small,
fixed-layout block that carries the input pointers, the shape, and
the scale:

| position | logical name  | points to (C-equivalent layout) | size |
|----------|---------------|----------------------------------|------|
| `rd`     | `O`           | the output matrix directly — `void *`, no wrapper block | 8 B |
| `rs1`    | `attn_ptrs`   | `struct { const void *q, *k, *v; }` | 24 B |
| `rs2`    | `attn_dims`   | `struct { uint64_t n, d, h; }` — `h` is head count, `1` for the single-head default | 24 B |
| `rs3`    | `attn_cfg`    | `struct { uint32_t scale_bits, flags; }` — `scale_bits` holds the IEEE-754 binary32 bit pattern of `1/sqrt(d)`; `flags` is reserved and currently always `0` | 8 B |

These three struct names and layouts are not just documentation:
they are the exact structs declared in
[`../demo/attn.h`](../demo/attn.h), the reference C-side realisation
of this ABI, checked field-for-field against
[`../tools/attn_model.py`](../tools/attn_model.py)'s `ATTN_STRUCTS`
by `scripts/tests/test_attn_contract.py`.

The shape of the problem — `N, D, H` — is therefore no longer
implicit: it travels in the `attn_dims` block that `rs2` points to,
and the hardware reads it from memory instead of needing dedicated
CSRs or a shape fixed at chip-fabrication time. `Q`, `K`, `V`, `O`
remain `[N x D]` row-major float32 matrices; only the way their
addresses reach the instruction has changed, from four direct
pointers to one level of indirection for the three input-side
operands.

Building the three input blocks — allocating them, filling in their
fields, keeping them alive across the instruction — is the caller's
job. The instruction encoding, the assembler entry, and the RTL
pattern are unaffected by this choice; they still see four plain
integer-register operands. Only what `rs1`, `rs2`, `rs3` point at
has changed.

A representative emitted instruction:

```asm
attn   a3, a0, a1, a2     # GCC's typical assignment under -O2
                          # rd  = a3 = O                (output matrix)
                          # rs1 = a0 = &attn_ptrs { q, k, v }
                          # rs2 = a1 = &attn_dims { n, d, h }
                          # rs3 = a2 = &attn_cfg  { scale_bits, flags }
```

**Known limitation.** The GIMPLE pass that emits `attn`
(`gcc/gcc/tree-ssa-attn.cc`) predates this ABI: its call site still
passes the four raw `O`/`Q`/`K`/`V` pointers directly instead of
building the `attn_ptrs`/`attn_dims`/`attn_cfg` blocks above. That
path is flagged experimental and non-conforming in the pass's own
known-limitation comment; treat any `attn` the idiom-recognition
pass emits today accordingly. The explicit-call path through
`demo/attn.h` and `__builtin_riscv_attn` does conform.

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
  memory. `O` is addressed directly by `rd`; `Q`, `K`, `V` are
  addressed indirectly through the `attn_ptrs` block that `rs1`
  points at (§4).
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
implementation in `riscv-isa-sim/riscv/insns/attn.h`, reading the
`attn_ptrs`/`attn_dims`/`attn_cfg` blocks (§4) through `rs1`, `rs2`,
`rs3` and writing the output matrix directly through `rd`.

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

### 7.3 GCC — internal function (removed)

Earlier revisions of this compiler routed `attnrec` through a
dedicated internal function, `IFN_RISCV_ATTN`, expanded by
`expand_RISCV_ATTN` in `gcc/gcc/internal-fn.cc`. That internal
function has since been deleted from the tree in favour of the
builtin path in §7.5: `attnrec` now calls `__builtin_riscv_attn`
directly (`gcc/gcc/tree-ssa-attn.cc`, `attn_emit_replacement`)
instead of emitting a call to a bespoke internal function. Neither
`IFN_RISCV_ATTN` nor `expand_RISCV_ATTN` exist in the current
source tree; this subsection is kept only as a historical note so
old dumps or patches that mention `IFN_RISCV_ATTN` are not
mistaken for a live mechanism.

### 7.4 The path from C source to the bit pattern

Putting the current entries together:

```
sdpa_test.c                           ┐
                                   │ GCC front end
      ▼                            │
GIMPLE  loop nest                  │
                                   │ GIMPLE optimisation passes
      ▼                            │
attnrec pass — recognises pattern, │ this project
calls __builtin_riscv_attn (§7.5)  │
                                   │
      ▼                            │
builtin expansion — no hand-written│
expander, see §7.5                 │
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

### 7.5 GCC — explicit builtin

Alongside the idiom-recognition path in §7.1–§7.4, the compiler also
exposes `attn` directly as a builtin function:

```c
void __builtin_riscv_attn(void *rd, void *rs1, void *rs2, void *rs3);
```

This is a straight 1:1 mapping onto the encoding in §1–§3: the four
`void *` arguments correspond directly to `rd`, `rs1`, `rs2`, `rs3`,
in that order, with no shape inference and no semantics attached by
the compiler beyond "put these four addresses in registers and emit
`attn`". A caller who wants the softmax-attention behaviour of §6 is
responsible for supplying pointers that satisfy it; the compiler does
not check.

Declared in `gcc/gcc/config/riscv/riscv-builtins.cc`:

```c
AVAIL (attn, TARGET_ATTN)

...

DIRECT_NO_TARGET_BUILTIN (attn,
			  RISCV_VOID_FTYPE_VOID_PTR_VOID_PTR_VOID_PTR_VOID_PTR,
			  attn),
```

`DIRECT_NO_TARGET_BUILTIN` maps the builtin straight onto
`CODE_FOR_riscv_attn` — the same `define_insn` from §7.2 — so this
path adds no new RTL pattern and no new expander.
`RISCV_VOID_FTYPE_VOID_PTR_VOID_PTR_VOID_PTR_VOID_PTR` is a new
prototype row in `gcc/gcc/config/riscv/riscv-ftypes.def`:

```c
DEF_RISCV_FTYPE (4, (VOID, VOID_PTR, VOID_PTR, VOID_PTR, VOID_PTR))
```

`AVAIL (attn, TARGET_ATTN)` gates the builtin on the same `-mattn`
flag as the idiom-recognition path (§8); calling
`__builtin_riscv_attn` without `-mattn` is a compile-time error,
the same as calling any other target-gated builtin outside its `-m`
flag.

`riscv_attn`'s operands are declared `register_operand:DI`. GCC's
generic builtin-expansion machinery (`maybe_legitimize_operand` in
`optabs.cc`) copies any input operand that fails an insn's predicate
into a fresh pseudo register, so the four pointer arguments do not
need to already sit in registers at the call site — an address
computed on the fly, or loaded from a local, is copied into a
register automatically before `attn` is emitted. No hand-written
expander is needed for this.

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
Operands     :  rd  = O               output matrix, direct pointer (§4)
                rs1 = &attn_ptrs { q, k, v }                    (§4)
                rs2 = &attn_dims { n, d, h }                    (§4)
                rs3 = &attn_cfg  { scale_bits, flags }          (§4)
Data type    :  IEEE-754 binary32 (float), row-major matrices
Builtin      :  void __builtin_riscv_attn(void*,void*,void*,void*)
                 -- rd,rs1,rs2,rs3, 1:1 with the encoding
Compile flag :  -mattn
GCC version  :  15.2.0
Validated on :  rv64gc / lp64d, GNU/Newlib bare-metal, Ubuntu 24.04
```

---

**Next:** [`02-compiler-pass.md`](02-compiler-pass.md) —
how the compiler decides, automatically, that a given loop nest is
"attention" and replaces it with this instruction.
