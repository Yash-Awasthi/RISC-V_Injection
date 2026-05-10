# 08 — Glossary

> **Audience.** Readers who need a quick definition of any term used
> elsewhere in this documentation set without leaving the repo. The
> entries are arranged alphabetically; cross-references point to
> the document where the concept is used in depth.

---

## A

**ABI (Application Binary Interface).** The set of conventions —
register usage, calling conventions, struct layout — that compiled
code on a given platform must follow. The RISC-V ABI tested in
this project is `lp64d`: 64-bit `long` and pointers, doubles passed
in floating-point registers.

**Accelerator.** A piece of hardware specialised for one workload
(e.g. matrix multiply, attention, FFT). In this project, the
hypothetical hardware behind the `attn` instruction is an
accelerator; it is out of scope for the current artefact.

**`attn`.** The custom RISC-V instruction defined by this project.
R4-type, 32 bits, opcode-slot `custom-0`. Specified in detail in
[`01-instruction-spec.md`](01-instruction-spec.md).

**`attnrec`.** The name of the GIMPLE pass that recognises SDPA and
emits `IFN_RISCV_ATTN`. Lives in `gcc/gcc/tree-ssa-attn.cc`.
See [`02-compiler-pass.md`](02-compiler-pass.md).

**Attention (in deep learning).** The operation
`softmax(QKᵀ/√d)V`. Core component of every Transformer block.
Background in
[§6 of `00-background.md`](00-background.md#6-what-is-attention-in-a-transformer).

## B

**Basic block.** A maximal sequence of consecutive statements in
GIMPLE that has a single entry and a single exit. The unit GCC's
control-flow graph is built from.

**Binutils.** The GNU package containing the assembler (GAS),
linker (LD), and object utilities (`objdump`, `nm`, `ar`, …).
This project modifies two binutils files (Files 1 and 2 in
[`04-patches-and-files.md`](04-patches-and-files.md)).

**Builtin (GCC).** A function name like `__builtin_riscv_attn`
that the compiler recognises specially and lowers directly to
machine code, skipping any actual function-call mechanism. The
current project deliberately does **not** expose `attn` as a
builtin; idiom recognition is used instead.

## C

**CFG (Control-Flow Graph).** The graph of basic blocks (nodes)
and possible branches (edges) that GCC builds for every function.
Manipulating the CFG is how passes redirect or eliminate code.

**custom-0 / custom-1 / custom-2 / custom-3.** The four 7-bit
opcode values reserved by the RISC-V specification for
implementer-defined instructions. `attn` lives in `custom-0`
(`opcode[6:0] = 0x0b`).

**Cross-compiler.** A compiler that runs on one architecture
(your x86 laptop) but produces code for another (RISC-V). The
toolchain produced by this project is a cross-compiler:
`riscv64-unknown-elf-gcc`.

## D

**DCE (Dead-Code Elimination).** The GCC optimisation that removes
statements whose results are not used. Without precautions, DCE
will eliminate an `IFN_RISCV_ATTN` call whose return value is
unused. The pass marks the call **volatile** to prevent this
(see [Issue 6](05-troubleshooting.md#issue-6--ice-in-propagate_necessity-dce)).

**DECLARE_INSN.** A binutils macro that registers an instruction in
the disassembler's table. Must appear inside the
`#ifdef DECLARE_INSN` guard in `riscv-opc.h`.

**`define_insn`.** The RTL pattern in `riscv.md` that tells GCC
how to print an assembly instruction. Each `define_insn` has
operands, a predicate, an assembly template, and attributes
(`type`, `mode`).

**Disassembler.** The program (`objdump -d`) that turns binary
machine code back into readable assembly. Uses the `MASK` / `MATCH`
constants we defined to recognise our instruction.

**DI / SI / DF / SF (RTL modes).** Shorthand for "double-integer"
(64-bit), "single-integer" (32-bit), "double-float", "single-float".
Pmode is `DI` on RV64 and `SI` on RV32. Used in `riscv.md`.

## E

**`-mattn`.** The compiler flag added by this project. Defines the
preprocessor macro `TARGET_ATTN`. Gates the `attnrec` pass and the
`define_insn`'s predicate. See
[§8 of `01-instruction-spec.md`](01-instruction-spec.md#8-the--mattn-compile-time-flag).

**ECF_LEAF / ECF_NOTHROW.** GCC call-property flags. `ECF_LEAF`
asserts the call does not access caller's memory. `ECF_NOTHROW`
asserts it cannot raise a C++ exception. The IFN declaration uses
`ECF_NOTHROW` only — `ECF_LEAF` would conflict with the volatile
flag and trigger a DCE ICE.

**ELF (Executable and Linkable Format).** The standard binary
format on Linux and on most bare-metal RISC-V boards. The output
format produced by `riscv64-unknown-elf-gcc`.

## F

**`sdpa_test.c`.** The reference SDPA implementation in this
repository, written as a fused outer loop. The file the matcher is
known to recognise.

**FlashAttention.** Optimised GPU kernel for SDPA by Dao et al.
(NeurIPS 2022). Influences the discussion of accelerator design;
not directly used in this project.

**`fmadd`.** The standard RISC-V floating-point fused multiply-add
instruction. Same R4-type format as `attn`. Useful as a "this
encoding shape already exists in the standard ISA" reference.

**funct2 / funct3 / funct7.** Sub-fields within a 32-bit RISC-V
instruction word that further classify an opcode. `attn` uses
`funct3 = 0`, `funct2 = 0` to identify itself within the
`custom-0` slot.

## G

**GAS (GNU Assembler).** The `as` program in binutils. Reads the
opcode table populated in File 2 of
[`04-patches-and-files.md`](04-patches-and-files.md).

**GCC.** The GNU Compiler Collection. The C compiler this project
modifies. Version 15.2.0.

**`gcall`.** GCC's GIMPLE call statement. The pass builds one of
these for `IFN_RISCV_ATTN`.

**Gate (of a pass).** The boolean predicate, on a `gimple_opt_pass`,
that decides whether the pass runs at all on a given function. The
`attnrec` gate requires `TARGET_ATTN`, optimisation ≥ 2, and loop
optimisation enabled.

**GIMPLE.** GCC's machine-independent intermediate representation
(IR). A simplified form of C with three-address statements, an
explicit CFG, and SSA names. The pass operates on GIMPLE.

**Graphite.** GCC's polyhedral loop-optimisation framework. The
`attnrec` pass is registered immediately *after* the Graphite
block in `passes.def`.

## H

**Hardware/software co-design.** The methodology of designing a
hardware accelerator and its compiler/runtime support together,
each informing the other. This project realises the
"compiler" arm of that methodology for `attn`.

## I

**IR (Intermediate Representation).** A representation of code
between source and target machine code. GCC has three: AST,
GIMPLE, and RTL.

**IFN (Internal Function).** A GCC mechanism for declaring an
abstract operation that has a fixed expansion to RTL but no
language-level form. Declared in `internal-fn.def`, expanded in
`internal-fn.cc`. The pass emits `IFN_RISCV_ATTN`.

**ICE (Internal Compiler Error).** GCC's term for "the compiler
itself crashed". An ICE is always a bug in the compiler, never
just a user error. See
[`05-troubleshooting.md`](05-troubleshooting.md) for the ICEs
encountered by this project.

**ISA (Instruction Set Architecture).** The contract between
hardware and software, listing every machine instruction and its
encoding. RISC-V's ISA reserves four custom slots; this project
uses one.

## L

**LP64D.** The RISC-V calling convention used in this project:
**L**ong and **P**ointer = **64** bits, **D**oubles in
floating-point registers.

## M

**Machine Description (MD).** GCC's term for the file
`config/<target>/<target>.md` (here, `riscv.md`) that describes
how to print machine instructions. Holds `define_insn` patterns.

**`MATCH` / `MASK`.** A pair of 32-bit constants by which a
disassembler identifies an instruction:
`(insn & MASK) == MATCH` means *this* is the instruction. For
`attn`, `MATCH = 0x0000000b`, `MASK = 0x0600707f`.

**`mem:BLK`.** An RTL operand whose mode is "block of memory of
unknown size". Used in `define_insn` to declare that an operand
points to memory the optimiser may not reorder around.

**Mnemonic.** The human-readable name of an instruction (e.g.
`attn`, `add`, `fmadd`). The `riscv-opc.c` table maps mnemonics to
encodings.

## N

**Newlib.** A small C standard library used for bare-metal targets
(no operating system). The toolchain in this repository is a
Newlib toolchain.

## O

**`objdump`.** The binutils tool that disassembles object files.
Uses the `MASK` / `MATCH` table to recognise `attn`.

**Opcode.** Strictly: the 7-bit `opcode[6:0]` field of a 32-bit
RISC-V instruction word that picks the major instruction class.
Loosely: the entire bit pattern that identifies an instruction.
This project uses both senses depending on context.

## P

**Pass (compiler pass).** A function or class object that walks
the IR and either analyses or transforms it. GCC has hundreds of
GIMPLE passes; `attnrec` is one of them.

**Pmode.** The "pointer mode" — the RTL mode of a pointer-sized
register. `DImode` on RV64, `SImode` on RV32. Used by the IFN
expander to materialise its arguments.

**Preheader.** The unique basic block immediately before the
header of a loop. The pass inserts the `IFN_RISCV_ATTN` call into
the preheader of the matched outer loop.

## Q

**Q, K, V (Query, Key, Value).** The three input matrices of an
attention layer. `attn` takes pointers to these in `rs1`, `rs2`,
`rs3`.

## R

**R-type / R4-type / I-type / S-type / U-type / J-type / B-type.**
The seven RISC-V instruction-format families. R4-type is the only
format with four register operands; `attn` uses it.

**`rd`, `rs1`, `rs2`, `rs3`.** Register-number fields in a 32-bit
RISC-V instruction word. `rd` is the destination; `rs1`–`rs3` are
sources. For `attn`, `rd = O`, `rs1 = Q`, `rs2 = K`, `rs3 = V`.

**RoCC (Rocket Custom Coprocessor).** UC Berkeley's interface for
attaching accelerators to a Rocket Chip RISC-V core. Mentioned as
a candidate target for Phase 4's hardware implementation.

**Row-major.** Memory layout where consecutive elements of a row
are adjacent in memory. The matrices passed to `attn` are
row-major.

**RTL (Register Transfer Language).** GCC's lower IR, closer to
the target machine. The IFN expander lowers GIMPLE into RTL.

## S

**SCEV (Scalar Evolution).** GCC's analysis subsystem that models
how loop-induction variables evolve. The pass calls
`number_of_latch_executions(loop)` to query trip counts.

**SDPA (Scaled Dot-Product Attention).** The mathematical
operation `softmax(QKᵀ/√d)V`. The operation `attn` represents.

**Softmax.** Element-wise `exp` followed by row-wise normalisation
by the row sum. Pattern detector looks for an `expf` call and a
floating-point division.

**Spike.** The RISC-V reference instruction-set simulator.
`riscv-isa-sim`. Phase 4 of this project will implement the
semantics of `attn` here.

**SSA (Static Single Assignment).** The form GIMPLE is in. Every
named temporary is assigned exactly once. Enables one-step
def-use chains via `SSA_NAME_DEF_STMT`.

**`type "ghost"`.** An RTL `type` attribute meaning "this insn is
a scheduling barrier with no DFA reservation". Used by the
`define_insn` for `attn` to keep the RISC-V scheduler from
asserting on an unknown pipeline class.

## T

**Target triplet.** The three-part name like
`riscv64-unknown-elf` that identifies a cross-compiler target.

**TARGET_ATTN.** The C macro defined when `-mattn` is given,
declared by `riscv.opt`. Used to gate the pass and the
`define_insn`.

**Toolchain.** The set of programs (compiler, assembler, linker,
library, debugger) that together turn source code into runnable
binaries. This project modifies the GCC + binutils portions.

**Top-level loop.** A loop whose immediate parent in GCC's loop
tree is the function root — that is, a loop not nested inside
another loop. The matcher considers only top-level loops.

**Transformer.** The neural-network architecture introduced by
Vaswani et al. (NeurIPS 2017). The reason attention matters.

**Trip count.** The number of times a loop iterates. Queried via
SCEV's `number_of_latch_executions`. The matcher rejects any
loop whose trip count SCEV cannot reason about.

## U

**UNSPEC (Unspecified).** GCC's RTL category for opaque,
target-defined operations the optimiser must not simplify. The
project declares `UNSPEC_RISCV_ATTN` and uses it in the
`define_insn`.

## V

**Volatile (GIMPLE).** A flag set by `gimple_set_has_volatile_ops`
that tells GCC the statement has arbitrary observable side
effects. Set on the `IFN_RISCV_ATTN` call to keep DCE from
eliminating it.

## X

**XLEN.** RISC-V's term for "the natural integer width of this
configuration". `XLEN = 32` for RV32; `XLEN = 64` for RV64. The
`attn` encoding is XLEN-agnostic; the pass has only been validated
on RV64.

---

**End of documentation set.**

Return to the [README](../README.md) for the high-level overview,
or jump to any document via the suggested reading orders there.
