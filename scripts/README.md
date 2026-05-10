# `custom_attn/scripts/` — generic custom-RISC-V-instruction pipeline

A 5-script pipeline that takes a small JSON config describing a new
instruction and produces a buildable, patched `riscv-gnu-toolchain` tree
that recognises the corresponding C idiom automatically.

| #  | File                              | Lang   | Purpose                                                                |
|----|-----------------------------------|--------|------------------------------------------------------------------------|
| 01 | `01_identify_free_opcodes.py`     | Python | Find a free `MATCH/MASK` slot in `custom-0..3`. *(unchanged from upstream)* |
| 02 | `02_generate_snippets.py`         | Python | Read config → emit 10 anchor-based patch JSONs + 1 new `.cc`.          |
| 03 | `03_apply_patches.py`             | Python | Interactively patch the toolchain tree (no grep/sed).                  |
| 04 | `04_build_and_smoke_test.sh`      | bash   | `./configure`, `make`, assembler round-trip.                           |
| 05 | `05_run_pattern_test.sh`          | bash   | Compile `tests/<mnemonic>.c` and grep the `.s` output.                 |

## End-to-end usage (fds example)

```bash
cd custom_attn/scripts/

# 1. (optional) inspect free opcode slots
python3 01_identify_free_opcodes.py --inputs 3

# 2. generate the 11 artefacts for fds
python3 02_generate_snippets.py configs/fds.json
#    -> writes out/fds/patches/*.json   (10 files)
#    -> writes out/fds/new_files/tree-ssa-fds.cc
#    -> writes out/fds/resolved_config.json

# 3. apply them to the toolchain tree (interactive, with context)
python3 03_apply_patches.py out/fds --dry-run     # preview
python3 03_apply_patches.py out/fds               # apply for real

# 4. rebuild + smoke test
./04_build_and_smoke_test.sh fds  ~/riscv-gnu-toolchain  $HOME/riscv-install

# 5. pattern-match test
./05_run_pattern_test.sh fds  $HOME/riscv-install  tests/fds.c
```

For `nsum` substitute `nsum` everywhere.

## Config schema (JSON)

```jsonc
{
  "mnemonic":      "fds",                   // required, lowercase
  "num_inputs":    3,                       // 1/2 = R-type, 3 = R4-type
  "insn_class":    "INSN_CLASS_I",
  "pattern_kind":  "arith_expr",            // arith_expr | closed_form_loop
  "match":         null,                    // auto-allocated if null
  "mask":          null,
  "arith":   { "outer_op": "MINUS_EXPR", "inner_op": "RDIV_EXPR", "inner_pos": 0 },
  "loop":    { "reduction_op": "PLUS_EXPR", "step_is_iv": true }
}
```

Auto-derived: `upper`, `flag` (`m<mnemonic>`), `target_macro` (`TARGET_<UPPER>`),
`ifn` (`RISCV_<UPPER>`), `operand_string` (from `num_inputs`).

## How the patcher avoids `grep`/`sed`

Each patch record looks like:

```json
{
  "id": "08",
  "target_file": "gcc/gcc/passes.def",
  "anchor": { "kind": "contains", "text": "NEXT_PASS (pass_graphite)", "which": "first" },
  "position": "below_next_pop",
  "block": "      NEXT_PASS (pass_recognize_fds);\n"
}
```

`03_apply_patches.py`:
1. Opens the target, walks lines from top to bottom.
2. Finds the first line whose `lstrip()` starts with / contains the anchor text.
3. For passes.def's special case, walks further down to the matching
   `POP_INSERT_PASSES ()`.
4. Prints `>>file:line` plus 3 lines of context.
5. Asks `Apply this patch? [y/N]`.
6. Writes back in-place after creating a `.bak`.

## Version pin

`03_apply_patches.py` refuses to run unless:
- `gcc/gcc/BASE-VER`     starts with `15.2`
- `binutils/bfd/version.m4` contains `[2.46…]`

Override with `--force` (anchors may not match on other versions).

## Templates

`templates/tree_ssa_template.cc.tmpl` is a parameterised copy of
`tree-ssa-attn.cc` (includes block, pass_data, pass class, gate,
factory). The pattern-specific matcher is plugged in from one of:

- `templates/matcher_arith_expr.cc.frag`        (e.g. `(a/b) - c` → `IFN_RISCV_FDS(a,b,c)`)
- `templates/matcher_closed_form_loop.cc.frag`  (e.g. `Σ i = n(n-1)/2` → `IFN_RISCV_NSUM(n)`)

Adding a third pattern = add one fragment + one branch in
`02_generate_snippets.build_new_cc_file`.
