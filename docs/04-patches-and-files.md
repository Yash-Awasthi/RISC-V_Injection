# 04 — Patches and Files Reference

> **Audience.** Anyone reviewing the project's deltas against
> upstream `riscv-gnu-toolchain`. This document lists *every* file
> the project modifies or creates, the exact text of the change, the
> reason the change is needed, and a `grep` command to verify it is
> in place.

A modification of this scale touches **eleven files**: two in
binutils, eight in the GCC tree, and one new file. Each section
below is self-contained and can be read in any order, but the
files are ordered by the layer of the toolchain they belong to.

---

## Table of contents — the eleven files

| # | File | Layer | What changes |
|---|------|-------|---------------|
| 1 | [`binutils/include/opcode/riscv-opc.h`](#file-1--binutilsincludeopcoderisc-vopch) | encoding | `MATCH_ATTN`, `MASK_ATTN`, `DECLARE_INSN` |
| 2 | [`binutils/opcodes/riscv-opc.c`](#file-2--binutilsopcodesriscv-opcc) | encoding | opcode-table row |
| 3 | [`gcc/gcc/config/riscv/riscv.opt`](#file-3--gccgccconfigriscvriscvopt) | flag | new `-mattn` flag |
| 4 | [`gcc/gcc/config/riscv/riscv.md`](#file-4--gccgccconfigriscvriscvmd) | RTL | `UNSPEC_RISCV_ATTN` + `define_insn "riscv_attn"` |
| 5 | [`gcc/gcc/internal-fn.def`](#file-5--gccgccinternal-fndef) | IR | `IFN_RISCV_ATTN` declaration |
| 6 | [`gcc/gcc/internal-fn.cc`](#file-6--gccgccinternal-fncc) | IR | `expand_RISCV_ATTN` expander |
| 7 | [`gcc/gcc/config/riscv/riscv.cc`](#file-7--gccgccconfigriscvriscvcc) | flag | rv32 warning in `riscv_option_override` |
| 8 | [`gcc/gcc/passes.def`](#file-8--gccgccpassesdef) | pipeline | pass registration |
| 9 | [`gcc/gcc/tree-pass.h`](#file-9--gccgcctree-passh) | pipeline | factory function declaration |
| 10 | [`gcc/gcc/Makefile.in`](#file-10--gccgccmakefilein) | build | new object file in `OBJS` |
| 11 | [`gcc/gcc/tree-ssa-attn.cc`](#file-11--gccgcctree-ssa-attncc-new-file) | pass body | **new file**, ~500 lines |

All paths are relative to the root of the `riscv-gnu-toolchain`
checkout, which in this repository is the repository root itself.

---

## File 1 — `binutils/include/opcode/riscv-opc.h`

This header is the central encoding registry for RISC-V binutils.
Both the assembler (GAS) and the disassembler (`objdump`) include
it. Two distinct edits are required.

### Change A — `MATCH` and `MASK` macros

Inserted immediately above the first `#define MATCH_ADD` line:

```c
#define MATCH_ATTN  0x0000000b
#define MASK_ATTN   0x0600707f
```

The numbers come from the bit-field layout in
[§3 of `01-instruction-spec.md`](01-instruction-spec.md#3-encoding-constants-match-and-mask).
`MATCH_ATTN` has the locked sub-fields (opcode, funct3, funct2)
in their fixed positions; `MASK_ATTN` is `1` precisely on those
locked sub-fields and `0` everywhere else.

Verify:

```bash
grep -n 'MATCH_ATTN\|MASK_ATTN' binutils/include/opcode/riscv-opc.h
# Expected: 2 hits
```

### Change B — `DECLARE_INSN` entry

Inserted immediately above `DECLARE_INSN(add,`, *inside* the
`#ifdef DECLARE_INSN` block:

```c
DECLARE_INSN(attn, MATCH_ATTN, MASK_ATTN)
```

The `DECLARE_INSN` macro is only defined inside the
`#ifdef DECLARE_INSN ... #endif` guard. Placing the line outside
the guard is silently ignored or, worse, fails to compile in some
configurations — a mistake from an earlier prototype documented in
the old known-issues file.

Verify:

```bash
grep -n 'DECLARE_INSN(attn' binutils/include/opcode/riscv-opc.h
# Expected: 1 hit
```

---

## File 2 — `binutils/opcodes/riscv-opc.c`

The assembler's main lookup table is the array `riscv_opcodes[]`.
Add one row, placed immediately above the first real entry
(traditionally `{"unimp", ...}`):

```c
{"attn",  0,  INSN_CLASS_I,  "d,s,t,r",  MATCH_ATTN, MASK_ATTN, match_opcode, 0 },
```

The fields, decoded:

| field | value | meaning |
|-------|-------|---------|
| name      | `"attn"`        | mnemonic the assembler reads |
| xlen      | `0`             | works on rv32 *and* rv64 |
| isa class | `INSN_CLASS_I`  | base integer ISA, no FP unit needed |
| operands  | `"d,s,t,r"`     | rd, rs1, rs2, rs3 — note the `r` makes this R4-type |
| match     | `MATCH_ATTN`    | from File 1 |
| mask      | `MASK_ATTN`     | from File 1 |
| match_fn  | `match_opcode`  | the standard `(insn & mask) == match` predicate |
| pinfo     | `0`             | no special flags |

The operand string is identical to the one used by `fmadd`, which is
also R4-type — useful evidence the encoding is consistent with the
standard ISA.

Verify:

```bash
grep -n '"attn"' binutils/opcodes/riscv-opc.c
# Expected: exactly 1 hit
```

---

## File 3 — `gcc/gcc/config/riscv/riscv.opt`

Append (with no blank line between the flag-name line and the
`Target ...` line — the GCC `.opt` grammar is whitespace-sensitive):

```
mattn
Target Var(TARGET_ATTN) Init(0)
Enable the custom fused-attention instruction.
```

This produces:

* the user-visible flag `-mattn`;
* the C macro `TARGET_ATTN`, an integer that is non-zero when the
  flag is given;
* the description that appears in `gcc --help=target`.

`TARGET_ATTN` is what gates both the `attnrec` pass and the
`define_insn` predicate (Files 4 and 11).

Verify:

```bash
grep -n 'TARGET_ATTN\|^mattn' gcc/gcc/config/riscv/riscv.opt
# Expected: 2 hits
```

---

## File 4 — `gcc/gcc/config/riscv/riscv.md`

Two edits in this file: an enum entry and a new instruction pattern.

### Change A — UNSPEC constant

Inside the existing `define_c_enum "unspec"` block, before its
closing `])`, add a line:

```
  UNSPEC_RISCV_ATTN
```

`UNSPEC` (UNspecified) is GCC's RTL category for "an opaque
target-defined operation that the optimiser must not try to
simplify". It is exactly what we want for a custom instruction.

### Change B — `define_insn`

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

Three subtleties live in this small pattern:

* **`mem:BLK`** declares each of the four pointer operands as
  pointing into a block of memory of unknown size. This blocks
  load-store optimisations from reordering ordinary loads/stores
  across the instruction.
* **`UNSPEC_RISCV_ATTN`** ties the pattern to the enum from
  Change A.
* **`type "ghost"`** — the RISC-V scheduler asserts that every
  `define_insn` it processes has a known `type` attribute and a DFA
  reservation. The choice of `"unknown"` triggers an ICE in
  `riscv_sched_variable_issue`; `"ghost"` is the canonical type for
  scheduling barriers and is exactly right for an opaque coprocessor
  call. See
  [`05-troubleshooting.md` Issue 7](05-troubleshooting.md#issue-7--ice-in-riscv_sched_variable_issue).

Verify:

```bash
grep -n 'riscv_attn\|UNSPEC_RISCV_ATTN' gcc/gcc/config/riscv/riscv.md
# Expected: multiple hits for both
```

---

## File 5 — `gcc/gcc/internal-fn.def`

Add (near other `DEF_INTERNAL_FN` entries, alphabetical order
preferred):

```c
DEF_INTERNAL_FN (RISCV_ATTN, ECF_NOTHROW, NULL)
```

This declares the new internal function `IFN_RISCV_ATTN`, makes the
`expand_RISCV_ATTN` C++ symbol available, and tells GCC the call
properties:

* **`ECF_NOTHROW`** — cannot raise C++ exceptions;
* note the *absence* of `ECF_LEAF` — leaf functions are assumed not
  to touch memory, which contradicts our four-pointer
  read/write contract and triggers a DCE ICE
  (see [`05-troubleshooting.md` Issue 6](05-troubleshooting.md#issue-6--ice-in-propagate_necessity-dce)).

The third argument `NULL` says we have no fold/simplify hook for the
function — there is no algebraic simplification that applies.

Verify:

```bash
grep -n 'RISCV_ATTN' gcc/gcc/internal-fn.def
# Expected: 1 hit
```

---

## File 6 — `gcc/gcc/internal-fn.cc`

Add the expander near other `expand_*` definitions (a convenient
anchor is `expand_UBSAN_NULL`):

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

The function dispatches the four GIMPLE call arguments to RTL,
forces each into a Pmode register (so the RTL pattern's
`register_operand` predicate is satisfied), and emits the RTL
insn via the auto-generated `gen_riscv_attn` helper.

The wiring `IFN_RISCV_ATTN → expand_RISCV_ATTN` is performed
automatically by the macro machinery in `internal-fn.def` — no
manual dispatch table is needed.

Verify:

```bash
grep -n 'expand_RISCV_ATTN' gcc/gcc/internal-fn.cc
# Expected: 1 hit (the function definition)
```

---

## File 7 — `gcc/gcc/config/riscv/riscv.cc`

Inside `riscv_option_override`, near other validation code:

```c
  if (TARGET_ATTN && !TARGET_64BIT)
    warning (0, "%<-mattn%> has only been validated on RV64; "
                "rv32 codegen is experimental");
```

A *warning*, not an *error*. The earlier methodology proposed
hard-erroring on rv32, but the encoding is genuinely 32-bit and
XLEN-independent; refusing to compile would block future Spike
tests on rv32. The soft warning preserves user choice while
flagging the unvalidated configuration.

---

## File 8 — `gcc/gcc/passes.def`

The Graphite block in `passes.def` has the structure:

```c
NEXT_PASS (pass_graphite);
PUSH_INSERT_PASSES_WITHIN (pass_graphite)
   NEXT_PASS (pass_graphite_transforms);
   NEXT_PASS (pass_lim);
   ...
POP_INSERT_PASSES ()                  /* end of Graphite */
NEXT_PASS (pass_recognize_attn);      /* ← INSERT HERE */
```

The new line goes **after** `POP_INSERT_PASSES()`, *not* inside the
`PUSH_INSERT_PASSES_WITHIN ... POP_INSERT_PASSES` block. Inserting
inside the block would put the pass under Graphite's driver, where
it would silently never execute — Issue 8 in the troubleshooting
log.

Verify:

```bash
grep -n 'pass_recognize_attn' gcc/gcc/passes.def
# Expected: 1 hit
```

---

## File 9 — `gcc/gcc/tree-pass.h`

Inserted immediately below the existing `make_pass_graphite`
declaration:

```c
extern gimple_opt_pass *make_pass_recognize_attn (gcc::context *ctxt);
```

This is the factory function defined at the bottom of
`tree-ssa-attn.cc` (File 11). `passes.def` calls it implicitly via
the `NEXT_PASS` macro from File 8.

Verify:

```bash
grep -n 'make_pass_recognize_attn' gcc/gcc/tree-pass.h
# Expected: 1 hit
```

---

## File 10 — `gcc/gcc/Makefile.in`

Insert below the `tree-ssa-math-opts.o \` line — chosen as the
anchor because it appears exactly once in the file, unlike
`tree-ssa-loop.o` which appears multiple times and would be
ambiguous:

```
	tree-ssa-attn.o \
```

(The leading character is a literal **TAB**, not spaces; the
Makefile language requires it.)

After this edit, force the per-target Makefile to be regenerated so
the new dependency is picked up by `make`:

```bash
find ~/riscv-attn -path '*/gcc/Makefile' -delete
```

Verify:

```bash
grep -n 'tree-ssa-attn.o' gcc/gcc/Makefile.in
# Expected: 1 hit

grep -n 'tree-ssa-math-opts.o' gcc/gcc/Makefile.in
# Expected: 1 hit (the chosen anchor)
```

---

## File 11 — `gcc/gcc/tree-ssa-attn.cc` (new file)

This is the only **new** file in the modification. It implements the
`attnrec` pass — the pattern matcher and the IFN emitter. Roughly
500 lines.

> **Common mistake.** The file's home is `gcc/gcc/tree-ssa-attn.cc`
> — *two* `gcc` directories. Placing it at `gcc/tree-ssa-attn.cc`
> (one `gcc` directory) causes the build to fail with
> `tree-ssa-attn.o: No such file or directory`, because the build
> system looks under the inner `gcc/`. See
> [`05-troubleshooting.md` Issue 1](05-troubleshooting.md#issue-1--tree-ssa-attno-no-such-file-or-directory).

The file's required headers, in the order GCC expects them:

```c
#define INCLUDE_MEMORY
#include "config.h"
#include "system.h"
#include "coretypes.h"
#include "backend.h"
#include "tree.h"
#include "gimple.h"
#include "tree-pass.h"
#include "ssa.h"
#include "fold-const.h"           /* must precede tree-data-ref.h */
#include "gimple-iterator.h"
#include "cfgloop.h"
#include "tree-cfg.h"
#include "tree-ssa-loop.h"
#include "tree-scalar-evolution.h"
#include "internal-fn.h"
#include "tree-data-ref.h"
#include "tree-eh.h"
#include "tree-ssa.h"
#include "tree-into-ssa.h"        /* mark_virtual_operands_for_renaming */
#include "builtins.h"
```

Two header-ordering rules are non-obvious and were learnt by build
errors:

* **`fold-const.h` *before* `tree-data-ref.h`** — the latter uses
  `operand_equal_p` and `fold_unary` in inline functions, both
  declared in `fold-const.h`. The wrong order surfaces as
  "`operand_equal_p` was not declared in this scope" inside
  `tree-data-ref.h`, which is confusing because the offending name
  is not even in our file.
* **`tree-into-ssa.h` is required**, because
  `mark_virtual_operands_for_renaming` is declared there, not in
  the more obviously named `tree-ssa.h`.

Header `graphds.h` was tried for `build_rdg`/`free_rdg`. Those
symbols are *not* free functions but private methods of the
`loop_distribution` class, so they cannot be called from outside
that class. The pass uses a direct loop count instead — the SCC
check from the original methodology was replaced with a count of
loops carrying madd reductions.

The full source is the single best reference for the pass body and
should be read alongside [`02-compiler-pass.md`](02-compiler-pass.md).

---

## Cross-reference table

If you want to know "which file fixes problem X", this table maps
the eleven files to the specific design problems they solve.

| design problem | file(s) |
|----------------|---------|
| The assembler must accept `attn` | 1, 2 |
| `objdump` must disassemble `attn` | 1 (uses same MATCH/MASK) |
| `-mattn` must be a real flag | 3 |
| GCC must know how to *print* the assembly | 4 |
| GIMPLE must have a stable place to put the abstract operation | 5 |
| The abstract IFN must lower to RTL | 6 |
| Users on rv32 must see a warning | 7 |
| The pattern-matching pass must run at the right place | 8 |
| The pass factory must be visible to `passes.def` | 9 |
| `make` must compile the new pass | 10 |
| The pattern matching itself | 11 |

This is essentially the "responsibilities matrix" for the project.

---

**Next:** [`05-troubleshooting.md`](05-troubleshooting.md) —
every error, ICE, and silent misbehaviour encountered during the
project, with the root cause and the fix.
