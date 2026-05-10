# `scripts/configs/` — Way-1 JSON configs (worked examples)

Each `*.json` file in this directory is a complete, self-contained
description of a custom RISC-V instruction. Hand it to
`scripts/customrv.py from-config` and the pipeline will generate a
patched toolchain tree that recognises that instruction.

These configs exist for two reasons:

1. **Regression tests.** `scripts/tests/test_pipeline.py` loads them
   and asserts the generator produces the right artefacts.
2. **Reference shapes.** They show the four `pattern_kind` flavours
   the pipeline currently supports, so you can copy the closest one
   and edit it for your own instruction.

---

## How to run

```bash
# Validate one config + emit patches under scripts/out/<mnemonic>/.
python3 scripts/customrv.py from-config scripts/configs/fds.json

# Same, but interactively apply the patches and rebuild.
python3 scripts/customrv.py from-config scripts/configs/fds.json --apply --build
```

The schema is documented in [`../README.md`](../README.md#config-schema-json).

---

## What each config demonstrates

| File | Mnemonic | `pattern_kind` | `rtl_kind` | What it teaches |
|------|----------|----------------|------------|-----------------|
| [`fds.json`](./fds.json) | `fds` | `arith_expr` | `register` | The single-statement arithmetic form. The matcher folds `(a / b) - c` into `IFN_RISCV_FDS(a, b, c)`. R4-type, 3 source registers. |
| [`nsum.json`](./nsum.json) | `nsum` | `closed_form_loop` | `register` | The loop-reduction form. The matcher folds `for (i=0; i<n; ++i) acc += i;` into `IFN_RISCV_NSUM(n)` (i.e. `n*(n-1)/2`). R-type, 1 source register. |
| [`fma.json`](./fma.json) | `fma` | `marker` | `register` | The universal-marker form on register operands. Every call to `__custom_fma(a,b,c)` is rewritten to `IFN_RISCV_FMA(a,b,c)`. R4-type. |
| [`bnorm.json`](./bnorm.json) | `bnorm` | `marker` | `memory` | The universal-marker form on **pointer** operands. The RTL pattern uses `(mem:BLK ...)` so the optimiser knows the instruction touches memory of unknown size — the same form `attn` uses. R-type, 3 pointer arguments. |

---

## When to write a new config

Use a JSON config (Way 1) when you already know the exact GIMPLE
shape you want to match. If you would rather hand the pipeline a
plain C file and let it derive a config, use Way 2:

```bash
python3 scripts/customrv.py from-c scripts/examples/<your_demo>.c
```

See [`../examples/README.md`](../examples/README.md).

---

## Schema quick reference

```jsonc
{
  "mnemonic":      "fds",                    // required, lowercase
  "num_inputs":    3,                        // 0/1/2 = R-type/I-type, 3 = R4-type
  "insn_class":    "INSN_CLASS_I",           // optional, default INSN_CLASS_I
  "pattern_kind":  "arith_expr",             // arith_expr | closed_form_loop | marker
  "rtl_kind":      "register",               // "register" or "memory" (mem:BLK)
  "match":         null,                     // auto-allocated when null
  "mask":          null,
  "preferred_slot": "custom-0",              // optional

  // pattern_kind == "arith_expr"
  "arith":   { "outer_op": "MINUS_EXPR", "inner_op": "RDIV_EXPR", "inner_pos": 0 },

  // pattern_kind == "closed_form_loop"
  "loop":    { "reduction_op": "PLUS_EXPR", "step_is_iv": true },

  // pattern_kind == "marker"
  "marker_fn": "__custom_fds"                // optional, defaults to __custom_<mnemonic>
}
```

`null` for `match`/`mask` is the recommended default — the opcode
finder (`scripts/lib/opcodes.py`) walks `custom-0..custom-3` and
picks the first slot that does not collide with any existing
`MATCH_*` entry in `binutils/include/opcode/riscv-opc.h`.
