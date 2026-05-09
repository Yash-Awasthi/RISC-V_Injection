# Opcode and Operand Fields — Custom `attn` Instruction

## Instruction: `attn` (Attention Mechanism)

### Format: R4-type

RISC-V uses R4-type for instructions needing four register operands.
The standard example is FMADD (fused multiply-add). We use the same
format for `attn`, repurposing the `funct2` field (bits [26:25]) to
lock down the instruction, and `rs3` (bits [31:27]) for the 4th operand.

```
 31   27 26 25 24  20 19  15 14 12 11   7 6      0
+------+----+------+------+-----+------+--------+
|  rs3 | f2 |  rs2 |  rs1 | f3  |  rd  | opcode |
|      | 00 |      |      | 000 |      | 0001011|
+------+----+------+------+-----+------+--------+
   5     2     5      5     3      5       7
```

### Field Table

| Field   | Bits    | Width | Value      | Hex    | Description                  |
|---------|---------|-------|------------|--------|------------------------------|
| opcode  | [6:0]   | 7     | `0001011`  | `0x0b` | custom-0 opcode slot         |
| rd      | [11:7]  | 5     | varies     | —      | output pointer (O matrix)    |
| funct3  | [14:12] | 3     | `000`      | `0x0`  | sub-operation code           |
| rs1     | [19:15] | 5     | varies     | —      | Q matrix pointer             |
| rs2     | [24:20] | 5     | varies     | —      | K matrix pointer             |
| funct2  | [26:25] | 2     | `00`       | `0x0`  | R4-type format discriminator |
| rs3     | [31:27] | 5     | varies     | —      | V matrix pointer             |

### Encoding Constants

| Constant   | Value        | Derivation                                      |
|------------|--------------|--------------------------------------------------|
| MATCH_ATTN | `0x0000000b` | `opcode=0x0b, funct3=0, funct2=0`               |
| MASK_ATTN  | `0x0600707f` | Masks funct2[26:25] + funct3[14:12] + opcode[6:0] |

Verification:
```python
opcode = 0b0001011       # 0x0b — custom-0
funct3 = 0b000 << 12     # 0x0
funct2 = 0b00  << 25     # 0x0

MATCH = opcode | funct3 | funct2   # = 0x0000000b
MASK  = 0x7F | (0x7 << 12) | (0x3 << 25)  # = 0x0600707f

print(hex(MATCH))  # 0xb
print(hex(MASK))   # 0x600707f
```

### Bit-Level Diagram — `attn a3, a0, a1, a2`

```
rs3=a2=x12=01100, funct2=00, rs2=a1=x11=01011, rs1=a0=x10=01010
funct3=000, rd=a3=x13=01101, opcode=0001011

 31      27 26 25 24     20 19     15 14  12 11      7 6       0
+----------+----+----------+----------+------+----------+--------+
|  0 1 1 0 0| 00 | 0 1 0 1 1| 0 1 0 1 0| 000  | 0 1 1 0 1|0001011|
+----------+----+----------+----------+------+----------+--------+
     a2    f2=0      a1          a0    f3=0      a3      custom-0
```

Binary: `0000_0110_1011_0101_0000_0110_1000_1011`

---

## Operand Convention

```
attn  rd, rs1, rs2, rs3

  rd  — integer register holding pointer to O (output, written by hardware)
  rs1 — integer register holding pointer to Q (query matrix)
  rs2 — integer register holding pointer to K (key matrix)
  rs3 — integer register holding pointer to V (value matrix)
```

All four are **integer registers** (x0–x31), holding 64-bit addresses.
The matrices themselves are arrays of single-precision `float` (IEEE 754
binary32) in row-major order.

### Assembly Syntax

```asm
attn  rd, rs1, rs2, rs3
```

Examples from actual compiler output:
```asm
attn  a3, a0, a1, a2    # GCC assigns: rd=a3(O) rs1=a0(Q) rs2=a1(K) rs3=a2(V)
attn  s3, s5, s6, s4    # alternate register assignment for same operation
```

---

## Binutils Entries

### `riscv-opc.h`

```c
#define MATCH_ATTN 0x0000000b
#define MASK_ATTN  0x0600707f
DECLARE_INSN(attn, MATCH_ATTN, MASK_ATTN)
```

### `riscv-opc.c`

```c
{"attn", 0, INSN_CLASS_I, "d,s,t,r", MATCH_ATTN, MASK_ATTN, match_opcode, 0},
```

| Field      | Value          | Meaning                              |
|------------|----------------|--------------------------------------|
| name       | `"attn"`       | assembly mnemonic                    |
| xlen       | `0`            | works on any XLEN (rv32 and rv64)    |
| isa        | `INSN_CLASS_I` | base integer ISA                     |
| operands   | `"d,s,t,r"`   | rd, rs1, rs2, rs3 (4 int registers)  |
| match      | `MATCH_ATTN`   | `0x0000000b`                         |
| mask       | `MASK_ATTN`    | `0x0600707f`                         |
| match_func | `match_opcode` | standard opcode matcher              |
| pinfo      | `0`            | no special flags                     |

The `"r"` in the operand string `"d,s,t,r"` is the R4-type 4th register
(rs3). This is the same convention used by FMADD in the standard ISA.

---

## GCC Backend Entries

### `riscv.opt`

```
mattn
Target Var(TARGET_ATTN) Init(0)
Enable the custom fused-attention instruction.
```

### `riscv.md` — UNSPEC

```scheme
UNSPEC_RISCV_ATTN
```

Added inside the existing `define_c_enum "unspec"` block.

### `riscv.md` — define_insn

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

`mem:BLK` — tells GCC all four operands reference unbounded memory regions.
Prevents incorrect CSE, hoisting, or dead-code elimination around the call.

`type "ghost"` — tells the RTL scheduler this instruction is a blockage.
Required because the RISC-V scheduler asserts on `TYPE_UNKNOWN` and
requires a DFA reservation for every non-ghost instruction.

### `internal-fn.def`

```c
DEF_INTERNAL_FN (RISCV_ATTN, ECF_NOTHROW, NULL)
```

`ECF_LEAF` was intentionally removed. With `ECF_LEAF`, DCE treats the
call as not touching memory and may eliminate it. `ECF_NOTHROW` alone
is sufficient and correct.

---

## Mathematical Operation

```
Attention(Q, K, V) = softmax(Q × Kᵀ / √d) × V
```

Stages replaced by the single `attn` instruction:

| Stage | Operation              | Loop nests in C  |
|-------|------------------------|------------------|
| 1     | Q × Kᵀ                | 3 nested loops   |
| 2     | Scale by 1/√d          | folded into stage 1 at -O2 |
| 3     | Row-wise softmax       | 2 nested loops   |
| 4     | Scores × V             | 3 nested loops   |

Note: At `-O2`, GCC folds `1/sqrtf(D)` to the float constant
`1.7677669227123260498046875e-1` (for D=32) and multiplies it into
the accumulation loop. The scale is precomputed — no runtime sqrt.

---

## Quick Reference

```
Mnemonic  : attn rd, rs1, rs2, rs3
Format    : R4-type (32-bit)
Encoding  : funct2=00 | funct3=000 | opcode=0001011
MATCH     : 0x0000000b
MASK      : 0x0600707f
Slot      : custom-0 (opcode[6:2] = 0x02)
Flag      : -mattn (enables attnrec pass and instruction emission)
Data type : float (IEEE 754 binary32), row-major matrices
```
