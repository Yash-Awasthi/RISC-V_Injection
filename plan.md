# Assessment: `attn` custom RISC-V instruction toolchain

## What this project actually does

This is a fork of `riscv-gnu-toolchain` (full vendored copies of GCC 15.2
and binutils 2.46) with two real, working pieces on top:

1. **Assembler/disassembler support** for a new R4-type opcode `attn rd,
   rs1, rs2, rs3` in the `custom-0` slot. This part is complete and
   mechanical: opcode table entries in `binutils/`, verified by
   `objdump` round-tripping the encoding. Low risk, correctly scoped.

2. **A GCC middle-end pass** (`attnrec`, `gcc/gcc/tree-ssa-attn.cc`,
   ~550 lines) that runs at `-O2 -mattn` and tries to recognize
   scaled dot-product attention (softmax(QKᵀ/√d)V) in plain C, then
   inserts a call to an internal function that lowers to `attn`.

Compiling the one demo file (`demo/sdpa_test.c`) produces the instruction
as advertised. That result is real but narrow — it is the output of a
pattern matcher tuned to match exactly that file's loop shape, not a
general SDPA detector.

## How the pass actually decides "this is attention"

Reading `attn_match` in `tree-ssa-attn.cc`, the check is a bag of
whole-function heuristics, not a structural proof:

- at least one loop nest with an inner multiply-accumulate reduction
  (`attn_find_madd_reduction`, checked anywhere in the loop's
  descendants),
- an `expf`/`exp` call **and** a division **anywhere in the whole
  function** (`attn_has_softmax_and_scale` scans every basic block,
  not just the candidate loop),
- at least 3 distinct non-local pointer bases used as loads and 1 used
  as a store, collected **across the entire function**
  (`attn_collect_load_bases`), and
- a statically known trip count for the outer loop.

None of these checks confirm the loops actually compute
`softmax(QKᵀ/√d)V` — they confirm the function contains features that
tend to co-occur when someone writes SDPA by hand in the fused,
single-outer-loop style the author used for the demo. A function with
three unrelated pointer-chasing loops, an unrelated `expf` call, and an
unrelated division anywhere else in the same function would also match
and get its first qualifying loop nest replaced by a call to
`IFN_RISCV_ATTN`, silently, with no relation between the matched
operands and the real computation.

## The instruction has no defined semantics and no hardware

- `attn_emit_replacement` passes only 4 raw pointers (O, Q, K, V) as
  operands. The file's own header comment describes a richer ABI
  (`rs2 -> &{N, D, H}`, `rs3 -> &{scale_bits, flags}`), but the actual
  code that builds the call ignores that and never emits it — dims and
  scale are dropped. As specified today, no hardware implementation
  could recover N, D, or the scale factor from the instruction alone.
- There is no Spike model, no RTL/Verilog, no simulator semantics
  anywhere in the repo. The README's own status table says this
  explicitly: hardware semantics are "not done," synthesizable RTL is
  "out of scope." So the actual literal instruction — the thing this
  project is named after — has never been executed or verified to
  compute anything.
- Because there is no simulator to prove equivalence, the original
  loop body is deliberately left in the compiled output next to the
  new `attn` call. The compiled binary today does the full O(N²D) loop
  work **and** contains a dead call to an instruction nothing can
  execute. Nothing gets faster; nothing gets smaller. The demo proves
  "the compiler can be made to emit a specific instruction word," not
  "this accelerates attention."

## Testing depth

`demo/failures/` has 8 handwritten negative cases (missing loop, wrong
reduction shape, unfused top-level loops, unknown trip count, etc.),
which is a reasonable start and shows the author understood the
matcher's fragility. But all cases — positive and negative — are
small, synthetic, single-function files authored specifically to
exercise this matcher. There is no test against real transformer
kernel code (`ggml`, `llama.cpp`, PyTorch-generated C, an unrolled
multi-head variant) to see whether the heuristic fires correctly, or
misfires, on code nobody wrote to please it.

## Is this worth continued investment?

**As a compiler-internals learning exercise or portfolio piece**: yes,
this is a legitimate and reasonably careful demonstration of adding a
GIMPLE pass, an internal function, an RTL pattern, a compiler flag, and
an opcode table entry to a real GCC/binutils tree. That work is done
and documented well (`docs/` set, patch inventory, troubleshooting log).

**As "hardware transformer acceleration"**: not in its current
direction. Continued investment in the stated end goal (delete the
loop body once semantics are proven, per the README's own "next step")
is actively counterproductive without first fixing two things below —
doing that work now would be building on a matcher and an instruction
encoding that are both provably incomplete, and the eventual dead-code
deletion step is the point where an unsound match becomes a silent
wrong-answer bug instead of a harmless dead call.

## Next steps, in priority order

1. **Fix the correctness hazard before doing anything else.** Either
   (a) replace the whole-function bag-of-features matcher with a real
   structural match — the three phases must be nested inside the same
   outer loop, in the specific data-dependency order that constitutes
   SDPA, not merely present somewhere in the function — or (b) drop
   automatic recognition entirely and expose `attn` through an explicit
   builtin (`__builtin_riscv_attn(Q, K, V, O, N, D, scale)`). Option
   (b) is the standard approach real RISC-V extensions (vector, crypto)
   ship with, is far less code, and removes the silent-misfire risk
   completely. Given the ponytail lens (does this need to be built at
   all?), (b) is the right default unless auto-recognition is itself
   the research question.
2. **Fix the ABI mismatch.** Decide whether N, D, and the scale factor
   are implicit (fixed at compile time, baked into the hardware
   contract) or passed in registers, and make the emitted call and the
   header comment agree. Right now they contradict each other.
3. **Build a minimal Spike (or QEMU) model of `attn`** before doing any
   more compiler work. Until the instruction executes somewhere and
   produces a number, there is no way to check the encoding is even
   sound, let alone measure whether it is faster than the loop it
   replaces. This is the prerequisite for the "delete the loop body"
   step the docs already call out as the natural next phase.
4. **Test against real kernels, not just hand-authored demo files.**
   Run the matcher over an existing SDPA implementation from `ggml` or
   similar and record whether it fires, and separately confirm it does
   *not* fire on ordinary numeric code that happens to share a few
   features (a matmul-then-normalize routine, for instance).
5. **Slim the repository.** Full vendored copies of GCC 15.2 and
   binutils 2.46 sit in-tree instead of being tracked as a patch set
   or pinned upstream reference; this buries the actual contribution
   (under 10 changed files) inside hundreds of megabytes of unmodified
   upstream source and makes review harder than it needs to be.
