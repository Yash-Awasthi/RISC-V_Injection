# 10 — `scripts/` pipeline reference

This page is the documentation-set view of the [`scripts/`](../scripts/)
directory. Every file in `scripts/`, including every sub-directory,
is enumerated below with its role, the command that uses it, and a
cross-reference to the deeper module documentation when one exists.

For the command-first quickstart, see
[`../scripts/README.md`](../scripts/README.md). This file is the
long-form companion that the rest of `docs/` links to when it needs
to talk about the generic pipeline without repeating the entire
recipe.

---

## 1. What `scripts/` is for

`scripts/` is a generic re-implementation of the manual process
documented in [`06-extending-toolchain.md`](06-extending-toolchain.md).
The `attn` instruction shipped in [`gcc/`](../gcc/) and
[`binutils/`](../binutils/) was added by hand the first time; every
subsequent instruction can be added with one command:

```bash
python3 scripts/customrv.py from-c my_demo.c --apply --build
```

The driver reads a C file, picks a free opcode slot, generates 11
patches, walks them with the user, rebuilds the toolchain, and runs
a smoke test. The two entry points are documented in
[`../scripts/README.md`](../scripts/README.md#the-two-entry-points).

---

## 2. Top-level `scripts/` files

### 2.1 `customrv.py`

Path: [`../scripts/customrv.py`](../scripts/customrv.py).

The unified driver. Three subcommands:

| Subcommand | Purpose |
|------------|---------|
| `free-opcodes` | Print free MATCH/MASK slots in `custom-0..custom-3`. |
| `from-config <file.json>` | Way 1 — accept a hand-written JSON config. |
| `from-c <file.c>` | Way 2 — derive a config from a C file. |

Almost every user-facing command in `scripts/` ultimately calls
into this script. The library calls it delegates to live under
[`lib/`](../scripts/lib/).

### 2.2 `01_find_opcodes.py`

Path:
[`../scripts/01_find_opcodes.py`](../scripts/01_find_opcodes.py).

Standalone CLI wrapper for the opcode-slot finder. Reads
`binutils/include/opcode/riscv-opc.h`, parses every existing
`#define MATCH_<NAME>`, and prints free slots in
`custom-0..custom-3` for the chosen instruction format
(R-type, I-type, or R4-type). Equivalent to
`customrv.py free-opcodes`.

### 2.3 `02_generate.py`

Path: [`../scripts/02_generate.py`](../scripts/02_generate.py).

Thin CLI wrapper around `lib/snippets.write_all`. Renders the 11
patch artefacts (10 in-place edits + one brand-new
`tree-ssa-<mnemonic>.cc` file) from a config and writes them under
`scripts/out/<mnemonic>/{patches,new_files}/`. It does *not* touch
the toolchain tree; for that you need `03_apply.py`.

### 2.4 `03_apply.py`

Path: [`../scripts/03_apply.py`](../scripts/03_apply.py).

The interactive patcher. Walks each generated patch in order,
resolves its anchor against the current source file, prints a
3-line context window, asks `Apply this patch? [y/N]`, makes a
one-time `.bak`, and writes back in place. Idempotent: re-running
a patch that has already been applied is a no-op. Gated on
GCC == 15.2.x and binutils == 2.46.x (override with `--force`).

### 2.5 `04_build.sh`

Path: [`../scripts/04_build.sh`](../scripts/04_build.sh).

Rebuilds the modified toolchain. After the configure/make cycle it
runs an assemble/disassemble round-trip on a one-line `.S` file to
confirm the new mnemonic encodes correctly. Default `REPO_ROOT`
is derived from `$(dirname $0)`; install prefix defaults to
`$HOME/riscv-install`.

### 2.6 `05_test.sh`

Path: [`../scripts/05_test.sh`](../scripts/05_test.sh).

Compiles `tests/<mnemonic>.c` with the rebuilt compiler at
`-m<mnemonic> -O2 -S` and greps the output for the mnemonic. Exits
non-zero if the instruction is missing. Prints the
`-fdump-tree-<mnem>rec-details` hint when a recognised pattern was
expected but the pass did not emit anything.

### 2.7 `README.md`

Path: [`../scripts/README.md`](../scripts/README.md).

The command-first overview of the whole directory: 30-second example,
the two entry points, full config schema, RTL-kind cheat sheet,
patcher behaviour, version pin, and the recipe for adding a new
`pattern_kind`. This page (in `docs/`) is the long-form companion.

### 2.8 `.gitignore`

Path: `../scripts/.gitignore`.

Excludes generated output from version control. Specifically:
`__pycache__/`, `scripts/out/` (the per-mnemonic patch artefacts
emitted by `02_generate.py`), and `*.bak` files left by the
patcher.

---

## 3. `scripts/lib/` — shared Python library

Path: [`../scripts/lib/`](../scripts/lib/).

The CLI front-ends in `scripts/` are thin wrappers around this
library. Long-form documentation for each module is in
[`../scripts/lib/README.md`](../scripts/lib/README.md).

| File | Role |
|------|------|
| [`lib/__init__.py`](../scripts/lib/__init__.py) | Marks `lib/` as a Python package; no logic. |
| [`lib/opcodes.py`](../scripts/lib/opcodes.py) | Free MATCH/MASK slot allocator. Walks the funct2/funct3/funct7 sub-spaces of each `custom-N` opcode looking for a `(match, mask)` tuple that does not collide with any existing entry in `riscv-opc.h`. |
| [`lib/config.py`](../scripts/lib/config.py) | Config loading + validation (`validate`, `load`), `derive_names` (fills `upper`, `flag`, `target_macro`, `ifn`, `operand_string`), and `allocate_match_mask` (delegates to `opcodes.find_free_slots`). |
| [`lib/c_analyzer.py`](../scripts/lib/c_analyzer.py) | The Way-2 brain. Reads a user-supplied C file and produces a config: detects the marker call, the operand types (scalar vs pointer), the closed-form arithmetic shape, or falls back to a synthetic marker wrapper. |
| [`lib/builders.py`](../scripts/lib/builders.py) | Generates the RTL `define_insn` block and the `internal-fn.cc` expander. Two flavours: `rtl_kind="register"` (ALU-style) and `rtl_kind="memory"` (accelerator-style with `(mem:BLK ...)` operands — the same shape `attn` uses). |
| [`lib/snippets.py`](../scripts/lib/snippets.py) | Owns the canonical patch order. Builds the 11 patch records (each one a small JSON object with an anchor, a position, and an insertion block) and writes them to disk. |
| [`lib/patcher.py`](../scripts/lib/patcher.py) | Anchor-based interactive patch applier. No `grep` / `sed` / regex on file content. Resolves anchor kinds `startswith`, `contains`, `eof`, `open_brace_after`, `return_type_before`; prompts on ambiguity; gates on the GCC and binutils version. |

The five design rules these modules obey (no `grep`/`sed`, always
ask when ambiguous, always make a `.bak`, version-pinned,
pattern-kind = single dispatch) are listed in full in
[`../scripts/lib/README.md`](../scripts/lib/README.md#design-rules-these-modules-obey).

---

## 4. `scripts/templates/` — code-generation templates

Path: [`../scripts/templates/`](../scripts/templates/).

The C++ source that the pipeline emits into `gcc/gcc/`. One
skeleton plus one matcher fragment per supported `pattern_kind`.
Long-form documentation in
[`../scripts/templates/README.md`](../scripts/templates/README.md).

| File | Used when `pattern_kind ==` |
|------|-----------------------------|
| [`templates/tree_ssa_template.cc.tmpl`](../scripts/templates/tree_ssa_template.cc.tmpl) | *(always)* — the skeleton for the generated `tree-ssa-<mnemonic>.cc` GIMPLE pass. Contains `{{MATCHER_BODY}}` and `{{EXECUTE_BODY}}` placeholders. |
| [`templates/matcher_arith_expr.cc.frag`](../scripts/templates/matcher_arith_expr.cc.frag) | `arith_expr` — recognises `(a OP1 b) OP2 c` and rewrites it into `IFN_RISCV_<UPPER>(a, b, c)`. |
| [`templates/matcher_closed_form_loop.cc.frag`](../scripts/templates/matcher_closed_form_loop.cc.frag) | `closed_form_loop` — recognises `for (i=0; i<n; ++i) acc += i;` and rewrites it into `IFN_RISCV_<UPPER>(n)`. Uses SCEV, so this pass must run after `pass_graphite`. |
| [`templates/matcher_marker.cc.frag`](../scripts/templates/matcher_marker.cc.frag) | `marker` — universal fallback. Rewrites every call to `__custom_<mnemonic>(args…)` into `IFN_RISCV_<UPPER>(args…)`. This is what lets the pipeline support arbitrary instructions whose mathematics we deliberately do not simplify. |

The full placeholder vocabulary (`{{MNEMONIC}}`, `{{UPPER}}`,
`{{TARGET_MACRO}}`, …) is listed in
[`../scripts/templates/README.md`](../scripts/templates/README.md#placeholder-vocabulary).

---

## 5. `scripts/configs/` — Way-1 JSON configs

Path: [`../scripts/configs/`](../scripts/configs/).

Four hand-written JSON configs covering every supported
`pattern_kind`. Hand any of them to
`customrv.py from-config` and the pipeline generates a patched
toolchain tree.

| File | Mnemonic | `pattern_kind` | `rtl_kind` |
|------|----------|----------------|------------|
| [`configs/fds.json`](../scripts/configs/fds.json) | `fds` | `arith_expr` | `register` |
| [`configs/nsum.json`](../scripts/configs/nsum.json) | `nsum` | `closed_form_loop` | `register` |
| [`configs/fma.json`](../scripts/configs/fma.json) | `fma` | `marker` | `register` |
| [`configs/bnorm.json`](../scripts/configs/bnorm.json) | `bnorm` | `marker` | `memory` |

The schema is documented in
[`../scripts/README.md`](../scripts/README.md#config-schema-json)
and again in
[`../scripts/configs/README.md`](../scripts/configs/README.md#schema-quick-reference).

---

## 6. `scripts/examples/` — Way-2 C input files

Path: [`../scripts/examples/`](../scripts/examples/).

Three demonstration C files for the "start from a C file" workflow.
The analyser in
[`lib/c_analyzer.py`](../scripts/lib/c_analyzer.py) derives the
config from the source.

| File | Marker fn | Detected `pattern_kind` | `rtl_kind` |
|------|-----------|-------------------------|------------|
| [`examples/fma_demo.c`](../scripts/examples/fma_demo.c) | `__custom_fma(a,b,c)` | `marker` | `register` |
| [`examples/batchnorm_demo.c`](../scripts/examples/batchnorm_demo.c) | `__custom_bnorm(x, gb, out)` | `marker` | `memory` |
| [`examples/sinx_integral_demo.c`](../scripts/examples/sinx_integral_demo.c) | `__custom_sinint(a, b)` | `marker` | `register` |

The analyser's four-strategy detection hierarchy (explicit marker
→ arith_expr → closed_form_loop → fallback marker) is documented in
[`../scripts/examples/README.md`](../scripts/examples/README.md#how-the-analyser-decides).

---

## 7. `scripts/tests/` — sanity tests and per-mnemonic tests

Path: [`../scripts/tests/`](../scripts/tests/).

| File | What it tests |
|------|---------------|
| [`tests/test_pipeline.py`](../scripts/tests/test_pipeline.py) | Seven Python sanity tests for the generator and dry-run patcher. Does not require a built RISC-V toolchain. Run it after every change to `scripts/`. |
| [`tests/fds.c`](../scripts/tests/fds.c) | Compiler-pattern test for `fds` (`arith_expr`). |
| [`tests/nsum.c`](../scripts/tests/nsum.c) | Compiler-pattern test for `nsum` (`closed_form_loop`). |
| [`tests/fma.c`](../scripts/tests/fma.c) | Compiler-pattern test for `fma` (`marker`, register). |
| [`tests/sinint.c`](../scripts/tests/sinint.c) | Compiler-pattern test for `sinint` (`marker`, register, two-operand). |
| [`tests/bnorm.c`](../scripts/tests/bnorm.c) | Compiler-pattern test for `bnorm` (`marker`, generated by `c_analyzer.emit_marker_test_c`). |

The seven checks inside `test_pipeline.py` are listed one by one
in
[`../scripts/tests/README.md`](../scripts/tests/README.md#what-test_pipelinepy-actually-checks).
Run them with:

```bash
python3 scripts/tests/test_pipeline.py
```

Expected output: `7/7 tests passed`.

---

## 8. End-to-end example

Pulling every directory above into one command:

```bash
# 1. Optional sanity check — what's free in the opcode space.
python3 scripts/customrv.py free-opcodes --inputs 3

# 2. Way 2 — hand the driver a C file from scripts/examples/.
python3 scripts/customrv.py from-c scripts/examples/fma_demo.c \
    --apply  \
    --yes    \
    --build  \
    --install $HOME/riscv-install

# 3. Re-run the unit tests after any change to scripts/.
python3 scripts/tests/test_pipeline.py
```

What happens, in terms of the directories enumerated above:

1. `customrv.py` (§ 2.1) calls into `lib/c_analyzer.py` (§ 3) to
   produce a config.
2. `lib/opcodes.py` allocates a free MATCH/MASK slot.
3. `lib/snippets.py` renders 11 patch records, picking the right
   fragment from `templates/` (§ 4) for the chosen `pattern_kind`.
4. `lib/patcher.py` walks each patch against the live `gcc/` and
   `binutils/` trees.
5. `04_build.sh` (§ 2.5) rebuilds the toolchain.
6. `05_test.sh` (§ 2.6) compiles the matching file from
   `tests/<mnemonic>.c` (§ 7) and greps for the mnemonic.

The end result is a `riscv64-unknown-elf-gcc` that recognises a
brand-new `-m<mnemonic>` flag and emits the new instruction
mnemonic — the same property `demo/sdpa_test.c` exercises for the
hand-built `attn` instruction documented in
[`09-demo-walkthrough.md`](09-demo-walkthrough.md).

---

## 9. Cross-references with the rest of `docs/`

* The narrative behind the patch order in `lib/snippets.py` is in
  [`06-extending-toolchain.md`](06-extending-toolchain.md). The
  two are intentionally kept consistent: any change to the recipe
  in doc 06 should also be reflected in
  [`../scripts/lib/snippets.py`](../scripts/lib/snippets.py).
* The reference instruction the pipeline generalises — `attn` — is
  specified in [`01-instruction-spec.md`](01-instruction-spec.md)
  and the pass that recognises it is walked through in
  [`02-compiler-pass.md`](02-compiler-pass.md).
* The list of files the pipeline patches in the toolchain is
  enumerated in [`04-patches-and-files.md`](04-patches-and-files.md).
  The 11 patches `lib/snippets.py` generates correspond
  one-to-one to the 11 hand-edits documented there.
* If the pipeline reports an ICE during the rebuild step, the
  cause is almost certainly already documented in
  [`05-troubleshooting.md`](05-troubleshooting.md).

---

## 10. Coverage check

The following table shows that every file under `scripts/` —
including every sub-directory — is referenced at least once in this
document.

| Path | Referenced in this file |
|------|-------------------------|
| `scripts/README.md` | § 2.7 |
| `scripts/.gitignore` | § 2.8 |
| `scripts/customrv.py` | § 2.1, § 8 |
| `scripts/01_find_opcodes.py` | § 2.2 |
| `scripts/02_generate.py` | § 2.3 |
| `scripts/03_apply.py` | § 2.4 |
| `scripts/04_build.sh` | § 2.5, § 8 |
| `scripts/05_test.sh` | § 2.6, § 8 |
| `scripts/lib/README.md` | § 3 |
| `scripts/lib/__init__.py` | § 3 |
| `scripts/lib/opcodes.py` | § 3, § 8 |
| `scripts/lib/config.py` | § 3 |
| `scripts/lib/c_analyzer.py` | § 3, § 6, § 8 |
| `scripts/lib/builders.py` | § 3 |
| `scripts/lib/snippets.py` | § 3, § 8, § 9 |
| `scripts/lib/patcher.py` | § 3, § 8 |
| `scripts/templates/README.md` | § 4 |
| `scripts/templates/tree_ssa_template.cc.tmpl` | § 4 |
| `scripts/templates/matcher_arith_expr.cc.frag` | § 4 |
| `scripts/templates/matcher_closed_form_loop.cc.frag` | § 4 |
| `scripts/templates/matcher_marker.cc.frag` | § 4 |
| `scripts/configs/README.md` | § 5 |
| `scripts/configs/fds.json` | § 5 |
| `scripts/configs/nsum.json` | § 5 |
| `scripts/configs/fma.json` | § 5 |
| `scripts/configs/bnorm.json` | § 5 |
| `scripts/examples/README.md` | § 6 |
| `scripts/examples/fma_demo.c` | § 6, § 8 |
| `scripts/examples/batchnorm_demo.c` | § 6 |
| `scripts/examples/sinx_integral_demo.c` | § 6 |
| `scripts/tests/README.md` | § 7 |
| `scripts/tests/test_pipeline.py` | § 7, § 8 |
| `scripts/tests/fds.c` | § 7 |
| `scripts/tests/nsum.c` | § 7 |
| `scripts/tests/fma.c` | § 7 |
| `scripts/tests/sinint.c` | § 7 |
| `scripts/tests/bnorm.c` | § 7 |
