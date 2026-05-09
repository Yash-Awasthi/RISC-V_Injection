# `attn` — A Custom RISC-V Instruction for Transformer Attention

> A modified `riscv-gnu-toolchain` that teaches GCC to recognise the
> scaled dot-product attention (SDPA) pattern in ordinary C code and
> lower it to a single hardware instruction — **without inline
> assembly, without `__builtin_*` intrinsics, and without `.insn`
> directives**.

---

## At a glance

| Item | Value |
|------|-------|
| Mnemonic | `attn` |
| Format | R4-type (4 register operands, like `fmadd`) |
| Opcode slot | `custom-0` (`opcode[6:0] = 0x0b`) |
| MATCH / MASK | `0x0000000b` / `0x0600707f` |
| Compiler flag | `-mattn` |
| GCC version | 15.2.0 (riscv-gnu-toolchain fork) |
| Binutils version | 2.46 |
| Pass position | #179 in the GIMPLE pipeline (after Graphite) |
| Internal function | `IFN_RISCV_ATTN` |
| Status | Toolchain-side complete; hardware/simulator semantics are future work |

The instruction is the compiler-visible counterpart of the operation
implemented by every Transformer self-attention block:

$$
\text{Attention}(Q, K, V) \;=\; \mathrm{softmax}\!\left(\frac{Q K^{\top}}{\sqrt{d_k}}\right) V
$$

A plain C implementation of this expression — four loop nests, an
`expf` call, a division, several thousand multiply-adds — is reduced
by the compiler to a single 32-bit machine word:

```asm
attn  a3, a0, a1, a2     # rd = O,  rs1 = Q,  rs2 = K,  rs3 = V
```

---

## What this project actually contains

This repository is a fork of [`riscv-gnu-toolchain`](https://github.com/riscv-collab/riscv-gnu-toolchain).
On top of the upstream sources, it adds:

1. **A new GIMPLE optimisation pass** (`attnrec`,
   `gcc/gcc/tree-ssa-attn.cc`, ~500 lines) that walks every loop nest
   in the function being compiled and decides whether it implements
   SDPA.
2. **A new GCC internal function** `IFN_RISCV_ATTN` and its expander
   (`gcc/gcc/internal-fn.def`, `gcc/gcc/internal-fn.cc`).
3. **A new RTL pattern** (`define_insn "riscv_attn"` in
   `gcc/gcc/config/riscv/riscv.md`) that emits the assembly mnemonic.
4. **A new compiler flag** `-mattn` (`gcc/gcc/config/riscv/riscv.opt`).
5. **A new binutils opcode table entry** so that GAS can encode `attn`
   and `objdump` can disassemble it
   (`binutils/include/opcode/riscv-opc.h`,
   `binutils/opcodes/riscv-opc.c`).

The deliberate constraint of the project is that **the user’s C code
is unchanged**. There is no header to include, no intrinsic to call,
no inline-asm block to write. The detection happens automatically as
part of normal `-O2` compilation, gated only by `-mattn`.

---

## The "_new" documentation set

The original `docs/` folder contains terse engineering notes intended
for the implementer. The files below (all sitting next to this README,
all suffixed `_new` so they do not collide with the existing tree)
re-tell the same story for two new audiences: a CS undergraduate who
has never touched a compiler before, and a research supervisor who
needs the depth and the citations.

| File | Audience | Read it for |
|------|----------|-------------|
| [`README_new.md`](README_new.md) | everyone | this overview |
| [`00_BACKGROUND_new.md`](00_BACKGROUND_new.md) | undergraduate | RISC-V, attention, the GCC pipeline, GIMPLE/SSA, custom instructions — from zero |
| [`01_INSTRUCTION_SPEC_new.md`](01_INSTRUCTION_SPEC_new.md) | undergraduate + supervisor | the ISA-level specification of `attn` (encoding, semantics, ABI, worked decoding) |
| [`02_COMPILER_PASS_new.md`](02_COMPILER_PASS_new.md) | undergraduate + supervisor | the `attnrec` pass — how it detects SDPA and emits the instruction |
| [`03_BUILD_AND_RUN_new.md`](03_BUILD_AND_RUN_new.md) | undergraduate | how to build the toolchain and verify each layer |
| [`04_PATCHES_AND_FILES_new.md`](04_PATCHES_AND_FILES_new.md) | undergraduate + supervisor | every file that changed, the exact diff, and *why* |
| [`05_TROUBLESHOOTING_new.md`](05_TROUBLESHOOTING_new.md) | implementer | every ICE / build error encountered, with root cause and fix |
| [`06_EXTENDING_TOOLCHAIN_new.md`](06_EXTENDING_TOOLCHAIN_new.md) | researcher | template for adding *any* new RISC-V custom instruction |
| [`07_RESEARCH_CONTEXT_new.md`](07_RESEARCH_CONTEXT_new.md) | supervisor | related work, novelty claim, limitations, future research |
| [`08_GLOSSARY_new.md`](08_GLOSSARY_new.md) | undergraduate | every acronym and term used in this repo, defined |

The original `docs/*.md` files are preserved unchanged; the `_new`
files are intended to supersede them when readers want a more
self-contained reading experience.

---

## Suggested reading order

**If you have ~30 minutes and want the gist**

1. This README.
2. [`00_BACKGROUND_new.md`](00_BACKGROUND_new.md) §1–§4
   (just enough RISC-V and just enough attention).
3. [`01_INSTRUCTION_SPEC_new.md`](01_INSTRUCTION_SPEC_new.md) §1–§3.
4. [`02_COMPILER_PASS_new.md`](02_COMPILER_PASS_new.md) §1–§4
   (the high-level idea of the pass).

**If you are evaluating this as research output**

1. [`07_RESEARCH_CONTEXT_new.md`](07_RESEARCH_CONTEXT_new.md) (positioning).
2. [`02_COMPILER_PASS_new.md`](02_COMPILER_PASS_new.md) (the contribution).
3. [`01_INSTRUCTION_SPEC_new.md`](01_INSTRUCTION_SPEC_new.md) (the artefact).
4. [`05_TROUBLESHOOTING_new.md`](05_TROUBLESHOOTING_new.md) (depth of engagement with GCC internals).

**If you want to reproduce the build**

1. [`03_BUILD_AND_RUN_new.md`](03_BUILD_AND_RUN_new.md).
2. [`05_TROUBLESHOOTING_new.md`](05_TROUBLESHOOTING_new.md) when something
   inevitably goes wrong.

---

## What the result looks like

Compiling a clean C implementation of fused SDPA (`finale.c`) with
the modified toolchain:

```bash
$HOME/riscv-install/bin/riscv64-unknown-elf-gcc \
    -mattn -O2 \
    -fno-schedule-insns -fno-schedule-insns2 \
    -S finale.c -o finale.s
```

produces, among the usual prologue/epilogue, a single line:

```asm
        attn    a3,a0,a1,a2
```

That one instruction stands in for what is otherwise hundreds of
lines of unrolled vectorised loop body. The original loops are still
present in the output — the compiler matched the pattern but did not
*prove* the hardware is semantically equivalent — see
[§7 of `02_COMPILER_PASS_new.md`](02_COMPILER_PASS_new.md#7-why-the-loop-body-stays-and-what-removing-it-would-take)
for why this is the correct behaviour and how to take the next step.

---

## Project status and what is *not* in this repository

| Layer | Status | Where it lives |
|-------|--------|----------------|
| Toolchain-side encoding (assembler / disassembler) | Complete | this repo |
| Compiler pattern matching (`attnrec` pass) | Complete | this repo |
| RTL / IR plumbing (IFN, define_insn, expander) | Complete | this repo |
| Hardware semantics in a simulator (Spike) | **Not done** — Phase 4 | future work |
| Synthesisable RTL (Verilog/Chisel) for an accelerator | Out of scope | future work |
| Equivalence proof / verified loop deletion | Out of scope | future work |

The contribution of this project is therefore precisely the
**software-side custom-instruction infrastructure**, demonstrated end
to end on a non-trivial computation. The hardware accelerator is the
natural next step in a hardware/software co-design pipeline; see
[§5 of `07_RESEARCH_CONTEXT_new.md`](07_RESEARCH_CONTEXT_new.md#5-future-work).

---

## Author and credits

* **Author:** Yash Awasthi
* **Upstream toolchain:** `riscv-gnu-toolchain` (RISC-V International)
* **Compiler:** GCC 15.2.0
* **Binutils:** 2.46
* **Host platform tested:** Ubuntu 24.04 / WSL2

Bug reports and questions are welcome via the repository issue tracker.

---

## License

The toolchain sources retain their original licenses (GPLv3 for GCC,
GPLv3 / LGPL for binutils, etc.). The project-specific additions
(`tree-ssa-attn.cc`, the `_new` documentation files, and the test
input `finale.c`) are released under the same license as the
component they extend.
