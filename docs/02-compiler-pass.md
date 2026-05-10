# 02 — The `attnrec` Compiler Pass

> **Audience.** A reader who understands the basics of GIMPLE and
> the GCC pass pipeline (covered in
> [`00-background.md` §7](00-background.md#7-gimple-ssa-and-the-gcc-pass-pipeline))
> and now wants to know exactly what the `attnrec` pass does and why
> it is correct.

This document is the conceptual companion to the source file
`gcc/gcc/tree-ssa-attn.cc` (~500 lines) and to the manual edits in
`gcc/gcc/passes.def`, `gcc/gcc/tree-pass.h`, and
`gcc/gcc/Makefile.in` that wire the pass into GCC's build.

---

## Table of contents

1. [What problem does the pass solve?](#1-what-problem-does-the-pass-solve)
2. [Design philosophy: idiom recognition vs explicit intrinsics](#2-design-philosophy-idiom-recognition-vs-explicit-intrinsics)
3. [Where the pass runs and why](#3-where-the-pass-runs-and-why)
4. [The pass class — boilerplate](#4-the-pass-class--boilerplate)
5. [The five matching conditions](#5-the-five-matching-conditions)
6. [Emitting `IFN_RISCV_ATTN`](#6-emitting-ifn_riscv_attn)
7. [Why the loop body stays — and what removing it would take](#7-why-the-loop-body-stays-and-what-removing-it-would-take)
8. [Reading the GIMPLE dump](#8-reading-the-gimple-dump)
9. [Soundness, completeness, and false-positive analysis](#9-soundness-completeness-and-false-positive-analysis)

---

## 1. What problem does the pass solve?

Suppose the user has written a perfectly idiomatic SDPA in plain C:

```c
void attention(int N, int d,
               const float *Q, const float *K, const float *V,
               float *O)
{
    float S[N][N];
    float scale = 1.0f / sqrtf((float) d);

    for (int i = 0; i < N; i++) {
        // Phase 1+2:  S[i][j] = scale * sum_k Q[i][k] * K[j][k]
        for (int j = 0; j < N; j++) {
            float acc = 0.0f;
            for (int k = 0; k < d; k++)
                acc += Q[i*d + k] * K[j*d + k];
            S[i][j] = acc * scale;
        }

        // Phase 3:  S[i][:] = softmax(S[i][:])
        float row_max = S[i][0];
        for (int j = 1; j < N; j++)
            if (S[i][j] > row_max) row_max = S[i][j];
        float row_sum = 0.0f;
        for (int j = 0; j < N; j++) {
            S[i][j] = expf(S[i][j] - row_max);
            row_sum += S[i][j];
        }
        for (int j = 0; j < N; j++)
            S[i][j] /= row_sum;

        // Phase 4:  O[i][:] = S[i][:] · V
        for (int j = 0; j < d; j++) {
            float acc = 0.0f;
            for (int k = 0; k < N; k++)
                acc += S[i][k] * V[k*d + j];
            O[i*d + j] = acc;
        }
    }
}
```

The user has **not** included any header, called any intrinsic, or
written any inline assembly. They have written ordinary C. The job
of `attnrec` is:

> Recognise that this loop nest implements scaled dot-product
> attention, and replace it with a single `attn` machine instruction
> taking direct pointers to `Q`, `K`, `V`, and `O`.

If the loop is *not* attention — if any of the five matching
conditions in §5 fails — the pass does nothing at all. Compilation
proceeds exactly as upstream GCC would have done.

---

## 2. Design philosophy: idiom recognition vs explicit intrinsics

The conventional way to expose a custom instruction to a programmer
is via an **intrinsic** or **builtin** — the user writes
`__builtin_riscv_attn(O, Q, K, V)` and the compiler emits one
machine instruction. That approach is mechanically simpler, and an
earlier prototype of this project did exactly that (see
[`07-research-context.md` §3](07-research-context.md#3-comparison-with-the-earlier-prototype)).

The current project deliberately rejects that approach in favour of
**automatic idiom recognition** for three reasons:

1. **Portability of source code.** A C program containing
   `__builtin_riscv_attn` no longer compiles on x86, on a non-`-mattn`
   RISC-V target, or with a different compiler. The same source
   compiled with idiom recognition is *exactly* the same C program
   it was before — the optimisation is opt-in via a flag, and absent
   the flag the program runs the loop body as written.
2. **Hands-off acceleration.** Existing C corpora — kernels written
   ten years ago, models exported from PyTorch via `torch.compile`
   to plain C, OpenBLAS-style GEMM kernels — can benefit without
   anyone touching their source. This matches the way that
   auto-vectorisation, GCC's `tree-ssa-strlen`, and Intel's MKL
   "JIT GEMM" features deliver value: the user compiles, the
   compiler decides.
3. **Forcing function on compiler infrastructure.** Implementing
   automatic recognition forces engagement with the full GIMPLE
   pipeline (SSA, SCEV, loop tree, IFN, RTL expansion). The
   resulting infrastructure is reusable for *any* future fused
   primitive (LayerNorm, RMSNorm, RoPE, fused-FFN), not just `attn`.

The cost is implementation complexity and the risk of
false-negatives (legitimate attention loops we fail to match). §9
discusses this honestly.

---

## 3. Where the pass runs and why

The pass is registered at **position 179** in the GIMPLE optimisation
pipeline, immediately after Graphite's `POP_INSERT_PASSES()` block.
The relevant snippet from `gcc/gcc/passes.def`:

```c
NEXT_PASS (pass_graphite);
PUSH_INSERT_PASSES_WITHIN (pass_graphite)
   NEXT_PASS (pass_graphite_transforms);
   NEXT_PASS (pass_lim);
   ...
POP_INSERT_PASSES ()                  /* end of Graphite block */
NEXT_PASS (pass_recognize_attn);      /* ← OUR PASS */
```

The choice is constrained from both sides:

* **It must run after the loop framework is established.** The pass
  iterates over the function's loop tree (`loops_list`,
  `loop_depth`, `loop->inner`), queries trip counts via SCEV, and
  uses single-exit edges. All of these become reliable only after
  the loop pipeline has run.
* **It must run before the IR is lowered too far.** Once the
  representation is closer to RTL, the high-level idioms — calls
  to `expf`, dimensional `[i*d + k]` index arithmetic, distinct
  `float[]` array bases — get folded, vectorised, or strip-mined
  beyond easy recognition.

Position 179 — directly after Graphite — sits in the narrow window
where loops are *clean* but still *high-level*.

A common failure mode in early development was inserting the pass
**inside** the Graphite `PUSH_INSERT_PASSES_WITHIN` block. That
silently places the pass in the wrong driver scope; it is registered
but never executed. See
[`05-troubleshooting.md` Issue 8](05-troubleshooting.md#issue-8--pass-inserted-inside-graphite-block).

---

## 4. The pass class — boilerplate

Every GIMPLE pass in GCC inherits from `gimple_opt_pass` and
provides a `pass_data` descriptor plus an `execute` method. The
boilerplate for `attnrec` is the standard shape:

```cpp
namespace {

const pass_data pass_data_recognize_attn = {
  GIMPLE_PASS,                  /* type */
  "attnrec",                    /* name (shows in -fdump-tree-* filenames) */
  OPTGROUP_LOOP,                /* optinfo flags */
  TV_TREE_LOOP,                 /* timevar */
  PROP_cfg | PROP_ssa,          /* properties_required */
  0,                            /* properties_provided */
  0,                            /* properties_destroyed */
  0,                            /* todo_flags_start */
  TODO_update_ssa               /* todo_flags_finish */
};

class pass_recognize_attn : public gimple_opt_pass
{
public:
  pass_recognize_attn (gcc::context *ctxt)
    : gimple_opt_pass (pass_data_recognize_attn, ctxt) {}

  bool gate (function *) final override
  {
#ifdef TARGET_ATTN
    return TARGET_ATTN && optimize >= 2 && flag_tree_loop_optimize;
#else
    return false;
#endif
  }

  unsigned int execute (function *fun) final override
  {
    return try_recognize_attention_in_function (fun) ? TODO_cleanup_cfg : 0;
  }
};

} // anon namespace

gimple_opt_pass *
make_pass_recognize_attn (gcc::context *ctxt)
{
  return new pass_recognize_attn (ctxt);
}
```

Three details warrant attention:

* **`PROP_cfg | PROP_ssa`** in `properties_required` declares that
  the pass relies on the control-flow graph and SSA form being
  built; GCC will refuse to schedule the pass over a function that
  lacks them.
* **`TODO_update_ssa`** in `todo_flags_finish` tells GCC's pass
  manager to refresh SSA after we are done, since we have inserted
  a new GIMPLE call that defines/uses virtual operands.
* **The gate** insists on `TARGET_ATTN`, optimisation level ≥ 2,
  and loop optimisation enabled. At `-O0` or `-O1` the loop
  framework state we depend on is not guaranteed.

The factory function `make_pass_recognize_attn` is declared in
`tree-pass.h` and called by `passes.def`.

---

## 5. The five matching conditions

When the gate is open, `execute` walks every top-level loop in the
function and asks: *is this loop attention?* The decision is the
conjunction of five Boolean checks. **All five must pass** before
the pass will rewrite anything.

Throughout this section, "function" means the GIMPLE function
currently being compiled (`fun`), and "loop" means the candidate
outer loop that we are testing.

### Check 1 — Madd reduction in the inner loop

The innermost loop body must contain the canonical
multiply-and-accumulate idiom of a dot product:

```
acc_phi = PHI <0.0 (preheader), acc_new (latch)>
prod    = a * b           ;; MULT_EXPR
acc_new = acc_phi + prod  ;; PLUS_EXPR — back-edge value
```

Implementation: walk the phi nodes of the innermost header,
identify any whose back-edge value is a `PLUS_EXPR`, and check that
one of the addition operands is itself the LHS of a `MULT_EXPR`
two operands of which are SSA names *fed by memory loads*.

This check is what discriminates a dot product (`acc += a[i]*b[i]`)
from, say, a saxpy (`y[i] += alpha * x[i]`). In a saxpy, only one
factor is a load; in our dot product, **both** are loads, because
both sides come from the matrix arrays.

### Check 2 — Softmax signature in the function

A softmax cannot be expressed without an exponential and a division.
The pass scans every basic block in the function for:

* at least one call to `expf` (or `exp`, or `__builtin_expf`);
* at least one `RDIV_EXPR` (real / floating-point division) or, in
  rare integer-quantised models, `TRUNC_DIV_EXPR`.

This is a **function-level** check, not a loop-local one — at `-O2`
GCC is free to hoist or sink these operations into sibling blocks,
so insisting on locality would create false negatives.

A subtle interaction: the source code as written contains a
`sqrtf(d)` call, used to compute `1/√d`. But at `-O2`, GCC's
`fold-const` recognises that `d` is a small compile-time constant
(the test program uses `d = 32`) and folds `1/sqrtf(32)` into the
floating-point constant `0.17677669...`. The `sqrtf` is therefore
*absent* from the GIMPLE the pass sees. The matcher does **not**
look for `sqrt` precisely because of this folding behaviour.

### Check 3 — At least two madd-reduction loops in the function

SDPA contains *two* matrix products: `Q · Kᵀ` and `S · V`. Each, if
expressed as nested loops, has its own madd-reduction pattern. A
function that contains only one madd-reduction loop is doing
something simpler than attention (a plain GEMM, perhaps); the pass
rejects it.

This check counts how many top-level loops in the function pass
Check 1, and requires the count to be at least 2.

### Check 4 — Three distinct load bases, one store base

The pass collects, by walking every basic block in the function,
the *base addresses* of every `MEM_REF` / `TARGET_MEM_REF` it
encounters. Each address is canonicalised to a leaf declaration
(stripping `POINTER_PLUS_EXPR`, `ADDR_EXPR`, `NOP_EXPR`,
`SSA_NAME` chains). Local stack arrays — those whose declaration is
neither `DECL_EXTERNAL` nor `TREE_STATIC` — are filtered out, so
that the temporary `S[N][N]` does not contaminate the tally.

The pass requires:

* **≥ 3 distinct load bases** — one each for `Q`, `K`, `V`;
* **≥ 1 distinct store base** — for `O`.

Three loads is the minimum that distinguishes attention (Q, K, V)
from a plain GEMM (just two loads, A and B).

### Check 5 — Statically analysable trip count

Finally, the pass calls
`number_of_latch_executions(outer_loop)` to ask SCEV for the trip
count of the candidate outer loop. SCEV may answer with:

* a tree expression (anything finite the compiler can reason
  about) — accepted;
* the special tree `chrec_dont_know` — rejected.

This guards against pathological inputs (loops whose trip count
depends on data values, infinite loops, pointer-chasing) where a
mistaken rewrite would silently corrupt program semantics.

### Why exactly five?

The five checks are intentionally redundant — most real attention
loops would be uniquely identifiable from any three of them. The
redundancy is a **safety margin**: false-positive rewrites of
non-attention code are catastrophic (the binary will execute an
opaque `attn` instead of the correct loop), so the cost of a
slightly stricter matcher is low compared to the cost of a
false-positive in production. See §9 for a quantitative discussion.

---

## 6. Emitting `IFN_RISCV_ATTN`

When all five checks pass, the pass:

1. Resolves each of the four base pointers to a single SSA name
   (creating a temporary if the base is a constant address).
2. Constructs a `gcall` for the internal function `IFN_RISCV_ATTN`
   with these four arguments, in the order `(O, Q, K, V)`.
3. Marks the call **volatile** at the GIMPLE level —
   `gimple_set_has_volatile_ops(call, true)` — so DCE
   (dead-code elimination) treats it as observably side-effecting
   and never deletes it.
4. Inserts the call in the **preheader** of the matched outer
   loop. The loop body itself is left untouched.
5. Returns `TODO_cleanup_cfg` so GCC tidies up.

Pseudocode for the emission step:

```cpp
tree o_ptr = build_pointer_cast(cand.o_base);
tree q_ptr = build_pointer_cast(cand.q_base);
tree k_ptr = build_pointer_cast(cand.k_base);
tree v_ptr = build_pointer_cast(cand.v_base);

gcall *call = gimple_build_call_internal (IFN_RISCV_ATTN, 4,
                                          o_ptr, q_ptr, k_ptr, v_ptr);
gimple_set_has_volatile_ops (call, true);

gimple_stmt_iterator gsi = gsi_last_bb (loop_preheader_edge (outer)->src);
gsi_insert_after (&gsi, call, GSI_NEW_STMT);
```

The four arguments will, after RTL expansion, end up in the four
register operands of the `attn` instruction in the order required by
[§4 of `01-instruction-spec.md`](01-instruction-spec.md#4-operand-convention-and-abi):

```
                IFN_RISCV_ATTN (O, Q, K, V)         ← GIMPLE
                       │
                       │  expand_RISCV_ATTN
                       ▼
                gen_riscv_attn (O, Q, K, V)         ← RTL
                       │
                       │  riscv.md  define_insn
                       ▼
                attn rd, rs1, rs2, rs3              ← assembly
                     ^   ^    ^    ^
                     O   Q    K    V
```

The lowering from GIMPLE to RTL is handled by `expand_RISCV_ATTN`,
which is two dozen lines in `internal-fn.cc` and is shown in
[§7.3 of `01-instruction-spec.md`](01-instruction-spec.md#73-gcc--internal-function).

---

## 7. Why the loop body stays — and what removing it would take

After the rewrite, the binary contains *both* the new `attn`
instruction *and* the original loop body. This may look surprising;
it is in fact the **correct and intended** behaviour at this stage
of the project.

GCC is a compiler, not a theorem prover. It has matched a syntactic
pattern; it has *not* proven that the proposed `attn` instruction
computes the same values as the loop. Without that proof, removing
the loop body would mean trusting unverified hardware to produce
the right answer — a silent-wrong-answer hazard if the accelerator
has any bug.

The standard hardware/software co-design protocol for retiring a
software fallback in favour of a custom instruction is:

1. **Write the instruction's reference semantics** in an ISS such
   as Spike (`riscv-isa-sim/riscv/insns/attn.h`), reading `Q`, `K`,
   `V` from `rs1`/`rs2`/`rs3` and writing `O` through `rd`.
2. **Run both versions** — the original loop body and the
   `attn`-emitting version — on identical inputs, comparing outputs
   element-wise within an agreed tolerance.
3. **Once equivalence is established**, enable a follow-up GIMPLE
   pass (or a flag) that elides the original loops as provably-dead
   code.
4. **In production**, replace the Spike reference with an actual
   accelerator and re-run the equivalence harness on a continuous
   basis.

Steps 1–3 constitute the project's planned **Phase 4**, and they are
currently future work. The toolchain-side decision to keep the
loops is therefore the *responsible* default until the equivalence
proof is in place.

---

## 8. Reading the GIMPLE dump

The pass honours the standard `-fdump-tree-attnrec*` family of
flags. The most useful invocation for debugging is:

```bash
$HOME/riscv-install/bin/riscv64-unknown-elf-gcc \
    -mattn -O2 \
    -fdump-tree-attnrec-details \
    -c sdpa_test.c -o sdpa_test.o
cat sdpa_test.c.*attnrec*
```

A successful run produces output structured roughly like:

```
;; Function attention (attention, funcdef_no=0, decl_uid=...)
;;
;; attnrec: examining loop 1 (depth 1, header BB 4)
;;   check 1 (inner madd reduction): PASS — phi acc_5, mult acc_6 = _8 * _9
;;   check 2 (softmax signature):    PASS — found expf call in BB 12, RDIV in BB 14
;;   check 3 (>=2 madd loops):       PASS — counted 2 madd-reduction loops
;;   check 4 (load/store bases):
;;        load base[0] : Q_3(D)
;;        load base[1] : K_4(D)
;;        load base[2] : V_5(D)
;;       store base    : O_6(D)
;;     PASS — 3 distinct load bases, 1 store base
;;   check 5 (trip count): PASS — number_of_latch_executions = N - 1
;;
;; attnrec: emitting IFN_RISCV_ATTN (O_6(D), Q_3(D), K_4(D), V_5(D))
;;          in preheader BB 3
```

A *failed* run will show which check failed and why. A common
failure message looks like:

```
;; attnrec: examining loop 8 (depth 1, header BB 22)
;;   check 4 (load/store bases): FAIL — only 2 distinct load bases
```

That message means the loop has two `load`s (`A`, `B`); it is
probably a plain GEMM. The pass declines to rewrite it. Compilation
succeeds normally with no `attn` emitted.

The dump is the single most useful tool when adapting the matcher
for a new C source style; see
[`06-extending-toolchain.md`](06-extending-toolchain.md)
for guidance on extending the matching logic.

---

## 9. Soundness, completeness, and false-positive analysis

### 9.1 Soundness (no false positives)

A *false positive* would be: the pass emits `attn` for code that is
not, in fact, scaled dot-product attention. We argue informally
that this is unlikely.

Consider what a non-attention function would have to satisfy to be
mismatched:

1. Have at least two top-level loops, each containing an inner
   madd reduction with both factors loaded from arrays.
2. Contain at least one `expf` call somewhere in the body.
3. Contain at least one floating-point division.
4. Touch at least three statically distinct external/static array
   bases.
5. Have an analysable trip count on the outer loop.

Any C function meeting all five conditions is computing something
*structurally identical* to attention — it is doing two GEMM-like
operations with a softmax-like normalisation between them. In the
absence of a rigorous semantic proof, this is the project's working
notion of soundness; it is consistent with the standard "syntactic
recognition + deferred semantic verification" pattern used by other
GCC idiom-recognisers (e.g. `__builtin_strlen`, `tree-ssa-strlen`).

### 9.2 Completeness (false negatives)

The pass *will* fail to recognise attention written in styles it
was not designed for. Known patterns it currently rejects:

* **Three separate top-level loop nests** for the four phases
  (Q·Kᵀ, scale, softmax, S·V) without an outer `i` loop wrapping
  them. The fused source (one outer `i` loop containing all four
  phases) succeeds; the unfused style fails Check 4 because no
  single loop nest sees Q, K, *and* V together. (Issue 10 in
  [`05-troubleshooting.md`](05-troubleshooting.md#issue-10--three-separate-loop-nests-no-single-outer-loop)
  documents how the pass was eventually adapted to scan the whole
  function rather than just the matched loop body.)
* **Multi-head attention written as a single loop over all heads**
  with strided accesses into Q/K/V — the pass currently treats
  this as a plain 4-D GEMM and may not recognise it.
* **Attention with attention masks, dropout, or causal masking**
  introduces additional control flow inside the inner loops; the
  matcher does not yet skip past these.

These are all directions for improving recall in future revisions.

### 9.3 Practical false-positive risk and mitigation

To bound false-positive risk in practice, three safety nets are in
place:

1. The pass is gated by `-mattn`. A user opting in is implicitly
   asserting "this code base is attention-heavy".
2. The matched call is left **alongside** the original loop body
   (§7). Even if the emit was inappropriate, the program still runs
   correctly *as long as the hardware semantics of `attn` are a
   no-op or read-only* — and Phase 4's equivalence harness will
   detect any discrepancy before loops are deleted.
3. The GIMPLE dump (§8) records every match decision, so any
   surprising emission is visible to the compiler engineer in CI.

Together, these constitute "defence in depth" against bad rewrites
during the project's pre-Phase-4 lifetime.

---

**Next:** [`03-build-and-run.md`](03-build-and-run.md) — how to
actually build the toolchain and verify the layers above are
working.
