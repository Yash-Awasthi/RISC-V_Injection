# `scripts/lib/` — Shared Python library for the customrv pipeline

This package implements the *logic* of the
"add-a-new-custom-RISC-V-instruction" pipeline. The CLI front-ends
in `scripts/` (`customrv.py`, `01_find_opcodes.py`,
`02_generate.py`, `03_apply.py`) are thin wrappers that import these
modules.

This directory is **not** an executable. There is nothing to run
here; you import it.

---

## How to use this library (from another script)

```python
import sys
from pathlib import Path

# Make the parent of this lib/ importable (i.e. scripts/).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lib import c_analyzer, config as cfgmod, opcodes, snippets, patcher

REPO_ROOT = Path("/path/to/CD")

# 1. Analyse a C file → partial config.
cfg = c_analyzer.analyze(Path("my_demo.c"), mnemonic="myop")

# 2. Fill in derived names + allocate MATCH/MASK.
cfg = cfgmod.derive_names(cfg)
cfgmod.validate(cfg)
cfg = cfgmod.allocate_match_mask(cfg, REPO_ROOT)

# 3. Render every patch artefact under scripts/out/myop/.
out = Path("scripts/out/myop")
snippets.write_all(cfg, out, Path("scripts/templates"))

# 4. Apply them to the toolchain tree.
patcher.check_versions(REPO_ROOT, force=False)
for p in sorted((out / "patches").glob("*.json")):
    patcher.apply_one(REPO_ROOT, json.loads(p.read_text()),
                      dry_run=False, assume_yes=True)
```

In practice you almost never write this by hand — the CLI driver
[`customrv.py`](../customrv.py) does exactly this.

---

## Modules

| File | Lines | Responsibility |
|------|-------|----------------|
| [`__init__.py`](./__init__.py) | 1 | Marks `lib/` as a Python package; no logic. |
| [`opcodes.py`](./opcodes.py) | 141 | Free MATCH/MASK slot allocator for `custom-0..custom-3`. Reads `binutils/include/opcode/riscv-opc.h`, parses every `#define MATCH_<NAME>`, and walks the funct2/funct3/funct7 sub-spaces to find a slot that does not collide. |
| [`config.py`](./config.py) | 83 | Config loading + validation (`validate`, `load`), `derive_names` (fills `upper`, `flag`, `target_macro`, `ifn`, `operand_string`), and `allocate_match_mask` (delegates to `opcodes.find_free_slots`). |
| [`c_analyzer.py`](./c_analyzer.py) | 308 | "Way 2": derive a config from a user-supplied C file. Implements the four-strategy hierarchy (explicit marker → arith_expr → closed_form_loop → fallback marker). Also emits `tests/<mnem>.c` for marker-based configs. |
| [`builders.py`](./builders.py) | 118 | Renders the per-instruction RTL `define_insn` block and the `internal-fn.cc` expander. Two flavours: `rtl_kind="register"` (classic ALU-style) and `rtl_kind="memory"` (accelerator-style with `(mem:BLK ...)` operands). |
| [`snippets.py`](./snippets.py) | 219 | Builds the 11 patch records (10 in-place edits + 1 new `tree-ssa-<mnem>.cc`) and writes them under `scripts/out/<mnem>/`. Owns the canonical patch order and the per-pattern matcher fragment selection. |
| [`patcher.py`](./patcher.py) | 253 | Anchor-based interactive patch applier. **No grep, no sed, no regex search-and-replace on file content.** Resolves anchors (`startswith`, `contains`, `eof`, `open_brace_after`, `return_type_before`), prompts on ambiguity, prints 3-line context windows, asks `y/N`, makes a `.bak`, is idempotent. Also gates on GCC == 15.2.x and binutils == 2.46.x. |

---

## Design rules these modules obey

* **No `grep` / `sed` / regex on file content.** The patcher walks
  the target file line-by-line and resolves a structured anchor
  record. This is what makes the pipeline survive small upstream
  drift without exploding.
* **Always ask when ambiguous.** If an anchor matches multiple
  lines, prompt the user (unless `--yes` is passed). Only the `eof`
  anchor never prompts.
* **Always make a `.bak`** before any in-place edit. Idempotent —
  re-applying a patch is a no-op once the distinctive token is
  already present.
* **Version-pinned.** The patcher refuses to run unless GCC and
  binutils versions match what the patch templates were authored
  against (override with `--force`). See `patcher.check_versions`.
* **Pattern-kind = single dispatch.** Every per-pattern decision
  lives in exactly one place: `c_analyzer` (detection),
  `snippets.build_new_cc_file` (template selection), and the matcher
  fragments under [`../templates/`](../templates/) (the actual
  C++ matcher code).

---

## Adding a new pattern kind

1. Drop a `templates/matcher_<kind>.cc.frag` next to the existing
   ones; it must define `try_recognize_<MNEMONIC>_in_function`.
2. Add `<kind>` to `SUPPORTED_PATTERN_KINDS` in
   [`config.py`](./config.py).
3. Extend `build_new_cc_file` in [`snippets.py`](./snippets.py) so
   that the new kind picks the new fragment.
4. Optionally extend [`c_analyzer.py`](./c_analyzer.py) so the
   analyser can pick it automatically from a raw C file.

That is the entire surface area. No other file in `scripts/` needs
to change.

---

## Testing

The library is exercised by [`../tests/test_pipeline.py`](../tests/test_pipeline.py).
It does **not** require a built RISC-V toolchain — it only touches
the source tree.

```bash
python3 scripts/tests/test_pipeline.py
# Expected: 7/7 tests pass
```
