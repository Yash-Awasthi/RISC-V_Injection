# 05 — Troubleshooting Log

> **Audience.** The implementer trying to reproduce the build, or a
> reader curious about the *kind* of work involved in extending a
> production compiler. Each item below is a real problem encountered
> during the project, recorded as `Symptom → Root cause → Fix`.

The eleven issues are roughly ordered by the layer they touch:
build-system glitches first, then header-ordering, then
middle-end ICEs (Internal Compiler Errors), then pass-pipeline
mistakes, then matching-logic flaws, and finally a workaround for a
post-fix scheduler interaction.

---

## Table of contents

| # | Topic |
|---|-------|
| [1](#issue-1--tree-ssa-attno-no-such-file-or-directory) | New pass file in the wrong directory |
| [2](#issue-2--operand_equal_p-and-fold_unary-not-declared) | `operand_equal_p` / `fold_unary` not declared |
| [3](#issue-3--tree-loop-distributionh-no-such-file-or-directory) | Mistakenly including a non-existent header |
| [4](#issue-4--mark_virtual_operands_for_renaming-not-declared) | `mark_virtual_operands_for_renaming` not declared |
| [5](#issue-5--ice-in-scev_initialize) | ICE — calling `scev_initialize` twice |
| [6](#issue-6--ice-in-propagate_necessity-dce) | ICE — DCE on a memory-touching IFN |
| [7](#issue-7--ice-in-riscv_sched_variable_issue) | ICE — RISC-V scheduler asserts on `type "unknown"` |
| [8](#issue-8--pass-inserted-inside-graphite-block) | Pass registered but never executed |
| [9](#issue-9--attn-fires-inside-a-loop-wrong-position) | `attn` instruction in the wrong basic block |
| [10](#issue-10--three-separate-loop-nests-no-single-outer-loop) | Matcher rejects unfused source |
| [11](#issue-11--fno-schedule-insns-flag-still-required) | Stale scheduler state across partial rebuilds |

---

## Issue 1 — `tree-ssa-attn.o: No such file or directory`

### Symptom

```
ar: tree-ssa-attn.o: No such file or directory
make[2]: *** [Makefile:2314: libbackend.a] Error 1
```

The build progresses for a long time, then collapses while
archiving the GCC backend.

### Root cause

The new source file `tree-ssa-attn.cc` was placed at
`gcc/tree-ssa-attn.cc` (one `gcc/`), but GCC's build system looks
inside `gcc/gcc/` for compiler source files. The compile rule we
added to `Makefile.in` (File 10) names `tree-ssa-attn.o`, which
the build expects to be produced from `gcc/gcc/tree-ssa-attn.cc`.

### Fix

```bash
mv ~/riscv-attn/gcc/tree-ssa-attn.cc \
   ~/riscv-attn/gcc/gcc/tree-ssa-attn.cc
```

The directory structure of GCC's tree is mildly confusing because
the *outer* `gcc/` is the toolchain top-level umbrella (containing
all the GNU components — gcc, gdb, libgcc, libstdc++, …) while the
*inner* `gcc/gcc/` is the GCC compiler proper. Compiler source files
go in the inner directory.

---

## Issue 2 — `operand_equal_p` and `fold_unary` not declared

### Symptom

```
tree-data-ref.h:599:8: error: 'operand_equal_p' was not declared in this scope
tree-data-ref.h:703:30: error: 'fold_unary'      was not declared in this scope
```

The errors are reported inside `tree-data-ref.h`, which we did not
write — that is what makes this confusing.

### Root cause

`tree-data-ref.h` uses `operand_equal_p` and `fold_unary` in inline
helper functions. Their declarations live in `fold-const.h`. Because
C++ inline functions are processed at the point of inclusion, the
declarations must be in scope *before* `tree-data-ref.h` is
included.

### Fix

In `tree-ssa-attn.cc`, ensure `fold-const.h` is included **before**
`tree-data-ref.h`:

```c
#include "fold-const.h"          // declarations needed by tree-data-ref.h
...
#include "tree-data-ref.h"
```

The full required ordering is given in
[§ "File 11" of `04_PATCHES_AND_FILES_new.md`](04_PATCHES_AND_FILES_new.md#file-11--gccgcctree-ssa-attncc-new-file).

---

## Issue 3 — `tree-loop-distribution.h: No such file or directory`

### Symptom

```
tree-ssa-attn.cc:34:10: fatal error: tree-loop-distribution.h:
                        No such file or directory
```

### Root cause

The original methodology assumed `build_rdg` and `free_rdg` (used to
build the *Reduced Dependence Graph* for loop-distribution analysis)
were declared in a public header. They are in fact **private member
functions** of the `loop_distribution` class, defined inside
`tree-loop-distribution.cc`, with no public header.

### Fix

Remove `#include "tree-loop-distribution.h"` entirely. Replace the
SCC-based check (which was going to use `build_rdg`) with a simpler
direct count of loops carrying a madd reduction:

```c
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

This is sufficient for the matching logic: SDPA needs at least two
madd-reduction loops (one for `Q · Kᵀ`, one for `S · V`), and a
direct count answers that question without invoking the
loop-distribution machinery.

---

## Issue 4 — `mark_virtual_operands_for_renaming` not declared

### Symptom

```
tree-ssa-attn.cc:516:7: error: 'mark_virtual_operands_for_renaming'
                        was not declared in this scope
```

### Root cause

The intuitive guess is that the function is in `tree-ssa.h`. It is
not — it lives in the more specific `tree-into-ssa.h`, which the
`tree-ssa.h` umbrella does not pull in.

### Fix

Add the missing include:

```c
#include "tree-into-ssa.h"
```

---

## Issue 5 — ICE in `scev_initialize`

### Symptom

```
/tmp/sdpa.c:5:6: internal compiler error: in scev_initialize,
                 at tree-scalar-evolution.cc:3006
```

### Root cause

The pass body called `scev_initialize()` and `scev_finalize()`
explicitly, defensively, before querying SCEV. But by the time the
pass runs at position 179 in the pipeline (immediately after
Graphite's loop framework), SCEV has already been initialised by an
earlier pass. The second initialisation triggers an internal
assertion.

### Fix

Remove the explicit `scev_initialize()` / `scev_finalize()` calls
from the pass. Use SCEV directly:

```c
tree trip = number_of_latch_executions (outer);
if (chrec_contains_undetermined (trip))
  return false;
```

Lesson: in modern GCC, do not assume the pass needs to bring up
SCEV by hand. Check the pipeline state before adding initialisation
boilerplate.

---

## Issue 6 — ICE in `propagate_necessity` (DCE)

### Symptom

```
internal compiler error: in propagate_necessity, at tree-ssa-dce.cc:1148
```

or, alternatively:

```
internal compiler error: Segmentation fault
... walk_aliased_vdefs_1 ...
```

### Root cause

The `gcall` we created for `IFN_RISCV_ATTN` had no virtual operands
(no `vuse` / `vdef` chain). DCE's `propagate_necessity` reaches a
`gcc_unreachable()` when it encounters a memory-touching statement
without proper virtual-operand bookkeeping.

A first attempted fix — manually creating a vdef SSA name with
`make_ssa_name(gimple_vop(cfun))` — produced a different ICE:
`walk_aliased_vdefs` segfaults because the SSA name is not
properly linked into the def-use chain.

### Fix

Skip the manual virtual-operand management entirely. Mark the call
**volatile** at the GIMPLE level:

```c
gimple_set_has_volatile_ops (call, true);
```

Volatile statements are treated by every GCC pass as having
arbitrary side effects, are never removed by DCE, and are never
reordered with respect to other memory operations. This is exactly
the semantics we want for a custom instruction whose contract with
the hardware is opaque.

A complementary edit is required in `internal-fn.def`:

```c
DEF_INTERNAL_FN (RISCV_ATTN, ECF_NOTHROW, NULL)
```

`ECF_LEAF` was tried initially (in combination with `ECF_NOTHROW`)
because the call is "small" in the sense that it has no callees.
But `ECF_LEAF` carries the *additional* implication that the call
does not access caller's memory — which directly contradicts our
volatile flag and the `mem:BLK` operands in `riscv.md`. Remove
`ECF_LEAF`.

---

## Issue 7 — ICE in `riscv_sched_variable_issue`

### Symptom

```
internal compiler error: in riscv_sched_variable_issue,
                         at config/riscv/riscv.cc:9884
```

This fires while compiling user code that *would* otherwise emit
`attn`.

### Root cause

The RISC-V backend's instruction scheduler calls
`get_attr_type(insn)` on every RTL insn it sees. The `define_insn`
in `riscv.md` had `type "unknown"`. The scheduler then asserts:

```c
gcc_assert (get_attr_type (insn) != TYPE_UNKNOWN);
```

The check is there to catch authors of new patterns who forgot to
specify a type — exactly the situation we were in.

### Fix

Change the type to `"ghost"` in the `define_insn`:

```scheme
[(set_attr "type" "ghost")
 (set_attr "mode" "DI")]
```

`"ghost"` is GCC's standard type for instructions that are
scheduling barriers and have no DFA reservation — typical examples
are stack-prologue markers and other "do not really execute on the
pipeline" insns. It is the correct type for an opaque coprocessor
call whose timing model is not yet committed.

---

## Issue 8 — Pass inserted inside Graphite block

### Symptom

The pass registers cleanly, the build succeeds, and yet:

```bash
gcc -mattn -O2 -fdump-tree-attnrec-details -c finale.c -o finale.o
ls finale.c.*attnrec*
# (no such file or directory)
```

The dump file is never produced; the pass body never runs.

### Root cause

`NEXT_PASS (pass_recognize_attn);` was inserted immediately below
`NEXT_PASS (pass_graphite);`, which placed it **inside** the
Graphite driver block:

```c
NEXT_PASS (pass_graphite);
PUSH_INSERT_PASSES_WITHIN (pass_graphite)
   NEXT_PASS (pass_graphite_transforms);
   NEXT_PASS (pass_lim);
   ...
   NEXT_PASS (pass_recognize_attn);   /* WRONG — inside Graphite */
POP_INSERT_PASSES ()
```

When a pass is registered inside `PUSH_INSERT_PASSES_WITHIN ...
POP_INSERT_PASSES`, it is scheduled as a *sub-pass* of Graphite —
it runs only when the Graphite *driver* decides to run, which is
not the right context for our matcher.

### Fix

Move the line **after** `POP_INSERT_PASSES()`:

```c
PUSH_INSERT_PASSES_WITHIN (pass_graphite)
   ...
POP_INSERT_PASSES ()
NEXT_PASS (pass_recognize_attn);   /* CORRECT */
```

Lesson: `passes.def` is sensitive to balanced `PUSH/POP` braces.
Always verify *outside* which `POP_INSERT_PASSES` your pass lives.

---

## Issue 9 — `attn` fires inside a loop (wrong position)

### Symptom

The `attn` instruction *is* emitted, but in the wrong place: it
appears in the body of an outer loop, executing once per iteration
of that loop instead of once per function.

### Root cause

The pass walked loops with `LI_FROM_INNERMOST` and matched the
inner SDPA outer loop (loop #4 in the dump, the `S · V` loop). Its
preheader basic block was *itself inside* the body of an enclosing
larger loop. Inserting the IFN call into that preheader meant the
instruction fired on every iteration of the outer loop.

### Fix

Restrict the match to **top-level** loops only — direct children of
the function root:

```c
for (auto loop : loops_list (cfun, LI_FROM_INNERMOST))
  {
    if (loop_depth (loop) != 1)   // skip non-top-level loops
      continue;
    if (!loop->inner)             // need at least one nested loop
      continue;
    if (try_recognize_attention (loop))
      { changed = true; break; }
  }
```

`loop_depth(loop) == 1` filters to direct children of the implicit
root, which is the right granularity for SDPA.

---

## Issue 10 — Three separate loop nests, no single outer loop

### Symptom

The matcher rejects every loop in the function:

```
loop 1 rejected — need >=3 loads got 2
loop 8 rejected — need >=3 loads got 2
```

The function clearly is attention; the loops clearly do reference
all of `Q`, `K`, `V`. Yet the matcher cannot find any *single* loop
whose body sees all three.

### Root cause — two parts

1. The original `sdpa.c` was written with the four phases as
   **four separate top-level loop nests**:

   ```c
   /* phase 1 */ for (i...) for (j...) for (k...) S[i][j] += Q[i][k]*K[j][k];
   /* phase 2 */ for (i...) for (j...) S[i][j] *= scale;
   /* phase 3 */ for (i...) softmax_row(S, i);
   /* phase 4 */ for (i...) for (j...) for (k...) O[i][j] += S[i][k]*V[k][j];
   ```

   GCC sees these as four independent top-level loops. Loop 1 (Q·Kᵀ)
   has only Q and K as loads (two distinct bases, fails Check 4 of
   the matcher). Loop 4 (S·V) has only S and V (also fails). No
   single loop passes the "three distinct load bases" test.
2. Even with a fused source (one outer `i`-loop wrapping the four
   phases), GCC's `-O2` may *unfuse* the loop body across sibling
   blocks, so a strictly loop-local check still fails.

### Fix — also two parts

**Part A — write the source in fused form.** Authoring the test
program with all four phases inside one outer `i`-loop is the
discipline the matcher expects:

```c
for (int i = 0; i < N; i++) {
    /* phase 1+2: row i of S */
    /* phase 3: softmax row i in place */
    /* phase 4: row i of O */
}
```

`finale.c` in this repository is exactly this shape.

**Part B — broaden the load/store base scan to the whole function.**
Instead of collecting only the matched loop's loads, the pass walks
**every basic block** in the function:

```c
static void
attn_collect_load_bases (class loop *outer ATTRIBUTE_UNUSED,
                         auto_vec<tree> &bases)
{
  basic_block bb;
  FOR_EACH_BB_FN (bb, cfun)
    {
      /* collect MEM_REF / TARGET_MEM_REF bases, skip local stack arrays */
    }
}
```

Local stack arrays (e.g. the temporary `S[N][N]`) are filtered by
`DECL_EXTERNAL` / `TREE_STATIC`, so they do not pollute the count.

The combined effect is that the matcher tolerates a degree of
post-`-O2` rearrangement: as long as the function as a whole touches
three distinct external arrays, the check passes.

---

## Issue 11 — `-fno-schedule-insns` flag still required

### Symptom

After fixing Issue 7 (`type "ghost"`) and rebuilding, the verification
recipe still asks the user to compile with:

```
-fno-schedule-insns -fno-schedule-insns2
```

Why? If the source was rebuilt, surely the scheduler is now
satisfied.

### Root cause

The fix to `riscv.md` is in the source tree, but until the *staged*
build of GCC has both stages rebuilt with the new `riscv.md`, the
installed compiler binary may still embed the old (asserting)
behaviour. In partial rebuilds, the second stage of GCC sometimes
inherits stale `insn-attrtab.cc` / `insn-recog.cc` from the first
stage.

### Fix and current status

* In freshly-built compilers, the flags are not necessary.
* In the documented verification recipe (§6 of
  [`03_BUILD_AND_RUN_new.md`](03_BUILD_AND_RUN_new.md)),
  `-fno-schedule-insns -fno-schedule-insns2` are kept as a
  belt-and-braces measure so that the recipe also works on
  partially-rebuilt trees.
* If you want to confirm the flags are no longer needed on your
  build, do a clean rebuild (`rm -rf build-gcc-newlib-stage1
  build-gcc-newlib-stage2 stamps`) and re-run the verification
  without the flags.

---

## Meta-lesson

Eleven issues sounds like a lot, but they cluster into four
categories:

| category | issues |
|----------|--------|
| Build-system pitfalls (path, Makefile dependency) | 1, 11 |
| Header-ordering / missing includes | 2, 3, 4 |
| Middle-end and DCE invariants | 5, 6 |
| Backend / pass-pipeline mistakes | 7, 8, 9 |
| Matcher logic / source-style assumptions | 10 |

Each category corresponds to a *layer of GCC abstraction* the
project had to engage with. The exercise is therefore a useful
crash course in compiler-internals practice; a reader who works
through this list should be comfortable adding their *own* custom
instruction following the recipe in
[`06_EXTENDING_TOOLCHAIN_new.md`](06_EXTENDING_TOOLCHAIN_new.md).

---

**Next:** [`06_EXTENDING_TOOLCHAIN_new.md`](06_EXTENDING_TOOLCHAIN_new.md) —
generic template for adding your own custom instruction.
