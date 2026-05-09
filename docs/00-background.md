# 00 — Background: Everything You Need Before Reading the Rest

> **Audience.** A CS undergraduate who has *not* taken a compilers
> course, has *not* worked with RISC-V, and may have only a textbook
> notion of what a Transformer is. Everything assumed elsewhere in
> this repository is built up here from first principles.
> A reader already comfortable with GCC internals and the RISC-V ISA
> can skip to [`01-instruction-spec.md`](01-instruction-spec.md).

---

## Table of contents

1. [What is RISC-V, and why does it matter for research?](#1-what-is-risc-v-and-why-does-it-matter-for-research)
2. [What is a *custom instruction*?](#2-what-is-a-custom-instruction)
3. [What is the GNU toolchain?](#3-what-is-the-gnu-toolchain)
4. [The journey of a C program: from source to silicon](#4-the-journey-of-a-c-program-from-source-to-silicon)
5. [Anatomy of a 32-bit RISC-V instruction word](#5-anatomy-of-a-32-bit-risc-v-instruction-word)
6. [What is "attention" in a Transformer?](#6-what-is-attention-in-a-transformer)
7. [GIMPLE, SSA, and the GCC pass pipeline](#7-gimple-ssa-and-the-gcc-pass-pipeline)
8. [Putting it all together](#8-putting-it-all-together)

---

## 1. What is RISC-V, and why does it matter for research?

**RISC-V** (pronounced "risk-five") is an *open* instruction set
architecture (ISA). An ISA is the contract between hardware and
software: it lists every operation a CPU is required to understand,
and specifies their binary encodings.

Most ISAs you have heard of — x86, ARM, PowerPC — are *proprietary*.
You cannot legally design and sell a chip that implements them
without a licence. RISC-V was designed at UC Berkeley specifically
to remove that barrier. Its specification is public, royalty-free,
and *modular*: a chip implementer can pick and choose extensions
(integer multiplication, atomics, floating point, vector operations,
…) to suit their target market.

For computer-architecture research this is transformative. A research
group can:

* propose a new instruction;
* simulate it (in QEMU or Spike);
* implement it in synthesisable RTL;
* publish; and
* let other groups *actually adopt* the instruction without paying
  royalties.

The standard does not just allow custom instructions — it actively
**reserves opcode space** for them. Four 7-bit opcode values
(`custom-0`, `custom-1`, `custom-2`, `custom-3`) are guaranteed
never to be used by the official specification. Anything we put
there is safe forever.

| Slot | `opcode[6:0]` | hex | who uses it |
|------|---------------|-----|-------------|
| `custom-0` | `0001011` | `0x0b` | this project (`attn`) |
| `custom-1` | `0101011` | `0x2b` | free |
| `custom-2` | `1011011` | `0x5b` | free |
| `custom-3` | `1111011` | `0x7b` | free |

Within each slot, the `funct3` and `funct7` (or `funct2` for R4-type)
sub-fields disambiguate further. A single slot accommodates hundreds
of distinct custom instructions.

The configuration this project targets is **RV64GC**:

* **RV64** — 64-bit base integer ISA (registers and pointers are
  64 bits wide).
* **G** — the "general purpose" extension bundle, shorthand for
  IMAFD: **I**nteger, **M**ultiplication, **A**tomics, **F**loat
  (single precision), **D**ouble precision.
* **C** — the *Compressed* extension, which adds 16-bit short
  encodings of common instructions.

---

## 2. What is a *custom instruction*?

A *custom instruction* is one that does not exist in the published
RISC-V specification but that we have defined ourselves and taught
the toolchain to recognise. Three things are required for it to be
real:

1. **An encoding.** A specific 32-bit binary pattern in one of the
   custom opcode slots, with our chosen `funct3` / `funct7` /
   `funct2` sub-fields. Any other bit pattern is *not* our
   instruction.
2. **A toolchain entry.** The assembler must know that the mnemonic
   `attn` maps to that encoding; the disassembler must know the
   reverse direction; the compiler must know how to emit it.
3. **A semantics.** Some piece of hardware (or an instruction-set
   simulator) must actually do something useful when it executes
   that bit pattern.

This project is concerned only with the first two. The third — the
hardware semantics — is deliberately deferred (see
[`07-research-context.md` §5](07-research-context.md#5-future-work)).

The conceptual operation that `attn` represents is the
Transformer attention layer:

```
Attention(Q, K, V) = softmax( Q · Kᵀ / √d ) · V
```

We will see in §6 below where this expression comes from, what each
matrix means, and why one might want to push it down into the
hardware instead of doing it in software.

---

## 3. What is the GNU toolchain?

The "GNU toolchain" is the standard open-source set of programs that
turns a C source file into an executable. For RISC-V development we
use a *cross-toolchain*: it runs on a normal x86 laptop but produces
RISC-V binaries.

| Component | Role | Binary name (after install) |
|-----------|------|------------------------------|
| **GCC**     | C/C++ compiler — `.c → .s` | `riscv64-unknown-elf-gcc`     |
| **GAS**     | GNU assembler — `.s → .o`  | `riscv64-unknown-elf-as`      |
| **LD**      | linker — `.o + .o → .elf`  | `riscv64-unknown-elf-ld`      |
| **objdump** | disassembler — `.o → text` | `riscv64-unknown-elf-objdump` |
| **binutils**| package containing GAS, LD, objdump, ar, … | — |
| **newlib**  | minimal C standard library for bare-metal targets | linked in |

The prefix `riscv64-unknown-elf` is a *target triplet*:

* `riscv64` — the architecture;
* `unknown` — vendor (we are not Intel or Apple, so we leave this generic);
* `elf` — the object-file format (Executable and Linkable Format,
  the standard format on Linux and on most bare-metal RISC-V boards).

To add a custom instruction we need to modify two of these:

* **binutils**, so the assembler can produce its encoding and
  `objdump` can recognise it on the way back;
* **GCC**, so the compiler will *emit* it.

That is it. The linker, library, and OS are unaffected.

---

## 4. The journey of a C program: from source to silicon

The most useful diagram for understanding why this project edits the
files it edits is the compilation pipeline. Each stage is owned by
a different program, and a custom instruction has to be threaded
through every one of them.

```
                     +-------------------+
        finale.c     |  C source         |
                     +-------------------+
                              |
                              | preprocess (cpp)
                              v
                     +-------------------+
                     |  preprocessed C   |
                     +-------------------+
                              |
                              | parse + gimplify  (front end)
                              v
                     +-------------------+
                     |  GIMPLE IR (SSA)  |   <-- our pass runs here
                     +-------------------+
                              |
                              | ~250 GIMPLE optimisation passes
                              v
                     +-------------------+
                     |   GIMPLE IR       |
                     +-------------------+
                              |
                              | RTL expansion
                              v
                     +-------------------+
                     |   RTL IR          |   <-- define_insn matches here
                     +-------------------+
                              |
                              | register allocation, scheduling
                              v
                     +-------------------+
                     |   assembly text   |   <-- "attn  a3,a0,a1,a2"
                     +-------------------+
                              |
                              | assembler (binutils/gas)         <-- needs MATCH/MASK + opcode table
                              v
                     +-------------------+
                     |   ELF object      |
                     +-------------------+
                              |
                              | linker
                              v
                     +-------------------+
                     |   ELF executable  |
                     +-------------------+
                              |
                              | execute on QEMU / Spike / silicon
                              v
                            results
```

Two things are worth highlighting:

* **GIMPLE** is GCC’s machine-independent intermediate representation.
  We will spend a lot of time there in [`02-compiler-pass.md`](02-compiler-pass.md).
* **RTL** (Register Transfer Language) is GCC's lower, machine-aware
  IR. The mapping from "this is the `attn` operation" to "emit the
  string `attn ...` into the assembly file" lives in an RTL pattern
  called a `define_insn` (in `riscv.md`).

---

## 5. Anatomy of a 32-bit RISC-V instruction word

Every base RISC-V instruction is exactly 32 bits wide. (The C
extension adds 16-bit *compressed* forms, but custom instructions
in the standard slots are 32 bits.) The 32 bits are partitioned
into named *fields*, and the partitioning differs by *format*.
The standard formats are R, I, S, B, U, J, and a less-known one
called R4.

For `attn` we use **R4-type**, the same format used by the standard
fused-multiply-add `fmadd`. R4 is the only format with four register
operands.

```
 31    27 26 25 24    20 19    15 14   12 11    7 6        0
+--------+-----+--------+--------+-------+--------+----------+
|  rs3   | f2  |  rs2   |  rs1   | funct3|  rd    |  opcode  |
+--------+-----+--------+--------+-------+--------+----------+
   5 bits 2 bits 5 bits   5 bits  3 bits  5 bits   7 bits      = 32 bits
```

| field    | bits     | meaning                                       |
|----------|----------|-----------------------------------------------|
| `opcode` | `[6:0]`  | major instruction class (which opcode slot)   |
| `rd`     | `[11:7]` | destination register number (5 bits → 32 regs)|
| `funct3` | `[14:12]`| sub-class                                     |
| `rs1`    | `[19:15]`| source register 1                              |
| `rs2`    | `[24:20]`| source register 2                              |
| `funct2` | `[26:25]`| further sub-class (only in R4-type)            |
| `rs3`    | `[31:27]`| source register 3                              |

Each register field is 5 bits wide because RISC-V has 32 integer
registers (`x0`–`x31`), and `2⁵ = 32`. Registers also have ABI
aliases — `a0` = `x10`, `a1` = `x11`, `sp` = `x2`, and so on; we use
those names in the assembly listings throughout the docs.

Decoding a 32-bit word into one of our `attn` instructions is
straightforward: a CPU computes `(insn & MASK_ATTN) == MATCH_ATTN`.
If that succeeds, the four register fields are read out at their
fixed positions. The full worked example lives in
[`01-instruction-spec.md` §5](01-instruction-spec.md#5-bit-level-worked-example).

---

## 6. What is "attention" in a Transformer?

Skip this section if you already know.

A **Transformer** is a neural network architecture introduced in
*Attention Is All You Need* (Vaswani et al., NeurIPS 2017). Every
modern large language model (GPT, BERT, LLaMA, Claude, Gemini,
Mistral, …) is built from Transformer blocks. The arithmetic
backbone of every such block is a single operation called
**scaled dot-product attention** (SDPA):

$$
\mathrm{Attention}(Q, K, V) \;=\; \mathrm{softmax}\!\left( \frac{Q K^{\top}}{\sqrt{d_k}} \right) V
$$

The inputs are three matrices, conventionally named:

| matrix | shape | conceptual role |
|--------|-------|-----------------|
| `Q` (query) | `[N × d]` | "what am I looking for?" |
| `K` (key)   | `[N × d]` | "what features does each token expose?" |
| `V` (value) | `[N × d]` | "what content does each token carry?" |

`N` is the sequence length (e.g., the number of tokens in the
context), and `d` (often called `d_k` or `d_model`) is the
per-head embedding dimension. The computation has four arithmetic
phases:

1. **`S = Q · Kᵀ`** — pairwise dot products between every query and
   every key. Triple-nested loop (`i, j, k`), `O(N²d)` work.
2. **`S /= √d`** — scale, to keep the magnitudes well-behaved before
   the exponential in step 3. At `-O2`, GCC pre-computes `1/√d` as a
   floating-point constant and folds it into step 1.
3. **`S = softmax(S)`** — row-wise: subtract the row maximum (for
   numerical stability), exponentiate, divide by the row sum. Uses
   `expf` and a division.
4. **`O = S · V`** — another triple-nested matrix product, again
   `O(N²d)`.

A naïve C implementation looks like the file `finale.c` in this
repository: four loop nests, a few hundred GIMPLE statements after
expansion, a transcendental function call, and a floating-point
division. SDPA is the *single most expensive operation* in inference
and training of large models, so making it cheaper has out-sized
impact on data-centre cost and energy.

The conceptual proposition of this project is:

> Replace the entire SDPA computation with one machine instruction.
> Hide the cost behind a hardware accelerator that the CPU dispatches
> to. Expose the accelerator through a single opcode the compiler
> emits automatically.

The accelerator itself is out of scope here; the *compiler-side
plumbing* to expose it is exactly what the rest of this repository
implements.

---

## 7. GIMPLE, SSA, and the GCC pass pipeline

Most of the engineering of this project lives inside GCC's
*middle end*, so it pays to spend a few paragraphs introducing the
relevant concepts.

### 7.1 GIMPLE

When GCC parses your C code, it does *not* compile it directly to
assembly. Instead it produces an internal representation called
**GIMPLE** — essentially a simplified C-like language where every
expression has been broken into three-address form (no nested
sub-expressions), every conditional is explicit, and the
control-flow graph is fully built.

Source C:

```c
scores[i*N + j] += Q[i*d + k] * K[j*d + k];
```

Approximate GIMPLE:

```
_1 = i * d;
_2 = _1 + k;
_3 = Q[_2];
_4 = j * d;
_5 = _4 + k;
_6 = K[_5];
_7 = _3 * _6;             // MULT_EXPR
_8 = scores[i*N + j];
_9 = _8 + _7;             // PLUS_EXPR
scores[i*N + j] = _9;
```

Every line does exactly one thing. This regularity is what makes
pattern matching tractable.

### 7.2 SSA — Static Single Assignment

GIMPLE in GCC is in *SSA form*: every named temporary is assigned
**exactly once**. If your source code reassigns a variable in two
places, GCC creates two distinct SSA names (`x_1`, `x_2`, …). At
control-flow merge points (after an `if`/`else`, at the top of a
loop), GCC inserts **φ-nodes** (phi-nodes) that say "this value is
either `x_1` (if we came from the then-branch) or `x_2` (if we came
from the else-branch)".

The reason this matters here is *def-use chains*. Given any SSA name
in a statement, a single function call (`SSA_NAME_DEF_STMT`) returns
the unique GIMPLE statement that defined it. The pass uses this to
walk *backwards* from a multiplication to ask "did both operands
come from array loads?" — i.e., to recognise the dot-product idiom
of Q×Kᵀ.

### 7.3 The pass pipeline

GCC's middle end runs **hundreds of optimisation passes** over the
GIMPLE IR. Each pass is a `gimple_opt_pass` object with an `execute`
method, and the order in which they run is fixed by a giant table
in `gcc/gcc/passes.def`. Our pass is registered in that table at
position 179, immediately after the **Graphite** polyhedral
loop-transform block.

The exact insertion point matters:

* Earlier than this, loops are not yet in canonical form (the loop
  tree, single-exit, and SCEV trip counts may not be available).
* Much later, GCC has lowered the IR enough that the high-level
  `expf` calls and array-of-`float` loads are no longer easily
  recognisable.

Position 179 is the sweet spot where loops are clean but still
high-level. The detailed reasoning is in
[`02-compiler-pass.md` §3](02-compiler-pass.md#3-where-the-pass-runs-and-why).

### 7.4 SCEV — scalar evolution

When the pass needs to know "how many times does this loop run?",
it queries the **scalar evolution** subsystem (SCEV) which models
how each integer SSA name grows along the back-edge of a loop. The
function `number_of_latch_executions(loop)` returns either a
GIMPLE-tree expression for the trip count or the special tree
`chrec_dont_know`. The pass rejects any loop whose trip count it
cannot reason about — that is one of its safety checks.

### 7.5 RTL and `define_insn`

After all the GIMPLE passes finish, GCC *expands* GIMPLE into
**RTL** (Register Transfer Language) — a lower IR where the target
ISA is starting to peek through. Our `IFN_RISCV_ATTN` internal
function call is expanded by a small C++ helper
(`expand_RISCV_ATTN` in `internal-fn.cc`) into a call to
`gen_riscv_attn`, which is automatically generated by GCC from the
**`define_insn "riscv_attn"`** pattern in `riscv.md`. That pattern
is what eventually prints `attn\t%0,%1,%2,%3` into the assembly
output.

---

## 8. Putting it all together

After this background, the rest of the documentation should read
much more naturally. The high-level story is:

1. **Encoding (binutils)** — we picked the `custom-0` opcode slot,
   chose `funct3 = funct2 = 0`, and registered `attn` as an R4-type
   instruction so that the assembler and `objdump` both know it
   (see [`04-patches-and-files.md`](04-patches-and-files.md), files 1–2).
2. **Backend (GCC RTL)** — we declared an `UNSPEC` and a
   `define_insn` so the compiler knows how to *print* the assembly
   for `attn` (file 4).
3. **Internal function (GCC middle end)** — we declared
   `IFN_RISCV_ATTN` and wrote a one-screen expander that lowers it
   to RTL (files 5–6).
4. **Pattern recognition (GCC middle end)** — the `attnrec` pass
   walks every loop, checks five matching conditions, and replaces
   the matched nest with an `IFN_RISCV_ATTN` call (file 11; deeply
   covered in [`02-compiler-pass.md`](02-compiler-pass.md)).
5. **Build glue and the `-mattn` flag** — the glue files (`passes.def`,
   `tree-pass.h`, `Makefile.in`, `riscv.opt`, `riscv.cc`) wire the
   new pass into GCC's build and gating logic (files 3, 7–10).

With that mental model, the rest of the documentation set is just
detail.

---

**Next:** [`01-instruction-spec.md`](01-instruction-spec.md)
— the exact specification of the `attn` instruction.
