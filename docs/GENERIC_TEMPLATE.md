# Generic Template — Adding Custom RISC-V Instructions

This document is a step-by-step template for adding any new custom
instruction to the RISC-V GNU toolchain, based on the `attn` implementation.

---

## 1. Choose Your Opcode Slot

RISC-V reserves four slots for custom instructions:

| Slot     | opcode[6:0] | Hex    |
|----------|-------------|--------|
| custom-0 | `0001011`   | `0x0b` |
| custom-1 | `0101011`   | `0x2b` |
| custom-2 | `1011011`   | `0x5b` |
| custom-3 | `1111011`   | `0x7b` |

---

## 2. Choose Instruction Format

### R-type — 3 registers (rd, rs1, rs2)

```
 31      25 24   20 19   15 14  12 11    7 6      0
+----------+-------+-------+------+-------+--------+
|  funct7  |  rs2  |  rs1  | funct3|  rd  | opcode |
+----------+-------+-------+------+-------+--------+
```

```python
MATCH = (funct7 << 25) | (funct3 << 12) | opcode
MASK  = 0xfe00707f   # locks funct7 + funct3 + opcode
```

Operand string: `"d,s,t"` (rd, rs1, rs2)

### R4-type — 4 registers (rd, rs1, rs2, rs3)

```
 31   27 26 25 24  20 19  15 14 12 11   7 6      0
+------+----+------+------+-----+------+--------+
|  rs3 | f2 |  rs2 |  rs1 | f3  |  rd  | opcode |
+------+----+------+------+-----+------+--------+
```

```python
MATCH = (funct2 << 25) | (funct3 << 12) | opcode
MASK  = 0x0600707f   # locks funct2[26:25] + funct3 + opcode
```

Operand string: `"d,s,t,r"` (rd, rs1, rs2, rs3)

---

## 3. Compute MATCH and MASK

```python
# R-type example: custom-0, funct3=0, funct7=0
opcode = 0x0b
funct3 = 0x0
funct7 = 0x00
MATCH  = (funct7 << 25) | (funct3 << 12) | opcode
MASK   = 0xfe00707f

# R4-type example (attn): custom-0, funct3=0, funct2=0
opcode = 0x0b
funct3 = 0x0
funct2 = 0x0
MATCH  = (funct2 << 25) | (funct3 << 12) | opcode   # = 0x0000000b
MASK   = 0x0600707f
```

Verify with Python:
```python
print(hex(MATCH))   # should match your expected value
print(hex(MASK))    # should mask exactly the fixed fields
```

---

## 4. Files to Modify (6 files minimum)

### File 1 — `binutils/include/opcode/riscv-opc.h`

Add MATCH/MASK macros near other custom entries:
```c
#define MATCH_YOUR_INSN  0x________
#define MASK_YOUR_INSN   0x________
```

Add DECLARE_INSN immediately above `DECLARE_INSN(add,`:
```c
DECLARE_INSN(your_insn, MATCH_YOUR_INSN, MASK_YOUR_INSN)
```

Verify:
```bash
grep -n 'MATCH_YOUR_INSN\|MASK_YOUR_INSN\|DECLARE_INSN(your' \
    binutils/include/opcode/riscv-opc.h
# Expected: 3 hits
```

### File 2 — `binutils/opcodes/riscv-opc.c`

Add to `riscv_opcodes[]` array, above `{"unimp",`:
```c
{"your_insn", 0, INSN_CLASS_I, "d,s,t",
    MATCH_YOUR_INSN, MASK_YOUR_INSN, match_opcode, 0},
```

Use `"d,s,t,r"` for R4-type (4 registers).

Verify:
```bash
grep -n '"your_insn"' binutils/opcodes/riscv-opc.c
# Expected: 1 hit
```

### File 3 — `gcc/gcc/config/riscv/riscv.opt`

Append (no blank line between flag name and Target line):
```
myinsn
Target Var(TARGET_MY_INSN) Init(0)
Enable the custom your_insn instruction.
```

Verify:
```bash
grep -n 'TARGET_MY_INSN\|^myinsn' gcc/gcc/config/riscv/riscv.opt
# Expected: 2 hits
```

### File 4 — `gcc/gcc/config/riscv/riscv.md`

Add UNSPEC inside the existing `define_c_enum "unspec"` block:
```
  UNSPEC_YOUR_INSN
```

Add define_insn above `(define_insn "nop"`:

For R-type (3 registers):
```scheme
(define_insn "riscv_your_insn"
  [(set (mem:BLK (match_operand:DI 0 "register_operand" "r"))
        (unspec:BLK
          [(mem:BLK (match_operand:DI 1 "register_operand" "r"))
           (mem:BLK (match_operand:DI 2 "register_operand" "r"))]
          UNSPEC_YOUR_INSN))]
  "TARGET_MY_INSN"
  "your_insn\t%0,%1,%2"
  [(set_attr "type" "ghost")
   (set_attr "mode" "DI")])
```

For R4-type (4 registers), add one more `mem:BLK` operand and `%3` in
the template string.

**Important:** Use `type "ghost"` not `type "unknown"`. The RISC-V
scheduler asserts on unknown types and requires a DFA reservation.
Ghost instructions skip the scheduler cleanly.

### File 5 — `gcc/gcc/internal-fn.def`

```c
DEF_INTERNAL_FN (YOUR_INSN, ECF_NOTHROW, NULL)
```

Do not use `ECF_LEAF` if the instruction touches memory — DCE will
treat it as having no side effects and may eliminate it.

### File 6 — `gcc/gcc/internal-fn.cc`

Add expander near other `expand_*` functions:
```c
static void
expand_YOUR_INSN (internal_fn, gcall *stmt)
{
  rtx arg0 = expand_normal (gimple_call_arg (stmt, 0));
  rtx arg1 = expand_normal (gimple_call_arg (stmt, 1));
  rtx arg2 = expand_normal (gimple_call_arg (stmt, 2));
  arg0 = force_reg (Pmode, arg0);
  arg1 = force_reg (Pmode, arg1);
  arg2 = force_reg (Pmode, arg2);
  emit_insn (gen_riscv_your_insn (arg0, arg1, arg2));
}
```

### File 7 — `gcc/gcc/passes.def`

Register AFTER the `POP_INSERT_PASSES()` that closes the Graphite block:
```c
POP_INSERT_PASSES ()
NEXT_PASS (pass_your_pass);   ← here, not inside the block
```

### File 8 — `gcc/gcc/tree-pass.h`

Add factory declaration below `make_pass_graphite`:
```c
extern gimple_opt_pass *make_pass_your_pass (gcc::context *ctxt);
```

### File 9 — `gcc/gcc/Makefile.in`

Add below `tree-ssa-math-opts.o \` (single unique anchor):
```
	tree-ssa-your-pass.o \
```

Then delete stale Makefile to force regeneration:
```bash
find ~/riscv-gnu-toolchain -path '*/gcc/Makefile' -delete
```

---

## 5. Write the GCC Pass (new .cc file)

Create `gcc/gcc/tree-ssa-your-pass.cc`. Minimum structure:

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
#include "fold-const.h"           // must be before tree-data-ref.h
#include "gimple-iterator.h"
#include "cfgloop.h"
#include "tree-cfg.h"
#include "tree-ssa-loop.h"
#include "tree-scalar-evolution.h"
#include "internal-fn.h"
#include "tree-data-ref.h"
#include "tree-eh.h"
#include "tree-ssa.h"
#include "tree-into-ssa.h"        // mark_virtual_operands_for_renaming
#include "builtins.h"

namespace {

const pass_data pass_data_your_pass = {
  GIMPLE_PASS, "yourpass", OPTGROUP_LOOP, TV_TREE_LOOP,
  PROP_cfg | PROP_ssa, 0, 0, 0, TODO_update_ssa
};

class pass_your_pass : public gimple_opt_pass {
public:
  pass_your_pass (gcc::context *ctxt)
    : gimple_opt_pass (pass_data_your_pass, ctxt) {}

  bool gate (function *) final override {
#ifdef TARGET_MY_INSN
    return TARGET_MY_INSN && optimize >= 2;
#else
    return false;
#endif
  }

  unsigned int execute (function *fun) final override {
    // your pattern matching logic here
    return 0;
  }
};

} // anon namespace

gimple_opt_pass *
make_pass_your_pass (gcc::context *ctxt) {
  return new pass_your_pass (ctxt);
}
```

Key rules:
- Include `fold-const.h` **before** `tree-data-ref.h`
- Do NOT call `scev_initialize()` — already active at pass position
- Mark IFN calls with `gimple_set_has_volatile_ops(call, true)`
- Do NOT wipe loop bodies manually — leave them for DCE
- Gate must check `TARGET_MY_INSN` to prevent firing without the flag

---

## 6. Build and Test

```bash
# Clean stale objects
rm -f $(find ~/riscv-gnu-toolchain/build-gcc-newlib-stage1 \
    ~/riscv-gnu-toolchain/build-gcc-newlib-stage2 \
    -name 'tree-ssa-your-pass.o' 2>/dev/null)

cd ~/riscv-gnu-toolchain
make -j$(nproc) 2>&1 | tee build.log
grep 'error:' build.log | head -20
```

Test the assembler:
```bash
echo "attn x0, a0, a1, a2" | \
    $HOME/riscv-install/bin/riscv64-unknown-elf-as - -o /tmp/t.o && \
    $HOME/riscv-install/bin/riscv64-unknown-elf-objdump -d /tmp/t.o
```

Test the compiler pass:
```bash
$HOME/riscv-install/bin/riscv64-unknown-elf-gcc \
    -myinsn -O2 -fdump-tree-yourpass-details \
    -c test.c -o test.o

cat test.c.*yourpass*    # check what the pass saw and did
```

---

## 7. Common Pitfalls

| Pitfall | Fix |
|---|---|
| Source file in wrong directory | Must be at `gcc/gcc/`, not `gcc/` |
| `type "unknown"` in define_insn | Change to `type "ghost"` |
| `ECF_LEAF` on memory-touching IFN | Remove `ECF_LEAF`, keep `ECF_NOTHROW` |
| Calling `scev_initialize()` | Remove — already active at pass position |
| Manually wiping loop body | Don't — leave for DCE, or segfault in vdef chain |
| Pass inside Graphite block | Insert after `POP_INSERT_PASSES()`, not before |
| `tree-ssa-loop.o` as anchor in Makefile.in | Use `tree-ssa-math-opts.o` (unique) |
| `build_rdg` / `free_rdg` not found | These are class methods, not free functions |
