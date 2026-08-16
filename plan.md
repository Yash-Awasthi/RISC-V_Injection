# Assessment: `attn` custom RISC-V instruction toolchain

> **Status update.** The sections below are the original assessment.
> Since it was written, the explicit-builtin option in item 1 has
> been added (not substituted for the recognizer — both now exist
> side by side), the ABI mismatch in item 2 has been fixed for the
> builtin path only, and the false-positive risk named in the
> "how the pass decides" section has been confirmed concretely with
> a new test file. See "Next steps, in priority order" at the bottom
> for the current done/open status of each item.

## What this project actually does

This is a fork of `riscv-gnu-toolchain` (full vendored copies of GCC 15.2
and binutils 2.46) with two real, working pieces on top:

1. **Assembler/disassembler support** for a new R4-type opcode `attn rd,
   rs1, rs2, rs3` in the `custom-0` slot. This part is complete and
   mechanical: opcode table entries in `binutils/`, verified by
   `objdump` round-tripping the encoding. Low risk, correctly scoped.

2. **A GCC middle-end pass** (`attnrec`, `gcc/gcc/tree-ssa-attn.cc`,
   ~500 lines) that runs at `-O2` with both `-mattn` and the separate
   `-mattn-recognize` flag, tries to recognize scaled dot-product
   attention (softmax(QKᵀ/√d)V) in plain C, then inserts a direct
   call to `__builtin_riscv_attn` that lowers to `attn`. (An earlier
   revision routed this through a hand-written internal function,
   `IFN_RISCV_ATTN`; that internal function has since been deleted
   in favor of the direct builtin call — fewer moving parts, same
   result.)

Also now present: an **explicit builtin path**
(`demo/attn.h`, `demo/sdpa_builtin.c`) that skips pattern matching
entirely — the programmer states the four operands directly and
calls `__builtin_riscv_attn`, gated by plain `-mattn` with no
recognizer involved. This is now the primary, non-experimental path;
see "Next steps" item 1 below.

Compiling the one demo file (`demo/sdpa_test.c`) with the recognizer
produces the instruction as advertised. That result is real but
narrow — it is the output of a pattern matcher tuned to match exactly
that file's loop shape, not a general SDPA detector.

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
`__builtin_riscv_attn`, silently, with no relation between the matched
operands and the real computation.

**This is no longer hypothetical.** `demo/failures/fail_scattered-signature-known-false-positive.c`
is a concrete instance: an FIR filter plus two unrelated elementwise
loops plus an unrelated `expf`/division gain calculation, all in one
function. A hand trace against `attn_match` finds every one of its
eight conditions holds, so the matcher as it stands today is expected
to emit `attn` over it. The file has not been run through a built
toolchain (none was available when it was added), so this is a
verified-by-hand-trace claim, not yet a captured compiler run — but
it is a specific, named, reproducible failure, not a vague risk.

## The instruction has no defined semantics and no hardware

- The ABI mismatch is now fixed **for the explicit builtin path
  only**. `docs/01-instruction-spec.md` §4 normatively defines the
  block ABI (`rs1 -> &attn_ptrs{Q,K,V}`, `rs2 -> &attn_dims{N,D,H}`,
  `rs3 -> &attn_cfg{scale_bits,flags}`, `rd = O` direct), and
  `demo/attn.h`'s `attn_sdpa()` builds exactly those three structs
  before calling `__builtin_riscv_attn`. The recognizer path still has
  the original problem: `attn_emit_replacement` in `tree-ssa-attn.cc`
  passes only 4 raw pointers (O, Q, K, V), not the block ABI — dims
  and scale are dropped. This is now stated as a known limitation in
  the pass's own file header and in `docs/02-compiler-pass.md`, not
  silently contradicted, but it is still unfixed: no hardware
  implementation could recover N, D, or the scale factor from a call
  emitted by the recognizer.
- There is no Spike model, no RTL/Verilog, no simulator semantics
  anywhere in the repo — `tools/attn_model.py` is a pure-Python
  reference of the *arithmetic*, not an ISA simulator, and does not
  execute the encoded instruction. The README's own status table
  says this explicitly: hardware semantics are "never executed,"
  synthesizable RTL is "out of scope." So the actual literal
  instruction — the thing this project is named after — has never
  been executed or verified to compute anything, on either path.
- Because there is no simulator to prove equivalence, the original
  loop body is deliberately left in the compiled output next to the
  new `attn` call, on both paths. The compiled binary today does the
  full O(N²D) loop work **and** contains a dead call to an
  instruction nothing can execute. Nothing gets faster; nothing gets
  smaller. The demo proves "the compiler can be made to emit a
  specific instruction word," not "this accelerates attention."

## Testing depth

`demo/failures/` now has 10 handwritten cases, up from 8: the original
8 verified reject paths (missing loop, wrong reduction shape, unfused
top-level loops, unknown trip count, etc.), plus a realistic
near-miss (`fail_matmul-then-rownormalize.c` — matmul-then-normalize,
no softmax) and the known false positive discussed above
(`fail_scattered-signature-known-false-positive.c`). A separate
`scripts/tests/test_attn_contract.py` (14 tests, part of a 21-test
suite together with the existing pipeline tests) now checks the
encoding, the ABI struct sizes, and the builtin/ftype/insn wiring
stay consistent with each other. This is a reasonable, and now wider,
synthetic corpus, and it shows the author understood the matcher's
fragility. But all cases — positive and negative — remain small,
synthetic, single-function files authored specifically to exercise
this matcher, and none of the `demo/failures/` corpus has actually
been compiled in this environment (no `riscv64-unknown-elf-gcc`
toolchain was available) — the two newest files' claims are hand
traces against the matcher source, not captured `PASS`/`FAIL` output.
There is still no test against real transformer kernel code (`ggml`,
`llama.cpp`, PyTorch-generated C, an unrolled multi-head variant) to
see whether the heuristic fires correctly, or misfires, on code
nobody wrote to please it.

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
   builtin. Option (b) is the standard approach real RISC-V extensions
   (vector, crypto) ship with, is far less code, and removes the
   silent-misfire risk completely.
   **Status: partially done, and not the way originally proposed.**
   (b) was added — `__builtin_riscv_attn` via `demo/attn.h` now exists
   and is documented as the primary, non-experimental path — but (a)
   was not dropped. The recognizer still exists, still runs at `-O2`,
   and is still reachable by any caller who passes
   `-mattn -mattn-recognize`; it is opt-in and off by default rather
   than removed. The underlying matcher is unchanged and still unsound:
   `fail_scattered-signature-known-false-positive.c` (see "How the
   pass actually decides," above) demonstrates the exact hazard this
   item was written to close. **Open:** either fix the matcher's
   structural soundness or remove it; "off by default" reduces risk
   but does not resolve the item.
2. **Fix the ABI mismatch.** Decide whether N, D, and the scale factor
   are implicit (fixed at compile time, baked into the hardware
   contract) or passed in registers, and make the emitted call and the
   header comment agree. Right now they contradict each other.
   **Status: done for the builtin path, open for the recognizer path.**
   `docs/01-instruction-spec.md` §4 is now the normative ABI
   (`attn_ptrs`/`attn_dims`/`attn_cfg`), `demo/attn.h` builds it
   correctly, and nothing in the codebase claims otherwise for that
   path. `tree-ssa-attn.cc`'s `attn_emit_replacement` still passes raw
   O/Q/K/V pointers with no dims or scale — this is now disclosed as a
   known limitation in the source and in the docs rather than
   contradicted, but the underlying gap is not fixed.
3. **Build a minimal Spike (or QEMU) model of `attn`** before doing any
   more compiler work. Until the instruction executes somewhere and
   produces a number, there is no way to check the encoding is even
   sound, let alone measure whether it is faster than the loop it
   replaces. This is the prerequisite for the "delete the loop body"
   step the docs already call out as the natural next phase.
   **Status: open, untouched.** No simulator or hardware model exists
   anywhere in the repo. `tools/attn_model.py` is a pure-Python
   numerical reference of the SDPA arithmetic, checked against the ABI
   field layout by `scripts/tests/test_attn_contract.py` — useful, but
   it does not execute the encoded instruction and is not a substitute
   for this item.
4. **Test against real kernels, not just hand-authored demo files.**
   Run the matcher over an existing SDPA implementation from `ggml` or
   similar and record whether it fires, and separately confirm it does
   *not* fire on ordinary numeric code that happens to share a few
   features (a matmul-then-normalize routine, for instance).
   **Status: open, partially addressed with synthetic code only.** Two
   new files were added to `demo/failures/` — a matmul-then-normalize
   near-miss and the documented false-positive case — which is exactly
   the *kind* of case this item asked for, but both are still
   hand-authored specifically to exercise this matcher, not code from
   an existing, independently-written kernel. Nothing in the repo has
   been run against `ggml`, `llama.cpp`, or similar.
5. **Slim the repository.** Full vendored copies of GCC 15.2 and
   binutils 2.46 sit in-tree instead of being tracked as a patch set
   or pinned upstream reference; this buries the actual contribution
   (under 10 changed files) inside hundreds of megabytes of unmodified
   upstream source and makes review harder than it needs to be.
   **Status: open, untouched.**
