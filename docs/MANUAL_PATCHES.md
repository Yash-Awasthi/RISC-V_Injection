# Manual Patches — Custom `attn` Instruction

**Author:** Yash Awasthi
All paths are relative to the root of `riscv-gnu-toolchain`.

This document records the exact change made to each file.
Every edit is verified with a `grep` check shown after it.

---

## File 1 — `binutils/include/opcode/riscv-opc.h`

### Change A — Add MATCH/MASK macros

Inserted immediately above the first `#define MATCH_ADD` line:

```c
#define MATCH_ATTN 0x0000000b
#define MASK_ATTN  0x0600707f
```

Verify:
```bash
grep -n 'MATCH_ATTN\|MASK_ATTN' binutils/include/opcode/riscv-opc.h
# Expected: 2 hits
```

### Change B — Add DECLARE_INSN entry

Inserted immediately above `DECLARE_INSN(add,`:

```c
DECLARE_INSN(attn, MATCH_ATTN, MASK_ATTN)
```

Verify:
```bash
grep -n 'DECLARE_INSN(attn' binutils/include/opcode/riscv-opc.h
# Expected: 1 hit
```

---

## File 2 — `binutils/opcodes/riscv-opc.c`

### Change — Add opcode table entry

Inserted immediately above the `{"unimp",` entry (first real entry of
`riscv_opcodes[]`):

```c
{"attn",        0, INSN_CLASS_I,  "d,s,t,r",  MATCH_ATTN, MASK_ATTN, match_opcode, 0 },
```

Note: the format string is `"d,s,t,r"` — four registers for R4-type.
The original Phase 1 doc used `"d,s,t"` (3 registers). This was
updated to `"d,s,t,r"` during Phase 2 when the instruction was
changed from R-type to R4-type.

Verify:
```bash
grep -n '"attn"' binutils/opcodes/riscv-opc.c
# Expected: exactly 1 hit
```

---

## File 3 — `gcc/gcc/config/riscv/riscv.opt`

### Change — Add -mattn flag

Appended at end of file (no blank line between flag name and Target line):

```
mattn
Target Var(TARGET_ATTN) Init(0)
Enable the custom fused-attention instruction.
```

The `.opt` grammar requires that the flag name line and `Target ...`
line are adjacent — no blank line between them.

Verify:
```bash
grep -n 'TARGET_ATTN\|^mattn' gcc/gcc/config/riscv/riscv.opt
# Expected: 2 hits
```

---

## File 4 — `gcc/gcc/config/riscv/riscv.md`

### Change A — Add UNSPEC constant

Inside the existing `define_c_enum "unspec"` block, before its closing `])`:

```
  UNSPEC_RISCV_ATTN
```

### Change B — Add define_insn

Inserted immediately above `(define_insn "nop"`:

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

Note: `type "ghost"` was chosen after discovering that `type "unknown"`
causes an assert in `riscv_sched_variable_issue` when the RTL scheduler
encounters the instruction. Ghost instructions are treated as blockages
and require no DFA reservation.

Verify:
```bash
grep -n 'riscv_attn\|UNSPEC_RISCV_ATTN' gcc/gcc/config/riscv/riscv.md
# Expected: multiple hits for both
```

---

## File 5 — `gcc/gcc/internal-fn.def`

### Change — Declare IFN_RISCV_ATTN

Added near other `DEF_INTERNAL_FN` entries:

```c
DEF_INTERNAL_FN (RISCV_ATTN, ECF_NOTHROW, NULL)
```

Note: `ECF_LEAF` was removed after discovering it causes DCE to treat
the call as not touching memory, leading to an ICE in
`propagate_necessity` (tree-ssa-dce.cc).

Verify:
```bash
grep -n 'RISCV_ATTN' gcc/gcc/internal-fn.def
# Expected: 1 hit
```

---

## File 6 — `gcc/gcc/internal-fn.cc`

### Change — Add expand_RISCV_ATTN expander

Placed near other `expand_*` definitions, anchored near `expand_UBSAN_NULL`:

```c
static void
expand_RISCV_ATTN (internal_fn, gcall *stmt)
{
  /* R4-type: attn rd, rs1, rs2, rs3
       rd  = O pointer  (output array)
       rs1 = Q pointer
       rs2 = K pointer
       rs3 = V pointer  */
  rtx out = expand_normal (gimple_call_arg (stmt, 0));
  rtx q   = expand_normal (gimple_call_arg (stmt, 1));
  rtx k   = expand_normal (gimple_call_arg (stmt, 2));
  rtx v   = expand_normal (gimple_call_arg (stmt, 3));
  out = force_reg (Pmode, out);
  q   = force_reg (Pmode, q);
  k   = force_reg (Pmode, k);
  v   = force_reg (Pmode, v);
  emit_insn (gen_riscv_attn (out, q, k, v));
}
```

The dispatch from `IFN_RISCV_ATTN` → `expand_RISCV_ATTN` is wired
automatically by the macro machinery in `internal-fn.def`.

Verify:
```bash
grep -n 'expand_RISCV_ATTN' gcc/gcc/internal-fn.cc
# Expected: 1 hit (the function definition)
```

---

## File 7 — `gcc/gcc/config/riscv/riscv.cc`

### Change — Add option validation warning

Inside `riscv_option_override`, near existing flag validation:

```c
  if (TARGET_ATTN && !TARGET_64BIT)
    warning (0, "%<-mattn%> has only been validated on RV64; "
                "rv32 codegen is experimental");
```

Note: the original methodology proposed an `error()` here. Changed to
`warning()` because the instruction encoding is 32-bit regardless of
whether the ISA is rv32 or rv64. A hard error would break rv32 Spike
testing in Phase 4.

---

## File 8 — `gcc/gcc/passes.def`

### Change — Register pass after Graphite block

The Graphite block in `passes.def` has this structure:

```c
NEXT_PASS (pass_graphite);
PUSH_INSERT_PASSES_WITHIN (pass_graphite)
   NEXT_PASS (pass_graphite_transforms);
   NEXT_PASS (pass_lim);
   ...
POP_INSERT_PASSES ()           ← anchor here
NEXT_PASS (pass_recognize_attn);   ← inserted here
```

The pass must go AFTER `POP_INSERT_PASSES()`, not inside the Graphite
block. Inserting inside the block places it in the wrong driver scope.

Verify:
```bash
grep -n 'pass_recognize_attn' gcc/gcc/passes.def
# Expected: 1 hit
```

---

## File 9 — `gcc/gcc/tree-pass.h`

### Change — Declare pass factory

Inserted immediately below the `make_pass_graphite` declaration:

```c
extern gimple_opt_pass *make_pass_recognize_attn (gcc::context *ctxt);
```

Verify:
```bash
grep -n 'make_pass_recognize_attn' gcc/gcc/tree-pass.h
# Expected: 1 hit
```

---

## File 10 — `gcc/gcc/Makefile.in`

### Change — Add tree-ssa-attn.o to OBJS

Inserted immediately below the `tree-ssa-math-opts.o \` line
(chosen as anchor because it appears exactly once, unlike
`tree-ssa-loop.o` which appears multiple times):

```
	tree-ssa-attn.o \
```

After adding, force the build Makefile to regenerate:
```bash
find ~/riscv-gnu-toolchain -path '*/gcc/Makefile' -delete
```

Verify:
```bash
grep -n 'tree-ssa-attn.o' gcc/gcc/Makefile.in
# Expected: 1 hit

grep -n 'tree-ssa-math-opts.o' gcc/gcc/Makefile.in
# Expected: 1 hit (the anchor)
```

---

## File 11 — `gcc/gcc/tree-ssa-attn.cc` (new file)

New file created at `gcc/gcc/tree-ssa-attn.cc`.

**Common mistake:** The file must be at `gcc/gcc/tree-ssa-attn.cc`,
not at `gcc/tree-ssa-attn.cc`. The build system looks in `gcc/gcc/`.

Headers required (discovered through build errors):

```c
#include "fold-const.h"       // operand_equal_p, fold_unary
#include "tree-ssa.h"         // (attempted — not sufficient alone)
#include "tree-into-ssa.h"    // mark_virtual_operands_for_renaming
#include "tree-eh.h"          // tree exception handling
```

The `tree-data-ref.h` header requires `fold-const.h` to be included
before it — `operand_equal_p` and `fold_unary` are declared in
`fold-const.h` and used inside `tree-data-ref.h` inline functions.

`graphds.h` was initially included for `build_rdg`/`free_rdg`, but
these are methods of the `loop_distribution` class (not free functions)
and cannot be called from outside that class. The SCC check was
replaced with a direct loop count approach.

See the full file source in `gcc/gcc/tree-ssa-attn.cc`.
