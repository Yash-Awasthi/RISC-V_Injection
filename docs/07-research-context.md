# 07 — Research Context, Novelty, and Future Work

> **Audience.** A research supervisor, thesis examiner, or
> conference reviewer trying to place this project on the map of
> existing work in custom-instruction toolchains, accelerator
> design, and compiler-driven specialisation. Also useful as the
> starting outline for a write-up or paper draft.

This document does *not* re-explain the engineering — it positions
it. The relevant engineering details live in
[`01-instruction-spec.md`](01-instruction-spec.md),
[`02-compiler-pass.md`](02-compiler-pass.md), and
[`04-patches-and-files.md`](04-patches-and-files.md).

---

## Table of contents

1. [Problem statement and motivation](#1-problem-statement-and-motivation)
2. [Related work](#2-related-work)
3. [Comparison with the earlier prototype](#3-comparison-with-the-earlier-prototype)
4. [Contribution and novelty claim](#4-contribution-and-novelty-claim)
5. [Future work](#5-future-work)
6. [Limitations and threats to validity](#6-limitations-and-threats-to-validity)
7. [Suggested evaluation plan](#7-suggested-evaluation-plan)
8. [Suggested paper outline](#8-suggested-paper-outline)
9. [Reading list and primary sources](#9-reading-list-and-primary-sources)

---

## 1. Problem statement and motivation

Inference and training of Transformer language models is dominated,
in both energy and wall-clock time, by **scaled dot-product
attention** (SDPA): the operation
$\text{softmax}(QK^{\top}/\sqrt{d_k})V$.
Industrial accelerators address this by exposing fused primitives
(NVIDIA's `cuBLASLt + cuDNN` GEMM-softmax fusion, Intel's AMX,
Google's TPU "MatMul + Reduce" tiles, the FlashAttention CUDA
kernels), but the path from *plain C source code* to *one custom
machine instruction* is, in the open ecosystem, still mostly
manual: programmers must call vendor-specific intrinsics, write
inline assembly, or invoke proprietary libraries.

This project asks the orthogonal question:

> **Given an unmodified, plain-C implementation of SDPA, can the
> compiler — by recognising a syntactic idiom — replace the entire
> computation with a single custom RISC-V instruction, with no
> source changes, no intrinsics, and no inline assembly?**

The answer demonstrated in this repository is *yes*, at least for
the toolchain side (assembler, disassembler, GCC middle and back
end). The instruction is reserved on a real opcode slot, the
compiler emits it on `-mattn -O2`, and the GIMPLE dump documents
every match decision. The *hardware* side — implementing the
instruction's semantics in a simulator and then in synthesisable
RTL — is identified as future work in §5.

The research interest of the toolchain-only result is twofold:

* It exercises every layer of GCC's middle/back end (GIMPLE pass,
  IFN, RTL `define_insn`, scheduler attribute, command-line flag,
  binutils opcode registry) on a non-trivial transformation. The
  resulting infrastructure is a usable substrate for *any* future
  fused primitive (LayerNorm, RMSNorm, rotary embeddings, fused
  feed-forward), as templated in
  [`06-extending-toolchain.md`](06-extending-toolchain.md).
* It demonstrates idiom-recognition feasibility for a workload an
  order of magnitude more structured than the standard examples
  (`memcpy`, `strlen`, dot-product). The matcher imposes five
  simultaneous conditions on a function before rewriting; the
  conditions are stated in a way that survives `-O2`'s
  aggressive constant folding, scalar evolution, and partial loop
  fusion / fission.

---

## 2. Related work

The relevant literature falls into four buckets.

### 2.1 RISC-V custom-instruction methodology

The RISC-V specification has reserved four "custom" opcode slots
since the 2014 base ISA draft, and tutorials for adding instructions
to the GNU toolchain have circulated since at least 2017
(Patterson & Waterman, *The RISC-V Reader*; UC Berkeley CS152
labs; the original Rocket Chip / RoCC documentation). These works
typically illustrate the binutils + GCC plumbing for a *trivial*
instruction such as a custom `xor` or a "double-this-register"
opcode. They establish what the project here also relies on:
opcode-slot reservation, the binutils opcode table, and GCC's
`define_insn`. They do **not** show automatic idiom recognition.

The Chipyard / RoCC ecosystem (Asanović et al., UC Berkeley),
Esperanto, and SiFive's vendor extensions provide accelerator-style
custom instructions but expose them via intrinsics rather than via
compiler-side pattern matching.

### 2.2 Idiom recognition in compilers

Recognising high-level idioms in low-level IR has a long history:

* GCC's `tree-ssa-strlen` recognises `strlen` and related string
  loops.
* GCC's `tree-loop-distribution` rewrites memcpy/memset-shaped
  loops into library calls.
* `tree-ssa-math-opts` recognises `popcount`, `clz`, `ctz`, and
  reciprocal-square-root idioms.
* LLVM's `LoopIdiomRecognize` handles similar transformations.
* The polyhedral school (Pluto, isl, Polly, Graphite) recognises
  *affine* loops and re-tiles them but does not synthesise new ISA
  primitives.

These passes inform the design of `attnrec` — most directly, the
pattern of "five conjunctive checks gated on a target flag, with a
GIMPLE dump for every decision" is borrowed from
`tree-ssa-strlen.cc`. The novelty here is in the *target idiom*:
a four-phase mathematical operation involving a transcendental
function and a division, expressed across multiple loop nests, is
considerably more structured than `strlen` or `popcount`.

### 2.3 Specialised hardware for attention

Attention-specific accelerators have proliferated:

* FlashAttention (Dao et al., NeurIPS 2022; FlashAttention-2 / 3)
  optimises attention at the kernel level on existing GPUs.
* TPU `MXU` and Apple's *Neural Engine* expose fused matmul + softmax
  primitives, but only via vendor libraries.
* Academic SoCs — notably *Hardware Architecture for Transformer*
  (Tambe et al., Hot Chips 2023), *FACT* (HPCA 2023), *Sanger*
  (MICRO 2021) — propose dedicated attention engines with custom
  instructions, but the published descriptions are all hardware
  papers and stop at "the ISA includes an `attn` instruction".
  How the compiler reaches it is left as engineering detail.

This project sits on the toolchain side of that gap: it shows that
the existing free-and-open GCC + binutils stack is *sufficient* to
expose such an instruction without source-level intervention, given
a conventional GIMPLE pass.

### 2.4 Hardware/software co-design pipelines

The recipe of "spec → simulator → RTL → silicon, with the compiler
threaded through every stage" is canonical in computer-architecture
research (the "Y-chart" methodology, the *gem5+Spike* workflow, the
*Chipyard / FireSim* methodology). This project realises the
"compiler" leg of that recipe end-to-end while leaving the
"simulator" leg (Phase 4) and "RTL" leg explicitly to future work.

---

## 3. Comparison with the earlier prototype

A predecessor of this repository
(`Yash-Awasthi/riscv-gnu-toolchain`, the `custom_attn/` subtree)
implemented an earlier version of `attn`. Documenting the
differences makes the trajectory of design decisions clear.

| Aspect | Earlier prototype | Current project (`Yash-Awasthi/CD`) |
|--------|-------------------|--------------------------------------|
| Format | **R-type** (3 register operands) | **R4-type** (4 register operands) |
| Operand mechanism | Two stack-allocated descriptor structs (`dims`, `qkv`) passed through `rs1`, `rs2` | Four direct pointers in `rd, rs1, rs2, rs3` |
| MATCH / MASK | `0x0200000b` / `0xfe00707f` | `0x0000000b` / `0x0600707f` |
| Programmer interface | Builtin `__builtin_riscv_attn(dims, qkv)` *and* automatic detection | Builtin `__builtin_riscv_attn(o, rs1, rs2, rs3)` (primary, non-experimental path, see `demo/attn.h`) *and* automatic detection (`-mattn-recognize`, experimental, off by default) — no inline asm, no `.insn` in either path |
| Emission mechanism | The pass synthesised `volatile asm` with `.insn r 0x0b, 0, 0x01, x0, %0, %1` and a `"memory"` clobber | The builtin call maps directly onto the `define_insn` via ordinary builtin expansion — no internal function, no hand-written expander. (An earlier revision of this project routed the recognizer through `IFN_RISCV_ATTN`; that internal function has since been removed in favour of this direct builtin call.) |
| Pass position | After the "loop" pass | After Graphite's `POP_INSERT_PASSES()` (position 179) |
| Loop fusion requirement | Required all four phases as separate top-level loops | Requires fused source (one outer `i`-loop) and scans whole function for load bases |
| Loop-body removal | Pass redirected control flow past all four loops via `split_edge` + `redirect_edge_and_branch` | Pass leaves the body intact; deletion is deferred to Phase 4 (post-equivalence) |
| Matcher condition count | 4-stage window, each phase had its own detector | 5 unified conjunctive checks |

The current design removes the inline-assembly escape hatch (the
project's original task explicitly forbade it) and integrates with
GCC's ordinary builtin-expansion pipeline (builtin call → RTL →
assembly via `define_insn`, exactly like a standard ISA instruction,
with no internal function in between), and it keeps the user's loop
body intact pending the equivalence proof that a future Phase 4 will
provide. It also brings back an explicit builtin — dropped from the
"automatic detection only" framing this section originally described
— now with a documented block-ABI (`attn_ptrs`/`attn_dims`/`attn_cfg`,
[§4 of `01-instruction-spec.md`](01-instruction-spec.md#4-operand-convention-and-abi))
instead of the earlier prototype's two descriptor structs, and gated
as the primary, non-experimental path while automatic recognition is
now the opt-in, experimental one. The cost is that the matcher is
more cautious, and some perfectly valid hand-written attention loops
(e.g. the unfused four-loop style) are not matched without
source-side adaptation — and, independent of recall, the matcher's
soundness is not proven: see the known false positive documented in
[`../demo/failures/README.md`](../demo/failures/README.md#known-false-positive-fail_scattered-signature-known-false-positivec)
and `plan.md`.

---

## 4. Contribution and novelty claim

A precise statement of the contribution:

> **This project demonstrates that the entire scaled dot-product
> attention computation, written as plain C code without any
> annotations, can be reduced by GCC to a single custom RISC-V
> instruction via a self-contained GIMPLE pass and an internal
> function — using only mechanisms that are part of upstream GCC
> and binutils, and without any inline assembly, intrinsic, or
> `.insn` directive in either the user's source or the compiler's
> emission path.**

Three components of this claim are individually unremarkable in the
literature; the **combination** is, to the author's knowledge, new:

* Adding a custom RISC-V instruction to GCC + binutils (well-known).
* Recognising a high-level idiom in GIMPLE and rewriting it
  (well-known, used for `strlen`, `popcount`, etc.).
* Routing a recognised idiom through GCC's *internal-function*
  machinery into a `define_insn` rather than into volatile inline
  assembly (less common; the natural-feeling but rarely
  demonstrated approach for opaque opcodes).

The closest published descriptions either demonstrate (a) a custom
instruction exposed via an intrinsic, or (b) idiom recognition that
emits a *library call*, not a target instruction. The
combination of (a) + (b) without intrinsics, applied to a four-phase
mathematical operation including a transcendental function, is
the artefact this repository documents.

A modest secondary contribution is the **methodological corpus**
distilled in [`05-troubleshooting.md`](05-troubleshooting.md)
and [`06-extending-toolchain.md`](06-extending-toolchain.md):
eleven distinct GCC-internals issues identified, root-caused, and
fixed, then generalised into a recipe for the *next* fused
primitive.

---

## 5. Future work

The natural continuations, in order of decreasing priority:

### 5.1 Phase 4 — Spike reference implementation

Implement the architectural semantics of `attn` in the official
RISC-V instruction-set simulator
([Spike / `riscv-isa-sim`](https://github.com/riscv-software-src/riscv-isa-sim)).
The skeleton:

```c
// riscv-isa-sim/riscv/insns/attn.h
require_extension('I');
reg_t o_ptr = RS3;       // rd in our R4-type but read as RS3 by Spike's macros
reg_t q_ptr = RS1;
reg_t k_ptr = RS2;
reg_t v_ptr = RS3;
attention_reference (proc, o_ptr, q_ptr, k_ptr, v_ptr,
                     /* N, d from CSR or fixed */ );
```

This unblocks the **equivalence harness** described in
[§7 of `02-compiler-pass.md`](02-compiler-pass.md#7-why-the-loop-body-stays-and-what-removing-it-would-take).

### 5.2 Equivalence-driven dead-code elimination

Once Spike has the reference, implement a small follow-up GIMPLE
pass — call it `attndce` — that runs after `attnrec` and elides the
original loop body **only if** an out-of-band equivalence test has
been recorded as having passed for the relevant input shapes. The
flag could be `-mattn-elide-loops`; absence of the flag preserves
the current safe behaviour.

### 5.3 Synthesisable RTL

Implement the instruction in Chipyard's RoCC or an MMIO accelerator,
or in a Chisel-based custom core. Run on FireSim or an FPGA. The
toolchain side is, by construction, ready to drive any such
implementation — no compiler changes required.

### 5.4 Generalise the pass to other fused primitives

The pass's structure (gate, idiom recognition, IFN emission,
RTL expansion, encoded mnemonic) is generic; only the matching
predicate is `attn`-specific. Re-instantiating it for:

* **LayerNorm / RMSNorm** — a single-loop reduction with
  `(x - μ) / √(σ² + ε)`. One distinct load base, scalar reduction.
* **Rotary positional embedding (RoPE)** — element-wise pairs with
  `sin`/`cos`. Single-loop, two arrays.
* **Fused feed-forward (`Linear → GELU → Linear`)** — two GEMMs
  with a transcendental in between. Structurally similar to SDPA.

would each be a few hundred lines of pass code following the
template in [`06-extending-toolchain.md`](06-extending-toolchain.md).

### 5.5 Robustness studies

* Quantify the matcher's recall on a corpus of attention
  implementations from open-source repositories
  (HuggingFace `transformers`, `llama.cpp`, MLIR/IREE generated C,
  `torch.compile` C output).
* Stress-test for false positives by compiling SPEC CPU 2017,
  PolyBench, and the GCC test-suite under `-mattn` and confirming
  no `attn` is emitted in non-attention code.

### 5.6 Multi-head attention and batched attention

Multi-head attention is structurally an outer loop over heads
wrapping single-head SDPA. Extending the matcher to recognise
"single-head SDPA inside a small head-loop with strided base
addresses" would let the pass match *all* canonical attention
patterns, not just single-head.

---

## 6. Limitations and threats to validity

Honesty about what this work does *not* do strengthens the claim.

| Limitation | Mitigation / why it is acceptable |
|------------|------------------------------------|
| The hardware semantics of `attn` are not implemented. | This is by design — the project's contribution is the toolchain side. Phase 4 (§5.1) closes this gap. |
| The matcher recognises only *one* canonical SDPA shape (fused outer loop). | False negatives on alternative shapes are detected via the GIMPLE dump and produce no incorrect code. Adapting the source to the recognised shape is documented in [§9.2 of `02-compiler-pass.md`](02-compiler-pass.md#92-completeness-false-negatives). |
| Test coverage is one program (`sdpa_test.c`) and one shape (`N = d = 32`). | A robustness study (§5.5) is the appropriate response. |
| The five matching conditions are syntactic; they do not constitute a semantic proof. | Standard for idiom recogniser passes. The volatile call is left alongside the original loop body so that even a false positive is non-catastrophic until Phase 4 ships. |
| No measurement of compile-time overhead. | The pass's worst-case work is `O(F)` in the number of basic blocks/statements per function, with early-exit on each of the five checks. Overhead is expected to be sub-percentage of total compile time; measuring it is straightforward future work. |
| The R4-type encoding hard-codes float32 and row-major. | Quantised (int8, fp8) and column-major variants would require new `funct2` values and matcher branches. The two unused `funct2` bit patterns within `custom-0` provide easy room to grow. |

---

## 7. Suggested evaluation plan

A small but defensible evaluation that a thesis or paper could
present:

1. **Functional correctness.** Show, for `sdpa_test.c` with shapes
   (N, d) ∈ {(32,32), (64,64), (128,128)}, that the compiler
   emits exactly one `attn` instruction per call site, and that
   the binary's behaviour is identical *with* and *without*
   `-mattn` (because the loop body is preserved). Reproduces in
   one shell script.
2. **Soundness on adversarial code.** Compile SPEC CPU 2017
   benchmarks under `-mattn -O2`. Assert that no `attn`
   instruction is emitted in any binary. (Expected: zero
   false positives.)
3. **Recall on attention-shaped corpora.** Strip the `attn` calls
   from a small set of attention implementations (e.g. five
   hand-translated kernels from `llama.cpp` and HuggingFace),
   compile each under `-mattn`, count how many produce one
   `attn`. Report recall.
4. **Compile-time overhead.** `time` the build of a representative
   project (e.g. a small ML inference library) with and without
   `-mattn`. Report ratio.
5. **Code-size impact.** `size` the resulting `.text` segment with
   and without `-mattn`. (Currently the loop body remains, so
   `.text` will *increase* by the size of the new instruction;
   after Phase 4's loop deletion, `.text` is expected to *decrease*
   substantially.)
6. **Cross-version validation.** Repeat (1) on at least one other
   GCC version (e.g. 14.x). This guards against accidental reliance
   on internals specific to GCC 15.2.

Even points (1)–(4) together would constitute a respectable
empirical section in a workshop paper.

---

## 8. Suggested paper outline

For a venue such as **CGO**, **CC**, **LCTES**, **CARRV**, or a
RISC-V workshop:

```
1. Introduction
   - Attention dominates Transformer cost; specialised hardware
     emerging (FlashAttention, FACT, Sanger). Compiler-side path
     from C to a custom instruction is unclear in the open
     ecosystem.
2. Background
   - RISC-V custom-instruction slots; GCC pipeline; idiom
     recognition; the SDPA computation.
3. Design
   - The attn instruction (Section 4 of paper = our
     01_INSTRUCTION_SPEC_new).
   - The five-check pattern matcher (= our 02_COMPILER_PASS_new).
4. Implementation
   - The eleven files (= our 04_PATCHES_AND_FILES_new), with
     emphasis on the IFN + define_insn path.
5. Engineering lessons
   - Compressed retelling of 05_TROUBLESHOOTING_new — categorised
     into build, header, middle-end, backend pitfalls. Useful for
     practitioners.
6. Generalisation
   - The template (= our 06_EXTENDING_TOOLCHAIN_new). Sketch how
     to apply it to LayerNorm / RoPE.
7. Evaluation
   - The plan in §7 above.
8. Related work
   - Material in §2 of this document.
9. Limitations and future work
   - §5–§6 of this document.
10. Conclusion
```

The artefact (this repository) is the natural artefact-evaluation
submission accompanying such a paper.

---

## 9. Reading list and primary sources

For a reader writing a literature review:

**RISC-V**

* Asanović K., Patterson D. *Instruction Sets Should Be Free: The
  Case for RISC-V*, EECS UCB Tech. Rep. 2014.
* Waterman A., Asanović K. (eds.). *The RISC-V Instruction Set
  Manual: Volume I — Unprivileged ISA*, current revision.
* Patterson D., Waterman A. *The RISC-V Reader*, Strawberry
  Canyon, 2017.

**Transformers and attention**

* Vaswani A. et al. *Attention Is All You Need*, NeurIPS 2017.
* Dao T. et al. *FlashAttention: Fast and Memory-Efficient Exact
  Attention with IO-Awareness*, NeurIPS 2022.
* Tambe T. et al. *Hardware Architecture for Transformer*,
  Hot Chips 2023.

**GCC internals**

* Novillo D. *Tree SSA — A New High-Level Optimization Framework
  for GCC*, GCC Summit 2003.
* Stallman R. M. and the GCC Developer Community. *GNU Compiler
  Collection (GCC) Internals Manual*, current revision.
  Especially Chapter 11 (Machine Descriptions) and Chapter 26
  (RTL).
* `tree-ssa-strlen.cc` source — the canonical example of a
  GIMPLE idiom recogniser feeding into a builtin/IFN.

**Idiom recognition and accelerators**

* Pottenger W. M., Eigenmann R. *Idiom recognition in the Polaris
  parallelizing compiler*, ICS 1995.
* Mendis C. et al. *Compiler Auto-Vectorization with Imitation
  Learning*, NeurIPS 2019. (Useful as a contemporary
  pattern-matching counterpoint.)

**Co-design**

* Asanović K. et al. *The Rocket Chip Generator*, EECS UCB
  Tech. Rep. 2016.
* Karandikar S. et al. *FireSim: FPGA-Accelerated Cycle-Exact
  Scale-Out System Simulation in the Public Cloud*, ISCA 2018.

This list is intentionally short; it is the *minimum* set of
references a reader should be aware of to engage with this work
critically. A full literature review for a thesis would extend it
considerably, especially with FlashAttention follow-ups and recent
attention-accelerator ASIC papers.

---

**Next:** [`08-glossary.md`](08-glossary.md) — every term and
acronym used across this documentation set, defined.
