# Known Issues and Fixes

**Author:** Yash Awasthi

All build errors and ICEs encountered during implementation,
with the exact fix applied for each.

---

## Issue 1 — `tree-ssa-attn.o: No such file or directory`

**Symptom:**
```
ar: tree-ssa-attn.o: No such file or directory
make[2]: *** [Makefile:2314: libbackend.a] Error 1
```

**Cause:**
The source file `tree-ssa-attn.cc` was placed at `gcc/tree-ssa-attn.cc`
instead of `gcc/gcc/tree-ssa-attn.cc`. The build system looks in `gcc/gcc/`.

**Fix:**
```bash
mv ~/riscv-gnu-toolchain/gcc/tree-ssa-attn.cc \
   ~/riscv-gnu-toolchain/gcc/gcc/tree-ssa-attn.cc
```

---

## Issue 2 — `operand_equal_p` and `fold_unary` not declared

**Symptom:**
```
tree-data-ref.h:599:8: error: 'operand_equal_p' was not declared in this scope
tree-data-ref.h:703:30: error: 'fold_unary' was not declared in this scope
```

**Cause:**
`tree-data-ref.h` uses `operand_equal_p` and `fold_unary` in inline
functions. These are declared in `fold-const.h`, which must be included
before `tree-data-ref.h`.

**Fix:**
Add to `tree-ssa-attn.cc` before `#include "tree-data-ref.h"`:
```c
#include "fold-const.h"
```

---

## Issue 3 — `tree-loop-distribution.h: No such file or directory`

**Symptom:**
```
tree-ssa-attn.cc:34:10: fatal error: tree-loop-distribution.h: No such file or directory
```

**Cause:**
`build_rdg` and `free_rdg` were assumed to be declared in a header.
They are actually methods of the `loop_distribution` class, defined
inside `tree-loop-distribution.cc` — there is no public header.

**Fix:**
Remove `#include "tree-loop-distribution.h"` entirely. Replace the
`build_rdg`-based SCC check with a direct loop count:

```c
// Instead of build_rdg, count loops with madd reductions
static int
attn_count_reduction_sccs (class loop *outer)
{
  int count = 0;
  class loop *l;
  FOR_EACH_LOOP (l, 0)
    if (attn_find_madd_reduction (l))
      count++;
  return count;
}
```

---

## Issue 4 — `mark_virtual_operands_for_renaming` not declared

**Symptom:**
```
tree-ssa-attn.cc:516:7: error: 'mark_virtual_operands_for_renaming'
    was not declared in this scope
```

**Cause:**
The function is declared in `tree-into-ssa.h`, not in `tree-ssa.h`.

**Fix:**
```c
#include "tree-into-ssa.h"
```

---

## Issue 5 — ICE in `scev_initialize`

**Symptom:**
```
/tmp/sdpa.c:5:6: internal compiler error: in scev_initialize,
    at tree-scalar-evolution.cc:3006
```

**Cause:**
The pass called `scev_initialize()` explicitly, but SCEV is already
initialized by the time the pass runs at position #179 in the pipeline.
Calling it twice triggers the assertion.

**Fix:**
Remove `scev_initialize()` and `scev_finalize()` calls from the pass.
SCEV is already active — just use `number_of_latch_executions()` directly.

---

## Issue 6 — ICE in `propagate_necessity` (DCE)

**Symptom:**
```
internal compiler error: in propagate_necessity, at tree-ssa-dce.cc:1148
```
or
```
internal compiler error: Segmentation fault
... walk_aliased_vdefs_1 ...
```

**Cause:**
The `gcall` for `IFN_RISCV_ATTN` had no virtual operands (`vuse`/`vdef`).
DCE's `propagate_necessity` hits `gcc_unreachable()` when it encounters
a memory-touching statement without proper virtual operand links.

Attempts to manually add vdef via `make_ssa_name(gimple_vop(cfun))`
caused a segfault in `walk_aliased_vdefs` because the SSA name was not
properly linked into the def-use chain.

**Fix:**
Mark the call volatile instead of manually managing vdefs:
```c
gimple_set_has_volatile_ops (call, true);
```

Volatile calls are treated as having side effects by all GCC passes
and are never eliminated by DCE.

Also change `internal-fn.def` from `ECF_LEAF | ECF_NOTHROW` to just
`ECF_NOTHROW`. `ECF_LEAF` signals "does not touch memory" which
contradicts the volatile flag.

---

## Issue 7 — ICE in `riscv_sched_variable_issue`

**Symptom:**
```
internal compiler error: in riscv_sched_variable_issue,
    at config/riscv/riscv.cc:9884
```

**Cause:**
The RTL instruction scheduler encountered our `attn` instruction and
called `get_attr_type(insn)`. Our `define_insn` had `type "unknown"`,
which causes `riscv_sched_variable_issue` to hit:
```c
gcc_assert (get_attr_type (insn) != TYPE_UNKNOWN);
```

**Fix:**
Change `type "unknown"` to `type "ghost"` in `riscv.md`:
```scheme
[(set_attr "type" "ghost")
 (set_attr "mode" "DI")]
```

Ghost instructions are treated as scheduling blockages and require no
DFA reservation. The scheduler handles them without asserting.

---

## Issue 8 — Pass inserted inside Graphite block

**Symptom:**
Pass registered but `attnrec` dump never appears even with `-mattn -O2`.

**Cause:**
`NEXT_PASS (pass_recognize_attn)` was inserted immediately below
`NEXT_PASS (pass_graphite)`, which places it **inside** the Graphite
driver block (between `PUSH_INSERT_PASSES_WITHIN` and
`POP_INSERT_PASSES()`). Wrong scope, wrong driver.

**Fix:**
Find the `POP_INSERT_PASSES()` that closes the Graphite block and
insert immediately **after** it:

```c
POP_INSERT_PASSES ()
NEXT_PASS (pass_recognize_attn);   ← correct position
```

---

## Issue 9 — `attn` instruction fires inside a loop (wrong position)

**Symptom:**
Assembly shows `attn` instruction inside the S·V loop body, firing
once per outer iteration instead of once for the whole function.

**Cause:**
The pass visited loops `LI_FROM_INNERMOST` and matched loop 4 (the
S·V outer loop). Its preheader was inside the iteration of a larger
outer loop. The IFN was inserted in that inner preheader.

**Fix:**
Only consider top-level loops (direct children of the root loop):
```c
for (auto loop : loops_list (cfun, LI_FROM_INNERMOST))
  {
    if (loop_depth (loop) != 1)  // skip non-top-level loops
      continue;
    if (!loop->inner)
      continue;
    if (try_recognize_attention (loop))
      { changed = true; break; }
  }
```

---

## Issue 10 — Three separate loop nests, no single outer loop

**Symptom:**
Matcher rejects all loops — no single loop contains Q, K, and V as
load bases simultaneously. The dump shows:
```
loop 1 rejected — need >=3 loads got 2
loop 8 rejected — need >=3 loads got 2
```

**Cause:**
The original `sdpa.c` test used separate top-level loops for QKᵀ,
softmax, and S·V. GCC compiled them as three independent loop nests.
No single loop contained all three of Q, K, V.

**Fix (two parts):**

Part 1 — Rewrite `sdpa.c` in fused form (all phases inside one outer
i-loop) so GCC sees a single nest:

```c
for (int i = 0; i < N; i++) {
    // Phase 1: QK^T
    // Phase 2: softmax
    // Phase 3: S*V
}
```

Part 2 — Change load base collection to scan the whole function
(not just the matched loop's body), because at `-O2` GCC may still
place softmax in sibling loops even with the fused source:

```c
static void
attn_collect_load_bases (class loop *outer ATTRIBUTE_UNUSED,
                         auto_vec<tree> &bases)
{
  basic_block bb;
  FOR_EACH_BB_FN (bb, cfun)
    // ... collect from all BBs, skip local arrays
}
```

---

## Issue 11 — `-fno-schedule-insns` flags required

**Symptom:**
Without `-fno-schedule-insns -fno-schedule-insns2`, the build crashes
in `riscv_sched_variable_issue` (see Issue 7 above). Even after fixing
`type "ghost"`, the scheduler flags are still needed until the
`riscv.md` change is rebuilt into the installed compiler.

**Current status:**
`type "ghost"` fix is in the source. After a full rebuild, the
`-fno-schedule-insns` flags will no longer be required. They are
included in test commands as a workaround until the rebuilt compiler
is verified.
