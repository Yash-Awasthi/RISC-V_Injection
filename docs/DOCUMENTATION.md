# Documentation — RISC-V Custom `attn` Instruction

**Author:** Yash Awasthi
**Toolchain:** riscv-gnu-toolchain (GCC 15.2.0, Binutils 2.46)
**Host:** Ubuntu 24.04

---

## 1. What Is This?

This project adds a custom RISC-V instruction `attn` to the GNU toolchain
that represents the full scaled dot-product attention (SDPA) computation:

```
Attention(Q, K, V) = softmax(Q × Kᵀ / √d) × V
```

SDPA is the core operation inside every Transformer model — GPT, BERT,
LLaMA, etc. It is computationally expensive: four loop nests, thousands
of multiply-add operations, transcendental functions (exp), and divisions.

The goal is to let a hardware accelerator execute the entire computation
in a single instruction, with the compiler automatically recognizing the
pattern and emitting it — no manual intrinsics or builtins needed.

---

## 2. System Architecture

The implementation has two layers:

```
Layer 1 — Instruction Encoding (Binutils)
  Registers attn in the assembler and disassembler so the instruction
  can be assembled, disassembled, and objdump'd correctly.

Layer 2 — Compiler Pass (GCC)
  A GCC middle-end pass (attnrec) that:
    a) Recognizes the 4-stage attention loop pattern in GIMPLE IR
    b) Emits IFN_RISCV_ATTN (internal function) at the GIMPLE level
    c) The RISC-V backend lowers IFN_RISCV_ATTN to the attn instruction
```

Layer 1 is a prerequisite for Layer 2.

---

## 3. The `attn` Instruction

### Format

R4-type — four register operands in a 32-bit instruction word.
RISC-V already uses R4-type for floating-point fused multiply-add (FMADD).

```
 31   27 26 25 24  20 19  15 14 12 11   7 6      0
+------+----+------+------+-----+------+--------+
|  rs3 | f2 |  rs2 |  rs1 | f3  |  rd  | opcode |
+------+----+------+------+-----+------+--------+
   5     2     5      5     3      5       7
```

| Field   | Bits    | Value      | Meaning                  |
|---------|---------|------------|--------------------------|
| opcode  | [6:0]   | `0001011`  | custom-0 slot            |
| rd      | [11:7]  | varies     | destination register      |
| funct3  | [14:12] | `000`      | sub-operation code        |
| rs1     | [19:15] | varies     | source register 1         |
| rs2     | [24:20] | varies     | source register 2         |
| funct2  | [26:25] | `00`       | R4-type format code       |
| rs3     | [31:27] | varies     | source register 3         |

### Encoding Constants

```c
MATCH_ATTN = 0x0000000b   // opcode + funct3 + funct2 fixed
MASK_ATTN  = 0x0600707f   // masks funct2[26:25] + funct3[14:12] + opcode[6:0]
```

### Operand Convention

```
attn rd, rs1, rs2, rs3

  rd  = pointer to O  (output matrix, written by hardware)
  rs1 = pointer to Q  (query matrix)
  rs2 = pointer to K  (key matrix)
  rs3 = pointer to V  (value matrix)
```

The hardware (or simulator) reads the four pointers from registers,
performs the full SDPA computation, and writes results to `*rd`.

### Example

```asm
attn  a3, a0, a1, a2     # O=a3, Q=a0, K=a1, V=a2
```

---

## 4. GCC Pass — `attnrec`

### Location in Pass Pipeline

The pass runs at position 179 in the GIMPLE optimization pipeline,
immediately after Graphite loop transformations. This is after:
- Loop invariant motion
- Loop distribution
- Graphite polyhedral transformations

And before:
- RTL generation
- Register allocation

### Gate Condition

The pass only runs when:
1. `-mattn` flag is present
2. Optimization level is `-O2` or higher
3. Loop optimization is enabled (`-ftree-loop-optimize`)

### Detection Algorithm

For each loop nest in the function, the pass checks:

**Check 1 — Inner madd reduction**
The innermost loop must contain a phi node whose back-edge definition
is a PLUS_EXPR, one operand of which is a MULT_EXPR. This is the
dot-product accumulation pattern:
```c
acc += Q[i][d] * K[j][d];   // → phi + (mult Q K)
```

**Check 2 — Softmax signature**
The function body (all basic blocks) must contain:
- At least one `expf` or `exp` call
- At least one `RDIV_EXPR` or `TRUNC_DIV_EXPR`

This matches the softmax computation:
```c
S[j] = expf(S[j]);    // expf call
S[j] /= sum;          // division
```

Note: At `-O2`, `sqrtf(D)` is folded to a float constant by GCC
(e.g., `1/sqrt(32) = 0.17677...`), so the sqrt call does not appear
in GIMPLE. The matcher looks for exp+div only.

**Check 3 — Madd count**
The function must contain at least 2 loops with madd reductions —
one for QKᵀ and one for S·V.

**Check 4 — Load/store base pointers**
Scanning all basic blocks in the function, there must be at least
3 distinct load base pointers (Q, K, V) and 1 store base pointer (O).
Local stack arrays (like S[]) are excluded by checking `DECL_EXTERNAL`
and `TREE_STATIC`.

**Check 5 — Trip count**
The outer loop trip count must be statically analyzable by SCEV
(scalar evolution). `chrec_dont_know` causes rejection.

### Emission

When all checks pass, the pass:
1. Converts the four base pointers to `void*`
2. Builds a `gcall` for `IFN_RISCV_ATTN` with 4 arguments
3. Marks it `volatile` so DCE never eliminates it
4. Inserts it in the preheader of the matched outer loop
5. The original loop body remains (dead code pending Phase 4)

### GIMPLE Dump

To see the pass output:
```bash
riscv64-unknown-elf-gcc -mattn -O2 -fdump-tree-attnrec-details \
    -c finale.c -o finale.o

cat finale.c.*attnrec*
```

Expected dump contains:
```
;; attnrec: loop N load bases found: 3
;;   base[0]: Q_xx(D)
;;   base[1]: K_xx(D)
;;   base[2]: V_xx(D)
;;   store base: O_xx(D)
;; attnrec: replaced loop N with IFN_RISCV_ATTN
;;   rd=O  rs1=Q  rs2=K  rs3=V (direct pointers)
.RISCV_ATTN ((void *) O, (void *) Q, (void *) K, (void *) V);
```

---

## 5. Backend — `define_insn`

In `gcc/gcc/config/riscv/riscv.md`:

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

`mem:BLK` operands tell GCC the instruction reads/writes an unbounded
region of memory, preventing incorrect CSE or dead-code elimination.

`type "ghost"` tells the RTL instruction scheduler to treat this as
a blockage — it has no DFA reservation, so the scheduler skips it
rather than asserting on an unknown instruction type.

---

## 6. IFN Expander

In `gcc/gcc/internal-fn.cc`, `expand_RISCV_ATTN` lowers the GIMPLE
internal function call to RTL:

```c
static void
expand_RISCV_ATTN (internal_fn, gcall *stmt)
{
  rtx out = expand_normal (gimple_call_arg (stmt, 0));  // O pointer
  rtx q   = expand_normal (gimple_call_arg (stmt, 1));  // Q pointer
  rtx k   = expand_normal (gimple_call_arg (stmt, 2));  // K pointer
  rtx v   = expand_normal (gimple_call_arg (stmt, 3));  // V pointer
  out = force_reg (Pmode, out);
  q   = force_reg (Pmode, q);
  k   = force_reg (Pmode, k);
  v   = force_reg (Pmode, v);
  emit_insn (gen_riscv_attn (out, q, k, v));
}
```

`gen_riscv_attn` is automatically generated by GCC from the `define_insn`
in `riscv.md`. No manual RTL construction needed.

---

## 7. Why the Loop Body Remains

After the `attn` instruction is emitted, the original loop code stays
in the binary. This is correct behavior at this stage.

GCC is a compiler, not a theorem prover. It recognized the pattern and
emitted the instruction, but it cannot verify that the hardware actually
computes the correct result. Removing the loops without that guarantee
would produce silent wrong answers if the hardware implementation is buggy.

**To remove the loop body, you need:**

1. A Spike simulator implementation of `attn` (`Phase 4`):
   - File: `riscv-isa-sim/riscv/insns/attn.h`
   - Reads Q, K, V pointers from rs1, rs2, rs3
   - Computes full SDPA in simulated hardware
   - Writes result to `*rd`

2. Equivalence verification:
   - Run the C loop version and the `attn` version on identical inputs
   - Compare outputs bit-by-bit
   - Confirmed match → loops are provably dead code

3. Dead-code elimination pass (compiler or linker level)

This pipeline is standard practice in hardware-software co-design.

---

## 8. The `-mattn` Flag

Declared in `gcc/gcc/config/riscv/riscv.opt`:

```
mattn
Target Var(TARGET_ATTN) Init(0)
Enable the custom fused-attention instruction.
```

Usage:
```bash
# Enable attn recognition
riscv64-unknown-elf-gcc -mattn -O2 ...

# Without -mattn, the attnrec pass gate returns false
riscv64-unknown-elf-gcc -O2 ...     # no attn emitted
```

A soft warning is emitted if `-mattn` is used on rv32 targets (the
encoding is 32-bit and works on both rv32 and rv64, but only rv64 has
been validated).

---

## 9. Compiler Version

```
riscv64-unknown-elf-gcc (g5115c7e447f-dirty) 15.2.0
```

The `dirty` suffix indicates local modifications (your custom pass and
instruction additions) on top of the upstream GCC 15.2.0 release branch.
