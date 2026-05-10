# `scripts/` — generic custom-RISC-V-instruction pipeline

A small, self-contained toolkit that turns either **a JSON config**
or **a plain C source file** into a fully patched
`riscv-gnu-toolchain` tree which recognises the corresponding idiom
and emits a single custom machine instruction.

The reference instance of this pipeline is the `attn` instruction
that ships with the rest of this repository (`gcc/gcc/tree-ssa-attn.cc`,
`-mattn`, `attn rd, rs1, rs2, rs3` — see the top-level
[`README.md`](../README.md)).  Everything in `scripts/` exists so a
*different* instruction (FMA, batch-norm, integration-of-sin-x,
GEMM, RoPE, LayerNorm — anything you can express as a function call)
can be added the same way, in one command.

---

## Two ways to use it

### Way 1 — start from a JSON config

Best when you already know the exact GIMPLE shape you want to match.
Examples ship in `configs/`:

```bash
python3 scripts/customrv.py from-config scripts/configs/fds.json --apply --build
```

### Way 2 — start from a C file (recommended)

You hand the script a `.c` file with one function and (usually) one
call to a magic marker symbol `__custom_<mnemonic>(args…)`.  The
analyser figures out:

* the mnemonic,
* the input arity,
* whether the operands are scalars (R-type / R4-type) or pointers
  (memory-style `(mem:BLK …)` RTL),
* a free `MATCH/MASK` slot in `custom-0..custom-3`,
* and the matching pattern_kind.

Then it walks all 11 patches with you:

```bash
python3 scripts/customrv.py from-c scripts/examples/fma_demo.c --apply --build
```

If the C source contains arithmetic in a closed form the analyser
recognises (`(a/b) - c`, `Σi`, `a*b + c`), it picks the *specific*
matcher.  Otherwise it falls back to the universal **marker** matcher
— the one that just rewrites every `__custom_<mnemonic>(…)` call into
`IFN_RISCV_<UPPER>` regardless of what the surrounding code does.
This is what lets the pipeline work for instructions whose
mathematics we deliberately *do not* simplify (FMA, BatchNorm,
∫sin x dx, …).

---

## Files

| File / dir                              | Purpose                                                                 |
|-----------------------------------------|-------------------------------------------------------------------------|
| `customrv.py`                           | The unified driver.  Subcommands: `free-opcodes`, `from-config`, `from-c`. |
| `01_find_opcodes.py`                    | Standalone CLI — print free MATCH/MASK slots in `custom-0..custom-3`.   |
| `02_generate.py`                        | Config → 11 patch artefacts (`out/<mnemonic>/{patches,new_files}`).     |
| `03_apply.py`                           | Anchor-based, interactive patcher (no grep, no sed).                    |
| `04_build.sh`                           | Rebuild the toolchain and run an assemble/disassemble round-trip.       |
| `05_test.sh`                            | Compile a C test and grep the `.s` for the new mnemonic.                |
| `lib/opcodes.py`                        | Opcode-slot allocator.                                                  |
| `lib/config.py`                         | Config loading, validation, derive-names, MATCH/MASK auto-allocation.   |
| `lib/snippets.py`                       | Builds the 11 patch records.                                            |
| `lib/builders.py`                       | RTL `define_insn` and `internal-fn.cc` expander generators.             |
| `lib/patcher.py`                        | The patch applier (anchor resolver, ambiguity prompt, .bak, idempotency). |
| `lib/c_analyzer.py`                     | "Way 2" — walks a C source file and infers a config.                    |
| `templates/tree_ssa_template.cc.tmpl`   | Skeleton for the new `tree-ssa-<mnemonic>.cc` pass.                     |
| `templates/matcher_arith_expr.cc.frag`  | Matcher fragment for `(a OP1 b) OP2 c`.                                 |
| `templates/matcher_closed_form_loop.cc.frag` | Matcher fragment for `acc += i` style reductions.                  |
| `templates/matcher_marker.cc.frag`      | Universal marker matcher — `__custom_<mnemonic>(…)` → `IFN_RISCV_<UPPER>(…)`. |
| `configs/`                              | Worked examples: `fds.json`, `nsum.json`, `fma.json`, `bnorm.json`.     |
| `examples/`                             | Way-2 input C files: `fma_demo.c`, `batchnorm_demo.c`, `sinx_integral_demo.c`. |
| `tests/`                                | Generated and hand-written C tests + `test_pipeline.py` (sanity tests). |

---

## End-to-end usage (way 2 — recommended)

```bash
# 1. Inspect what's free in the opcode space (optional, for sanity).
python3 scripts/customrv.py free-opcodes --inputs 3

# 2. Hand the driver your C file.  It generates 11 patch artefacts
#    under scripts/out/<mnemonic>/ and (with --apply) walks them.
python3 scripts/customrv.py from-c examples/fma_demo.c \
    --apply               # interactively apply patches with context
    --yes                 # accept all prompts non-interactively
    --build               # rebuild + smoke + pattern test
    --install $HOME/riscv-install
```

`--apply` prints, for every patch:

* the resolved `<file>:<line>`,
* three lines of context (the anchor and one line above/below),
* the block to be inserted,
* an explanatory note (where applicable).

If an anchor is **ambiguous** (multiple raw matches in the file and
the patch's `which` field is non-strict) the patcher prompts you to
choose a candidate or type an explicit line number.  Anchors of kind
`eof` never prompt.  This matches the project convention:
*ask only when ambiguous*.

---

## End-to-end usage (way 1 — JSON config)

```bash
python3 scripts/customrv.py from-config configs/fds.json --apply --build
# or, equivalently, the lower-level scripts:
python3 scripts/02_generate.py configs/fds.json
python3 scripts/03_apply.py    out/fds --dry-run
python3 scripts/03_apply.py    out/fds
bash    scripts/04_build.sh    fds  ~/riscv-gnu-toolchain  $HOME/riscv-install
bash    scripts/05_test.sh     fds  $HOME/riscv-install
```

---

## Config schema (JSON)

```jsonc
{
  "mnemonic":      "fds",                    // required, lowercase
  "num_inputs":    3,                        // 0/1/2 = R-type/I-type, 3 = R4-type
  "insn_class":    "INSN_CLASS_I",           // optional, default INSN_CLASS_I
  "pattern_kind":  "arith_expr",             // arith_expr | closed_form_loop | marker
  "rtl_kind":      "register",               // "register" or "memory" (mem:BLK)
  "match":         null,                     // auto-allocated when null
  "mask":          null,
  "preferred_slot": "custom-0",              // optional, default custom-0

  // pattern_kind == "arith_expr"
  "arith":   { "outer_op": "MINUS_EXPR", "inner_op": "RDIV_EXPR", "inner_pos": 0 },

  // pattern_kind == "closed_form_loop"
  "loop":    { "reduction_op": "PLUS_EXPR", "step_is_iv": true },

  // pattern_kind == "marker"
  "marker_fn": "__custom_fds"                // optional, defaults to __custom_<mnemonic>
}
```

Auto-derived: `upper`, `flag` (`m<mnemonic>`),
`target_macro` (`TARGET_<UPPER>`), `ifn` (`RISCV_<UPPER>`),
`operand_string` and `operand_string_human` (from `num_inputs`).

---

## How the patcher avoids `grep` / `sed`

Each patch record is a JSON file like:

```json
{
  "id": "08",
  "target_file": "gcc/gcc/passes.def",
  "anchor": { "kind": "contains", "text": "NEXT_PASS (pass_graphite)", "which": "first" },
  "position": "below_next_pop",
  "block": "      NEXT_PASS (pass_recognize_fds);\n"
}
```

`03_apply.py` (and the equivalent `customrv.py from-… --apply` path):

1. Opens the target file in memory and walks lines top-to-bottom.
2. Collects every line that matches the anchor.
3. If exactly one match (or `which: "first"`), uses it.
   Otherwise prompts the user to pick a candidate or enter a line
   number.
4. For passes.def's `below_next_pop`, walks further down to the
   matching `POP_INSERT_PASSES ()`.
5. Prints `>> file:line` plus a 3-line context window.
6. Asks `Apply this patch? [y/N]` (skipped when `--yes` is given).
7. Writes back in-place after creating a one-time `.bak`.

Anchor kinds supported: `eof`, `startswith`, `contains`,
`open_brace_after`, `return_type_before`.

---

## RTL kinds explained

* **`rtl_kind: "register"`** — the instruction reads/writes register
  values:

  ```scheme
  (define_insn "riscv_myinsn"
    [(set (match_operand:DI 0 "register_operand" "=r")
          (unspec:DI [(match_operand:DI 1) ...] UNSPEC_RISCV_MYINSN))]
    "TARGET_MYINSN"
    "myinsn\t%0,%1,..."
    [(set_attr "type" "ghost") (set_attr "mode" "DI")])
  ```

* **`rtl_kind: "memory"`** — the instruction touches memory of
  unknown size; every operand is a register-held pointer (this is
  what `attn` itself uses):

  ```scheme
  (define_insn "riscv_myinsn"
    [(set (mem:BLK (match_operand:DI 0 "register_operand" "r"))
          (unspec:BLK
            [(mem:BLK (match_operand:DI 1 "register_operand" "r")) ...]
            UNSPEC_RISCV_MYINSN))]
    "TARGET_MYINSN"
    "myinsn\t%0,%1,..."
    [(set_attr "type" "ghost") (set_attr "mode" "DI")])
  ```

Both forms set `type "ghost"`; non-ghost types trigger
`riscv_sched_variable_issue` ICEs in the RISC-V scheduler
(see `docs/05-troubleshooting.md`, Issue 7).

---

## Version pin

`03_apply.py` refuses to run unless:

* `gcc/gcc/BASE-VER` starts with `15.2`
* `binutils/bfd/version.m4` contains `[2.46.…]`

Override with `--force` (anchors may not match on other versions).

---

## Sanity tests

```bash
python3 scripts/tests/test_pipeline.py
```

Exercises the opcode finder, the config loader, both autodetect
paths, the marker `mem:BLK` form, and a full dry-run patch against
the real toolchain tree.  None of these tests need a built RISC-V
toolchain — they only touch the source.

```
[1] opcode finder                        ✔
[2] config validation                    ✔
[3] arith_expr generation (fds)          ✔
[4] marker generation (bnorm, mem:BLK)   ✔
[5] arith_expr autodetect from raw C     ✔
[6] end-to-end dry-run via 03_apply.py   ✔
[7] customrv.py from-c (way 2)           ✔
```

---

## Adding a new pattern kind

1. Drop a `templates/matcher_<kind>.cc.frag` next to the existing
   ones.  It should define `try_recognize_<MNEMONIC>_in_function`.
2. Add `<kind>` to `SUPPORTED_PATTERN_KINDS` in `lib/config.py`.
3. Add a branch in `lib/snippets.py::build_new_cc_file` that picks
   the new fragment when `cfg["pattern_kind"] == "<kind>"`.
4. Optionally extend `lib/c_analyzer.py` so the C analyser can pick
   it automatically.

That is the entire surface area.  No other files in `scripts/` need
to change.
