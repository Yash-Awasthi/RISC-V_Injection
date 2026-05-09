# 06 — Adding Your Own Custom RISC-V Instruction

> **Audience.** A researcher or engineer who has read the rest of
> this documentation set and now wants to add a *different* custom
> instruction (LayerNorm, RMSNorm, fused-FFN, RoPE, GEMM-with-bias —
> anything). This document distils the project's experience into a
> reusable template.

The template is independent of `attn`. Read it as
"every step you must take, in order, to add any new instruction".
The eleven files of [`04-patches-and-files.md`](04-patches-and-files.md)
are the concrete instantiation of this generic recipe for
`attn`.

---

## Table of contents

1. [Choose an opcode slot and a format](#1-choose-an-opcode-slot-and-a-format)
2. [Compute `MATCH` and `MASK`](#2-compute-match-and-mask)
3. [Update binutils (encoding registry)](#3-update-binutils-encoding-registry)
4. [Add a `-myinsn` GCC flag](#4-add-a--myinsn-gcc-flag)
5. [Add the RTL `define_insn`](#5-add-the-rtl-define_insn)
6. [Declare an `IFN` and write its expander](#6-declare-an-ifn-and-write-its-expander)
7. [Decide: idiom recognition or intrinsic?](#7-decide-idiom-recognition-or-intrinsic)
8. [Wire the new pass into the build](#8-wire-the-new-pass-into-the-build)
9. [Skeleton of a pattern-matching pass](#9-skeleton-of-a-pattern-matching-pass)
10. [Test and iterate](#10-test-and-iterate)
11. [Common pitfalls (read these first)](#11-common-pitfalls-read-these-first)

---

## 1. Choose an opcode slot and a format

### Slot

```
| Slot     | opcode[6:0] | hex    |
|----------|-------------|--------|
| custom-0 | 0001011     | 0x0b   |  <- used by attn in this project
| custom-1 | 0101011     | 0x2b   |
| custom-2 | 1011011     | 0x5b   |
| custom-3 | 1111011     | 0x7b   |
```

If you are adding a *second* instruction to the same toolchain, use
a different `funct3`/`funct2` (or `funct7`) within the same slot,
or move to a different slot entirely.

### Format

| Format | Operand layout | Use when you need |
|--------|----------------|-------------------|
| **R-type**  | `rd, rs1, rs2`             | three registers (e.g. fused multiply) |
| **R4-type** | `rd, rs1, rs2, rs3`        | four registers (e.g. `attn`, `fmadd`) |
| **I-type**  | `rd, rs1, imm12`           | a register and a 12-bit immediate |
| **S-type**  | `rs1, rs2, imm`            | store-style with no destination register |
| **U-type**  | `rd, imm20`                | a single 20-bit immediate (e.g. `lui`) |

For accelerator-style "give me four pointers" instructions, R4-type
is the natural fit.

For a single-pointer/single-scalar configuration register write
(LayerNorm with one γ, one β, one ε broadcast), I-type or S-type
may be more appropriate.

The bit layouts:

```
R-type
 31      25 24   20 19   15 14  12 11    7 6      0
+----------+-------+-------+-------+-------+--------+
|  funct7  |  rs2  |  rs1  | funct3|  rd   | opcode |
+----------+-------+-------+-------+-------+--------+

R4-type
 31    27 26 25 24    20 19    15 14   12 11    7 6      0
+--------+-----+--------+--------+-------+--------+--------+
|  rs3   |  f2 |  rs2   |  rs1   | funct3|  rd   | opcode |
+--------+-----+--------+--------+-------+--------+--------+

I-type
 31              20 19    15 14   12 11    7 6      0
+------------------+--------+-------+--------+--------+
|     imm12        |  rs1   | funct3|  rd   | opcode |
+------------------+--------+-------+--------+--------+
```

---

## 2. Compute `MATCH` and `MASK`

### R-type

```python
opcode = 0x0b              # custom-0
funct3 = 0x0
funct7 = 0x01              # pick anything not yet used in the slot

MATCH  = opcode | (funct3 << 12) | (funct7 << 25)
MASK   = 0xfe00707f        # locks funct7[31:25] + funct3[14:12] + opcode[6:0]
```

### R4-type

```python
opcode = 0x0b
funct3 = 0x0
funct2 = 0x0               # 0..3

MATCH  = opcode | (funct3 << 12) | (funct2 << 25)
MASK   = 0x0600707f        # locks funct2[26:25] + funct3[14:12] + opcode[6:0]
```

### I-type

The 12-bit immediate is variable, so only `funct3` and `opcode` are
locked:

```python
MATCH  = opcode | (funct3 << 12)
MASK   = 0x0000707f        # locks funct3[14:12] + opcode[6:0]
```

Verify in Python:

```python
print(hex(MATCH))
print(hex(MASK))
```

Reading the hex-mask is the easiest sanity check that you have
locked the right fields.

---

## 3. Update binutils (encoding registry)

### File: `binutils/include/opcode/riscv-opc.h`

Add MATCH/MASK macros:

```c
#define MATCH_MYINSN  0x________
#define MASK_MYINSN   0x________
```

Add a `DECLARE_INSN` line **inside** the `#ifdef DECLARE_INSN`
block (not after `#endif`):

```c
DECLARE_INSN(myinsn, MATCH_MYINSN, MASK_MYINSN)
```

### File: `binutils/opcodes/riscv-opc.c`

Add a row to `riscv_opcodes[]`. Pick the right operand string for
your format:

| Format     | Operand string |
|------------|----------------|
| R-type     | `"d,s,t"`     |
| R4-type    | `"d,s,t,r"`   |
| I-type     | `"d,s,j"`     |
| S-type     | `"t,q(s)"`    |

```c
{"myinsn", 0, INSN_CLASS_I, "d,s,t",
    MATCH_MYINSN, MASK_MYINSN, match_opcode, 0},
```

Verify:

```bash
grep -n '"myinsn"' binutils/opcodes/riscv-opc.c
# Expected: 1 hit
```

---

## 4. Add a `-myinsn` GCC flag

File: `gcc/gcc/config/riscv/riscv.opt`. Append (no blank line
between the flag-name line and the `Target` line):

```
mmyinsn
Target Var(TARGET_MYINSN) Init(0)
Enable the custom myinsn instruction.
```

This produces:

* the user-visible flag `-mmyinsn`;
* the C macro `TARGET_MYINSN` (non-zero when the flag is given);
* the description shown by `gcc --help=target`.

`TARGET_MYINSN` should gate **both** the matching pass and the
`define_insn`'s predicate so that nothing happens unless the user
opts in.

---

## 5. Add the RTL `define_insn`

File: `gcc/gcc/config/riscv/riscv.md`.

### Add an UNSPEC

Inside the existing `define_c_enum "unspec"` block:

```
  UNSPEC_MYINSN
```

### Add the `define_insn`

For an R-type, three-operand pattern:

```scheme
(define_insn "riscv_myinsn"
  [(set (mem:BLK (match_operand:DI 0 "register_operand" "r"))
        (unspec:BLK
          [(mem:BLK (match_operand:DI 1 "register_operand" "r"))
           (mem:BLK (match_operand:DI 2 "register_operand" "r"))]
          UNSPEC_MYINSN))]
  "TARGET_MYINSN"
  "myinsn\t%0,%1,%2"
  [(set_attr "type" "ghost")
   (set_attr "mode" "DI")])
```

For R4-type, add a fourth `mem:BLK` operand and `%3` in the assembly
template, exactly like `attn` does
([§4 of `04-patches-and-files.md`](04-patches-and-files.md#file-4--gccgccconfigriscvriscvmd)).

Three rules of thumb that came directly out of this project's
experience:

* Use **`mem:BLK`** if your instruction reads or writes memory of
  unknown size; use a normal mode (`DI`, `SI`, `SF`, …) only if the
  result is a value in a register.
* Use **`type "ghost"`** unless you know your instruction's
  pipeline cost model. `"unknown"` triggers an ICE in the RISC-V
  scheduler ([Issue 7](05-troubleshooting.md#issue-7--ice-in-riscv_sched_variable_issue)).
* Always include a **predicate** (the `"TARGET_MYINSN"` string)
  that gates the pattern on the `-m` flag.

---

## 6. Declare an `IFN` and write its expander

### File: `gcc/gcc/internal-fn.def`

```c
DEF_INTERNAL_FN (MYINSN, ECF_NOTHROW, NULL)
```

Avoid `ECF_LEAF` if your instruction touches memory — see
[Issue 6](05-troubleshooting.md#issue-6--ice-in-propagate_necessity-dce).

### File: `gcc/gcc/internal-fn.cc`

```c
static void
expand_MYINSN (internal_fn, gcall *stmt)
{
  rtx a = expand_normal (gimple_call_arg (stmt, 0));
  rtx b = expand_normal (gimple_call_arg (stmt, 1));
  rtx c = expand_normal (gimple_call_arg (stmt, 2));
  a = force_reg (Pmode, a);
  b = force_reg (Pmode, b);
  c = force_reg (Pmode, c);
  emit_insn (gen_riscv_myinsn (a, b, c));
}
```

For each operand: `expand_normal` lowers the GIMPLE argument into an
RTL value, `force_reg` puts it into a register if it is not already.
`gen_riscv_myinsn` is auto-generated by GCC from your `define_insn`
in step 5.

---

## 7. Decide: idiom recognition or intrinsic?

You now have two ways to expose `myinsn` to user code:

### 7a. Intrinsic / builtin (the easy way)

Register a `DIRECT_BUILTIN` in `riscv-builtins.cc`. The user writes:

```c
__builtin_riscv_myinsn(a, b, c);
```

and the compiler emits one machine instruction. Quick to implement;
makes the source code non-portable.

### 7b. Idiom recognition (the project's choice)

Write a GIMPLE pass (next section) that walks the IR, recognises a
specific code pattern, and replaces it with an `IFN_MYINSN` call.
The user's source code is unchanged plain C.

The two are not mutually exclusive — you can ship both. `attn` chose
to ship only the idiom recogniser, on the rationale spelled out in
[§2 of `02-compiler-pass.md`](02-compiler-pass.md#2-design-philosophy-idiom-recognition-vs-explicit-intrinsics).

The remainder of this document assumes you want a recogniser pass.

---

## 8. Wire the new pass into the build

### File: `gcc/gcc/passes.def`

Pick an insertion point. **After the loop pipeline** (right after
Graphite's closing `POP_INSERT_PASSES()`) is usually correct for
loop-shaped patterns.

```c
POP_INSERT_PASSES ()           /* end of Graphite */
NEXT_PASS (pass_recognize_myinsn);
```

### File: `gcc/gcc/tree-pass.h`

```c
extern gimple_opt_pass *make_pass_recognize_myinsn (gcc::context *ctxt);
```

### File: `gcc/gcc/Makefile.in`

Below the `tree-ssa-math-opts.o \` line (chosen as a unique anchor):

```
	tree-ssa-myinsn.o \
```

Force the per-target Makefile to regenerate after this edit:

```bash
find ~/riscv-gnu-toolchain -path '*/gcc/Makefile' -delete
```

### File: `gcc/gcc/tree-ssa-myinsn.cc`

The new pass body. Skeleton in §9.

---

## 9. Skeleton of a pattern-matching pass

```cpp
#define INCLUDE_MEMORY
#include "config.h"
#include "system.h"
#include "coretypes.h"
#include "backend.h"
#include "tree.h"
#include "gimple.h"
#include "tree-pass.h"
#include "ssa.h"
#include "fold-const.h"           /* MUST come before tree-data-ref.h */
#include "gimple-iterator.h"
#include "cfgloop.h"
#include "tree-cfg.h"
#include "tree-ssa-loop.h"
#include "tree-scalar-evolution.h"
#include "internal-fn.h"
#include "tree-data-ref.h"
#include "tree-eh.h"
#include "tree-ssa.h"
#include "tree-into-ssa.h"
#include "builtins.h"

namespace {

const pass_data pass_data_recognize_myinsn = {
  GIMPLE_PASS,
  "myinsnrec",                   /* shows in -fdump-tree-myinsnrec* filenames */
  OPTGROUP_LOOP,
  TV_TREE_LOOP,
  PROP_cfg | PROP_ssa,
  0, 0, 0,
  TODO_update_ssa
};

class pass_recognize_myinsn : public gimple_opt_pass
{
public:
  pass_recognize_myinsn (gcc::context *ctxt)
    : gimple_opt_pass (pass_data_recognize_myinsn, ctxt) {}

  bool gate (function *) final override
  {
#ifdef TARGET_MYINSN
    return TARGET_MYINSN && optimize >= 2 && flag_tree_loop_optimize;
#else
    return false;
#endif
  }

  unsigned int execute (function *fun) final override
  {
    bool changed = false;

    for (auto loop : loops_list (cfun, LI_FROM_INNERMOST))
      {
        if (loop_depth (loop) != 1) continue;        /* top-level only */
        if (try_recognize_myinsn (loop))
          { changed = true; break; }                  /* one match per fn */
      }

    return changed ? TODO_cleanup_cfg : 0;
  }
};

}  /* anonymous namespace */

gimple_opt_pass *
make_pass_recognize_myinsn (gcc::context *ctxt)
{
  return new pass_recognize_myinsn (ctxt);
}
```

The body of `try_recognize_myinsn(loop)` is your matching logic.
Pattern after `attn`:

1. Decide the syntactic shape your idiom must take.
2. Walk the loop's basic blocks, looking for that shape.
3. Collect the operand SSA names you need to pass to the
   instruction.
4. Build the IFN call:

   ```cpp
   gcall *call = gimple_build_call_internal (IFN_MYINSN, /*nargs=*/3,
                                             arg1, arg2, arg3);
   gimple_set_has_volatile_ops (call, true);          /* survive DCE */
   gimple_stmt_iterator gsi = gsi_last_bb (loop_preheader_edge (loop)->src);
   gsi_insert_after (&gsi, call, GSI_NEW_STMT);
   ```

5. Return `true`. The pass manager's `TODO_cleanup_cfg` will tidy
   up.

For a longer worked example see `gcc/gcc/tree-ssa-attn.cc` and
[`02-compiler-pass.md`](02-compiler-pass.md).

---

## 10. Test and iterate

### Build incrementally

```bash
rm -f $(find ~/riscv-gnu-toolchain -name 'tree-ssa-myinsn.o')
cd ~/riscv-gnu-toolchain
make -j$(nproc) 2>&1 | tee build.log
grep -n 'error:' build.log | head -20
```

### Layer-1 test (assembler)

```bash
echo "myinsn x0, a0, a1" | \
    riscv64-unknown-elf-as - -o /tmp/t.o && \
    riscv64-unknown-elf-objdump -d /tmp/t.o
```

### Layer-2 test (compiler)

Write a small C source that matches the idiom, then:

```bash
riscv64-unknown-elf-gcc \
    -mmyinsn -O2 -fdump-tree-myinsnrec-details \
    -c test.c -o test.o
cat test.c.*myinsnrec*
```

The dump tells you which loops the matcher considered and which
checks failed.

---

## 11. Common pitfalls (read these first)

These are direct lessons from the eleven issues catalogued in
[`05-troubleshooting.md`](05-troubleshooting.md). Reading
this section once before you start saves hours of debugging.

| Pitfall | Fix |
|---------|-----|
| Source file in the wrong directory | New `.cc` files go in `gcc/gcc/`, **not** in `gcc/` |
| `type "unknown"` in `define_insn` | Change to `type "ghost"` |
| `ECF_LEAF` on a memory-touching IFN | Drop `ECF_LEAF`, keep `ECF_NOTHROW` |
| `gimple_set_has_volatile_ops` not set | DCE will delete your call; always set it |
| Calling `scev_initialize()` | Don't — SCEV is already active at that pass position |
| Pass inserted *inside* a `PUSH_INSERT_PASSES_WITHIN` block | Move it after the matching `POP_INSERT_PASSES()` |
| `tree-ssa-loop.o` chosen as Makefile anchor | Use `tree-ssa-math-opts.o` — it appears exactly once |
| `build_rdg` / `free_rdg` "not found" | They are class methods of `loop_distribution`, not free functions |
| Header order: missing `fold-const.h` | Include before `tree-data-ref.h` |
| Header order: missing `tree-into-ssa.h` | Required for `mark_virtual_operands_for_renaming` |
| Pass matches but inserts in wrong basic block | Filter by `loop_depth(loop) == 1` |
| Loop body still present after match | Expected — see [§7 of `02-compiler-pass.md`](02-compiler-pass.md#7-why-the-loop-body-stays-and-what-removing-it-would-take) |

---

**Next:** [`07-research-context.md`](07-research-context.md) —
how this project relates to existing work and where it can go from
here.
