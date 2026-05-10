# `scripts/templates/` — Matcher fragments and the tree-ssa skeleton

This directory holds the C++ source that the pipeline emits into the
GCC tree. There is one **skeleton** that defines the shape of every
generated `tree-ssa-<mnemonic>.cc` file, plus one **matcher fragment**
per supported `pattern_kind`. The renderer in
[`../lib/snippets.py`](../lib/snippets.py) substitutes `{{KEY}}`
placeholders and splices in the right fragment.

This directory is **not** meant to be run. It is the build-time
template store for the code generator.

---

## How these are used

```
              ┌────────────────────────────────────────┐
              │  tree_ssa_template.cc.tmpl            │   ← skeleton
              │  ───────────────────────────────────  │
              │  #includes …                          │
              │  pass_data pass_data_recognize_<m> …  │
              │  {{MATCHER_BODY}}     ← spliced in    │
              │  pass_recognize_<m>::execute (fun)    │
              │  { bool changed = {{EXECUTE_BODY}}; } │
              └────────────────────┬───────────────────┘
                                   │
                       chosen by   │  cfg["pattern_kind"]
                                   ▼
            ┌──────────────────────┬───────────────────┐
            │ matcher_arith_expr   │ matcher_marker    │
            │     .cc.frag         │     .cc.frag      │
            └──────────────────────┴───────────────────┘
                  │
                  └── matcher_closed_form_loop.cc.frag
```

The renderer (`scripts/lib/snippets.py::build_new_cc_file`) loads
the skeleton, picks the right fragment, replaces both `{{KEY}}`s,
and writes the result to
`scripts/out/<mnemonic>/new_files/tree-ssa-<mnemonic>.cc`. The
patcher then copies that file into `gcc/gcc/`.

---

## Files

| File | Used when `pattern_kind ==` | What it does |
|------|-----------------------------|--------------|
| [`tree_ssa_template.cc.tmpl`](./tree_ssa_template.cc.tmpl) | *(always)* | The full skeleton of a GCC GIMPLE pass: includes, `pass_data`, `pass_recognize_<m>` class, `gate()`, `execute()`. Contains `{{MATCHER_BODY}}` and `{{EXECUTE_BODY}}` placeholders that the renderer fills in. |
| [`matcher_arith_expr.cc.frag`](./matcher_arith_expr.cc.frag) | `arith_expr` | Walks every basic block looking for the GIMPLE shape `tmp = a OP_in b; result = tmp OP_out c;` and rewrites it into `IFN_RISCV_<UPPER>(a, b, c)`. The two ops come from `cfg["arith"]` (e.g. `RDIV_EXPR`, `MINUS_EXPR`). |
| [`matcher_closed_form_loop.cc.frag`](./matcher_closed_form_loop.cc.frag) | `closed_form_loop` | Walks every loop with a statically analysable trip count and replaces `for (i=0; i<n; ++i) acc += i;` with a single `IFN_RISCV_<UPPER>(n)` call. Uses SCEV (`number_of_latch_executions`), which is why this pass must run **after** `pass_graphite`. |
| [`matcher_marker.cc.frag`](./matcher_marker.cc.frag) | `marker` | The universal fallback. Walks every basic block and replaces every call to `__custom_<mnemonic>(args…)` with `IFN_RISCV_<UPPER>(args…)`. This is what lets the pipeline support arbitrary instructions whose mathematics we deliberately do **not** simplify (FMA, batchnorm, ∫sin x dx, …). |

---

## Placeholder vocabulary

Every fragment and the skeleton support the same flat replacement
table, populated by `scripts/lib/snippets.py::render`:

| Placeholder | Source | Example |
|-------------|--------|---------|
| `{{MNEMONIC}}` | `cfg["mnemonic"]` | `attn` |
| `{{UPPER}}` | `cfg["upper"]` | `ATTN` |
| `{{FLAG}}` | `cfg["flag"]` | `mattn` |
| `{{TARGET_MACRO}}` | `cfg["target_macro"]` | `TARGET_ATTN` |
| `{{IFN}}` | `cfg["ifn"]` | `RISCV_ATTN` |
| `{{INSN_CLASS}}` | `cfg["insn_class"]` | `INSN_CLASS_I` |
| `{{OPERAND_STRING}}` | derived from `num_inputs` | `d,s,t,r` |
| `{{OPERAND_STRING_HUMAN}}` | derived from `num_inputs` | `rd, rs1, rs2, rs3` |
| `{{NUM_INPUTS}}` | `cfg["num_inputs"]` | `3` |
| `{{PATTERN_KIND}}` | `cfg["pattern_kind"]` | `marker` |
| `{{MATCH}}` | `cfg["match"]` | `0x0000000b` |
| `{{MASK}}` | `cfg["mask"]` | `0x0600707f` |
| `{{MARKER_FN}}` | `cfg.get("marker_fn", ...)` | `__custom_attn` |
| `{{ARITH_OUTER_OP}}` / `{{ARITH_INNER_OP}}` / `{{ARITH_INNER_POS}}` | `cfg["arith"]` (only when `pattern_kind == arith_expr`) | `MINUS_EXPR` etc. |
| `{{LOOP_REDUCTION_OP}}` / `{{LOOP_STEP_IS_IV}}` | `cfg["loop"]` (only when `pattern_kind == closed_form_loop`) | `PLUS_EXPR` / `true` |
| `{{MATCHER_BODY}}` | spliced from the chosen fragment | (whole function) |
| `{{EXECUTE_BODY}}` | hard-coded by the renderer | `try_recognize_<m>_in_function (fun)` |

If you need a placeholder the renderer does not yet provide, add it
in `scripts/lib/snippets.py::render`. Avoid touching the templates
themselves to add per-instruction logic — the design rule here is
that templates are *general* and configs are *specific*.

---

## Adding a new pattern kind

1. Add `templates/matcher_<kind>.cc.frag` in this directory. The
   fragment must define a free function
   `try_recognize_<MNEMONIC>_in_function` returning `bool`
   (`true` when at least one rewrite happened). Use the
   placeholders above.
2. Add `<kind>` to `SUPPORTED_PATTERN_KINDS` in
   [`../lib/config.py`](../lib/config.py).
3. Add a branch in
   [`../lib/snippets.py::build_new_cc_file`](../lib/snippets.py)
   that picks your new fragment when `cfg["pattern_kind"] == "<kind>"`.
4. Optionally extend
   [`../lib/c_analyzer.py`](../lib/c_analyzer.py) so the analyser
   can pick the new pattern automatically from raw C.

That is the entire surface area. No other file in `scripts/` needs
to change.
