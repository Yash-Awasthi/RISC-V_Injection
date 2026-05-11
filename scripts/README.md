# `scripts/` — Add your own custom RISC-V instruction in one command

## What this directory is

Adding a new instruction to GCC + binutils by hand means editing
eleven different files spread across two source trees, in a very
specific order, with subtly different syntax in each one. Get any
edit wrong and the toolchain either fails to build or silently does
the wrong thing. This directory is a script that does all eleven
edits *for* you, given a one-paragraph description of the instruction
you want.

The `attn` instruction shipped with the rest of this repository
(`gcc/gcc/tree-ssa-attn.cc`, the `-mattn` flag, the assembly mnemonic
`attn rd, rs1, rs2, rs3` — see the top-level
[`README.md`](../README.md)) was the first instruction added by hand.
Everything in `scripts/` is that experience boiled down to a reusable
driver so the next instruction (FMA, batch-norm, integration of
sin x, GEMM, RoPE, LayerNorm, anything else you can express as a
function call) takes one command instead of a week.

## The 30-second version

Write a C file that calls a marker function:

```c
/* my_demo.c */
extern long __custom_myop (long a, long b, long c);
long demo (long a, long b, long c) { return __custom_myop (a, b, c); }
```

Run:

```bash
python3 scripts/customrv.py from-c my_demo.c --apply --build
```

The driver will (a) read your C file, (b) pick a free opcode slot in
`custom-0..custom-3`, (c) generate eleven patches for the GCC and
binutils trees, (d) show each one to you in context and ask before
applying it, (e) install a brand-new `tree-ssa-myop.cc` GIMPLE pass
that rewrites every `__custom_myop(...)` call into a single `myop`
machine instruction, then (f) rebuild the toolchain and run a
pattern test. The result is a `riscv64-unknown-elf-gcc` that
recognises `-mmyop` and emits the new mnemonic — without intrinsics,
without inline assembly, without `.insn` directives.

That is it. The rest of this document is detail.

---

## The two entry points

The driver `customrv.py` accepts your instruction description in
either of two equivalent forms.

### Way 1 — JSON config

Use this when you already know the exact GIMPLE shape you want to
match. The four worked examples in [`configs/`](./configs/) cover
every `pattern_kind` the pipeline currently supports.

```bash
python3 scripts/customrv.py from-config scripts/configs/fds.json --apply --build
```

### Way 2 — C source file (recommended for first-time users)

Use this when you would rather describe the instruction by example.
Hand the driver a `.c` file containing one function and (usually)
one call to a marker symbol `__custom_<mnemonic>(args...)`. The
analyser in [`lib/c_analyzer.py`](./lib/c_analyzer.py) infers

* the **mnemonic** (from the marker or the function name);
* the **input arity** (number of register operands);
* whether the operands are **scalars** (R-type / R4-type) or
  **pointers** (memory-style `(mem:BLK …)` RTL);
* a free **MATCH / MASK** slot in `custom-0..custom-3`;
* and the right **pattern_kind** (`arith_expr`, `closed_form_loop`,
  or universal `marker`).

It then walks the eleven patches with you:

```bash
python3 scripts/customrv.py from-c scripts/examples/fma_demo.c --apply --build
```

When the C source contains a closed-form arithmetic shape the
analyser recognises (`(a/b) - c`, `Σi`, `a*b + c`), the *specific*
matcher template is selected. Otherwise the analyser falls back to
the universal **marker** matcher — it rewrites every
`__custom_<mnemonic>(…)` call into `IFN_RISCV_<UPPER>` regardless of
the surrounding code. The marker path is what makes this pipeline
work for any instruction whose mathematics we deliberately do *not*
ask the compiler to understand (FMA, BatchNorm, ∫sin x dx, GEMM,
LayerNorm, …).

---

## Files in this directory

The pipeline is organised into a thin top-level CLI layer, a shared
library that does the actual work, and a templates / examples /
tests subtree.

### Top-level CLI

| File | What it does |
|------|--------------|
| `customrv.py` | The unified driver. Three subcommands: `free-opcodes` (print available MATCH/MASK slots), `from-config` (Way 1), `from-c` (Way 2). Almost every user-facing command in this directory eventually calls into this script. |
| `01_find_opcodes.py` | Standalone CLI for the opcode-slot finder. Reads `binutils/include/opcode/riscv-opc.h`, parses every existing `#define MATCH_<NAME>`, and prints free slots in `custom-0..custom-3` for the chosen instruction format. |
| `02_generate.py` | Renders the eleven patch artefacts (ten in-place edits + one brand-new `tree-ssa-<mnemonic>.cc` file) from a config. Output goes under `out/<mnemonic>/{patches,new_files}/`. |
| `03_apply.py` | The interactive patcher. Walks each generated patch, shows a 3-line context window from the live source, and asks for confirmation before writing. Anchor-based — not `grep` / `sed` / regex search-and-replace. |
| `04_build.sh` | Rebuilds the modified toolchain and runs an assemble / disassemble round-trip on a one-line `.S` file to confirm the new mnemonic encodes correctly. |
| `05_test.sh` | Compiles `tests/<mnemonic>.c` with the rebuilt compiler and greps the `.s` output for the new mnemonic. Exits non-zero if the instruction is missing. |

### Shared library (`lib/`)

| File | What it does |
|------|--------------|
| `lib/opcodes.py` | Free-slot allocator. Walks the funct2 / funct3 / funct7 sub-spaces of each custom-N opcode looking for a `(match, mask)` tuple that does not collide with any existing entry. |
| `lib/config.py` | Loads + validates JSON configs, derives the bookkeeping fields (`upper`, `flag`, `target_macro`, `ifn`, `operand_string`), and auto-allocates MATCH/MASK when the config left them `null`. |
| `lib/snippets.py` | Owns the canonical patch order. Builds the eleven patch records (each one a small JSON object with an anchor, a position, and an insertion block) and writes them to disk. |
| `lib/builders.py` | Generates the RTL `define_insn` block and the `internal-fn.cc` expander function. Two flavours: `rtl_kind="register"` (classic ALU-style) and `rtl_kind="memory"` (accelerator-style with `(mem:BLK ...)` operands — the same shape `attn` uses). |
| `lib/patcher.py` | The patch applier itself. Resolves anchors, prompts on ambiguity, prints context, asks `y/N`, creates a one-time `.bak`, is idempotent on re-runs, and gates on GCC == 15.2.x / binutils == 2.46.x. |
| `lib/c_analyzer.py` | The "Way 2" brain. Reads a user-supplied `.c` file and produces a config: detects the marker call, the operand types, the closed-form arithmetic shape, or falls back to a synthetic marker wrapper. |

### Templates (`templates/`)

| File | Used when |
|------|-----------|
| `templates/tree_ssa_template.cc.tmpl` | *Always.* The skeleton for the generated `tree-ssa-<mnemonic>.cc` GIMPLE pass. |
| `templates/matcher_arith_expr.cc.frag` | `pattern_kind == arith_expr`. Recognises `(a OP1 b) OP2 c` and rewrites it into `IFN_RISCV_<UPPER>(a, b, c)`. |
| `templates/matcher_closed_form_loop.cc.frag` | `pattern_kind == closed_form_loop`. Recognises `for (i=0; i<n; ++i) acc += i;` and rewrites it into `IFN_RISCV_<UPPER>(n)`. |
| `templates/matcher_marker.cc.frag` | `pattern_kind == marker`. The universal fallback: rewrites every `__custom_<mnemonic>(…)` call into `IFN_RISCV_<UPPER>(…)`. |

### Worked examples and tests

| File / dir | What it contains |
|-----------|------------------|
| `configs/` | Four hand-written JSON configs covering every supported `pattern_kind`: `fds.json` (arith_expr), `nsum.json` (closed_form_loop), `fma.json` (marker / register), `bnorm.json` (marker / memory). |
| `examples/` | Three Way-2 input `.c` files: `fma_demo.c`, `batchnorm_demo.c`, `sinx_integral_demo.c`. |
| `tests/` | The Python sanity harness `test_pipeline.py` (seven checks, no toolchain build required) plus one `<mnemonic>.c` per worked example for the rebuilt-compiler pattern tests. |

---

## End-to-end usage — Way 2 (recommended)

```bash
# 1. Optional sanity check — inspect what's free in the opcode space.
python3 scripts/customrv.py free-opcodes --inputs 3

# 2. Hand the driver your C file. It generates eleven patch artefacts
#    under scripts/out/<mnemonic>/ and, with --apply, walks them.
python3 scripts/customrv.py from-c examples/fma_demo.c \
    --apply  \
    --yes    \
    --build  \
    --install $HOME/riscv-install
```

What each flag does:

* `--apply` — actually edit the GCC and binutils source trees.
  Without this flag the driver only writes patch JSON files under
  `scripts/out/<mnemonic>/`; nothing in `gcc/` or `binutils/`
  changes.
* `--yes` — accept every confirmation prompt non-interactively.
  Omit this flag the first time you run the pipeline so you can see
  the context window for each edit.
* `--build` — after applying, run `04_build.sh` (rebuild) and
  `05_test.sh` (compile a test C file and grep for the new
  mnemonic).
* `--install <path>` — the toolchain install prefix used by the
  build/test scripts. Defaults to `$HOME/riscv-install`.

For every patch, `--apply` prints the resolved `<file>:<line>`, a
three-line context window centred on the anchor, the block that
will be inserted, and an explanatory note where one applies.

If an anchor is **ambiguous** — multiple raw matches in the file and
the patch's `which` field is not strict — the patcher prompts you to
pick a candidate or type an explicit line number. Anchors of kind
`eof` never prompt. The convention throughout is *ask only when
ambiguous*.

---

## End-to-end usage — Way 1 (JSON config)

Use this when you know the exact GIMPLE shape and want to write the
config by hand. The unified driver and the lower-level scripts
give equivalent results:

```bash
# One-shot via the driver:
python3 scripts/customrv.py from-config configs/fds.json --apply --build

# Or, step by step — useful when you want to inspect what would
# happen before letting the patcher write to the source tree:
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

## Sanity tests — run these before anything else

The Python sanity harness exercises the opcode finder, the config
loader, both autodetect paths, the marker `mem:BLK` form, and a
full dry-run patch against the real toolchain tree. None of these
tests need a built RISC-V toolchain — they only touch the source.
Running the seven checks takes well under a second; running the
full 45-90 minute toolchain rebuild over a generation bug does not.

```bash
python3 scripts/tests/test_pipeline.py
```

Expected output:

```
[1] opcode finder                        ✔
[2] config validation                    ✔
[3] arith_expr generation (fds)          ✔
[4] marker generation (bnorm, mem:BLK)   ✔
[5] arith_expr autodetect from raw C     ✔
[6] end-to-end dry-run via 03_apply.py   ✔
[7] customrv.py from-c (way 2)           ✔

  7/7 tests passed
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
