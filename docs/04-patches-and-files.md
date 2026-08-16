# 04 — Patches and Files Reference

> **Audience.** Anyone reviewing the project's deltas against
> upstream `riscv-gnu-toolchain`. This document lists *every* file
> the project modifies or creates, the exact text of the change, the
> reason the change is needed, and a `grep` command to verify it is
> in place.

A modification of this scale touches **twelve files**: two in
binutils, nine in the GCC tree — of which six live under
`gcc/gcc/config/riscv/` — and one new file. Each section below is
self-contained and can be read in any order, but the files are
ordered by the layer of the toolchain they belong to.

The pattern-matching pass (File 12) talks to the backend only
through a normal target builtin, `__builtin_riscv_attn`, declared
and expanded entirely inside `config/riscv/`. No core GCC file
outside `config/riscv/` — `internal-fn.def`, `internal-fn.cc` — needs
to know this instruction exists.

---

## Table of contents — the twelve files

| # | File | Layer | What changes |
|---|------|-------|---------------|
| 1 | [`binutils/include/opcode/riscv-opc.h`](#file-1--binutilsincludeopcoderisc-vopch) | encoding | `MATCH_ATTN`, `MASK_ATTN`, `DECLARE_INSN` |
| 2 | [`binutils/opcodes/riscv-opc.c`](#file-2--binutilsopcodesriscv-opcc) | encoding | opcode-table row |
| 3 | [`gcc/gcc/config/riscv/riscv.opt`](#file-3--gccgccconfigriscvriscvopt) | flag | new `-mattn` flag |
| 4 | [`gcc/gcc/config/riscv/riscv.md`](#file-4--gccgccconfigriscvriscvmd) | RTL | `UNSPEC_RISCV_ATTN` + `define_insn "riscv_attn"` |
| 5 | [`gcc/gcc/config/riscv/riscv-ftypes.def`](#file-5--gccgccconfigriscvriscv-ftypesdef) | builtin | function prototype for `__builtin_riscv_attn` |
| 6 | [`gcc/gcc/config/riscv/riscv-builtins.cc`](#file-6--gccgccconfigriscvriscv-builtinscc) | builtin | builtin table row + decl accessor |
| 7 | [`gcc/gcc/config/riscv/riscv-protos.h`](#file-7--gccgccconfigriscvriscv-protosh) | builtin | accessor declaration |
| 8 | [`gcc/gcc/config/riscv/riscv.cc`](#file-8--gccgccconfigriscvriscvcc) | flag | rv32 warning in `riscv_option_override` |
| 9 | [`gcc/gcc/passes.def`](#file-9--gccgccpassesdef) | pipeline | pass registration |
| 10 | [`gcc/gcc/tree-pass.h`](#file-10--gccgcctree-passh) | pipeline | factory function declaration |
| 11 | [`gcc/gcc/Makefile.in`](#file-11--gccgccmakefilein) | build | new object file in `OBJS` |
| 12 | [`gcc/gcc/tree-ssa-attn.cc`](#file-12--gccgcctree-ssa-attncc-new-file) | pass body | **new file**, ~500 lines |

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

## File 5 — `gcc/gcc/config/riscv/riscv-ftypes.def`

Append one prototype line:

```c
DEF_RISCV_FTYPE (4, (VOID, VOID_PTR, VOID_PTR, VOID_PTR, VOID_PTR))
```

This declares the C signature of `__builtin_riscv_attn`: a
`void`-returning function taking the four raw `void *` operands
(O, Q, K, V). `riscv_build_function_type` turns this line into a
real `tree` function type the first time the prototype is used.

Verify:

```bash
grep -n 'VOID_PTR, VOID_PTR, VOID_PTR, VOID_PTR' gcc/gcc/config/riscv/riscv-ftypes.def
# Expected: 1 hit
```

---

## File 6 — `gcc/gcc/config/riscv/riscv-builtins.cc`

Three edits, all inside the existing builtin machinery.

### Change A — availability predicate

Next to the other `AVAIL` declarations:

```c
AVAIL (attn, TARGET_ATTN)
```

### Change B — the builtin table row

Appended to the `riscv_builtins[]` array:

```c
DIRECT_NO_TARGET_BUILTIN (attn,
                          RISCV_VOID_FTYPE_VOID_PTR_VOID_PTR_VOID_PTR_VOID_PTR,
                          attn),
```

`DIRECT_NO_TARGET_BUILTIN` wires the name `__builtin_riscv_attn` to
instruction `CODE_FOR_riscv_attn` (the `define_insn "riscv_attn"`
from File 4). `NO_TARGET` because the instruction returns nothing —
all four operands are inputs, matching the four-pointer contract in
`attn_emit_replacement`. `riscv_init_builtins` only registers the
decl when `TARGET_ATTN` is set; with `-mattn` absent, the builtin
does not exist and the pass must not run (see File 12's gate).

### Change C — decl accessor

Added after `riscv_builtin_decl`:

```c
tree
riscv_builtin_decl_attn (void)
{
  return GET_BUILTIN_DECL (CODE_FOR_riscv_attn);
}
```

`tree-ssa-attn.cc` (File 12) is a GIMPLE pass, not the C front end,
so it cannot spell `__builtin_riscv_attn()` as source text. It needs
the `FUNCTION_DECL` tree directly, and `GET_BUILTIN_DECL` — the same
macro `riscv_atomic_assign_expand_fenv` already uses for `frflags`
and `fsflags` — is the established way to fetch one by instruction
code from outside the translation unit that built the table.

Verify:

```bash
grep -n 'attn' gcc/gcc/config/riscv/riscv-builtins.cc
# Expected: hits for AVAIL, the builtin row, and riscv_builtin_decl_attn
```

---

## File 7 — `gcc/gcc/config/riscv/riscv-protos.h`

One declaration, next to `riscv_builtin_decl`:

```c
extern tree riscv_builtin_decl_attn (void);
```

Verify:

```bash
grep -n 'riscv_builtin_decl_attn' gcc/gcc/config/riscv/riscv-protos.h
# Expected: 1 hit
```

---

## File 8 — `gcc/gcc/config/riscv/riscv.cc`

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

## File 9 — `gcc/gcc/passes.def`

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

## File 10 — `gcc/gcc/tree-pass.h`

Inserted immediately below the existing `make_pass_graphite`
declaration:

```c
extern gimple_opt_pass *make_pass_recognize_attn (gcc::context *ctxt);
```

This is the factory function defined at the bottom of
`tree-ssa-attn.cc` (File 12). `passes.def` calls it implicitly via
the `NEXT_PASS` macro from File 9.

Verify:

```bash
grep -n 'make_pass_recognize_attn' gcc/gcc/tree-pass.h
# Expected: 1 hit
```

---

## File 11 — `gcc/gcc/Makefile.in`

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

## File 12 — `gcc/gcc/tree-ssa-attn.cc` (new file)

This is the only **new** file in the modification. It implements the
`attnrec` pass — the pattern matcher and the emitter that replaces a
recognized loop nest with a call to `__builtin_riscv_attn`. Roughly
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
#include "tree-data-ref.h"
#include "tree-eh.h"
#include "tree-ssa.h"
#include "tree-into-ssa.h"        /* mark_virtual_operands_for_renaming */
#include "builtins.h"
#include "config/riscv/riscv-protos.h" /* riscv_builtin_decl_attn */
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

`attn_emit_replacement` no longer builds an internal-fn call. It
fetches the `FUNCTION_DECL` for `__builtin_riscv_attn` from File 6's
`riscv_builtin_decl_attn ()` and passes it straight to
`gimple_build_call`, the same way any other GIMPLE pass calls a
known builtin. Because the builtin decl only exists when `-mattn`
registered it, the pass gate checks `TARGET_ATTN` in addition to
`TARGET_ATTN_RECOGNIZE` — recognizing the idiom without the
instruction to replace it with would call a stale or wrong decl.

The full source is the single best reference for the pass body and
should be read alongside [`02-compiler-pass.md`](02-compiler-pass.md).

---

## Cross-reference table

If you want to know "which file fixes problem X", this table maps
the twelve files to the specific design problems they solve.

| design problem | file(s) |
|----------------|---------|
| The assembler must accept `attn` | 1, 2 |
| `objdump` must disassemble `attn` | 1 (uses same MATCH/MASK) |
| `-mattn` must be a real flag | 3 |
| GCC must know how to *print* the assembly | 4 |
| `__builtin_riscv_attn` must have a C-callable prototype | 5 |
| The builtin must be registered and lower to RTL | 6 |
| The pass must be able to fetch the builtin decl | 7 |
| Users on rv32 must see a warning | 8 |
| The pattern-matching pass must run at the right place | 9 |
| The pass factory must be visible to `passes.def` | 10 |
| `make` must compile the new pass | 11 |
| The pattern matching itself, and calling the builtin | 12 |

This is essentially the "responsibilities matrix" for the project.

---

**Next:** [`05-troubleshooting.md`](05-troubleshooting.md) —
every error, ICE, and silent misbehaviour encountered during the
project, with the root cause and the fix.
