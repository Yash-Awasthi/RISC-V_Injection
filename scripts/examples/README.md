# `scripts/examples/` — Way-2 C input files (worked examples)

Each `*.c` file in this directory is a hand-written demonstration of
the **"start from a C file"** workflow (Way 2). Hand any of them to
`scripts/customrv.py from-c` and the pipeline will:

1. Analyse the source (`scripts/lib/c_analyzer.py`).
2. Pick the right `pattern_kind` automatically.
3. Allocate a free MATCH/MASK slot in `custom-0..custom-3`.
4. Emit 11 patch artefacts under `scripts/out/<mnemonic>/`.
5. Optionally apply them, then rebuild + run the pattern test.

---

## How to run

```bash
# Just generate the patches (no toolchain modification).
python3 scripts/customrv.py from-c scripts/examples/fma_demo.c

# Generate + interactively apply + rebuild + smoke + pattern test.
python3 scripts/customrv.py from-c scripts/examples/fma_demo.c --apply --build

# Force a different mnemonic (otherwise derived from the function name
# or the embedded __custom_<x>() marker).
python3 scripts/customrv.py from-c scripts/examples/sinx_integral_demo.c \
    --mnemonic sinint --apply --build
```

---

## What each example demonstrates

| File | Marker fn | Detected `pattern_kind` | `rtl_kind` | What it teaches |
|------|-----------|-------------------------|------------|-----------------|
| [`fma_demo.c`](./fma_demo.c) | `__custom_fma(a,b,c)` | `marker` | `register` | The simplest "Way 2" shape: an extern marker call with three scalar operands. The pipeline does *not* try to prove `a*b + c` equivalence — it just rewrites the marker call into the IFN. |
| [`batchnorm_demo.c`](./batchnorm_demo.c) | `__custom_bnorm(x, gamma_beta, out)` | `marker` | `memory` | The accelerator-style shape: three **pointer** arguments. The analyser detects the pointers, picks `rtl_kind: "memory"`, and emits a `(mem:BLK …)` RTL pattern — the same shape as the reference `attn` instruction. |
| [`sinx_integral_demo.c`](./sinx_integral_demo.c) | `__custom_sinint(a_bits, b_bits)` | `marker` | `register` | A two-operand marker. Mathematically `∫ₐᵇ sin x dx = cos a − cos b`, but we deliberately do not encode that identity in the compiler — we just emit one machine instruction and leave the semantics to the future hardware/simulator. |

The four-flavour matrix is completed by the JSON configs in
[`../configs/`](../configs/), which include `arith_expr` and
`closed_form_loop` examples.

---

## How the analyser decides

The detection hierarchy in `scripts/lib/c_analyzer.py` is, in order:

1. **Explicit marker** — does the source call `__custom_<mnem>(...)`
   (or contain a `// @custom: <mnem>` pragma)? → `pattern_kind: "marker"`.
   `rtl_kind` is `"memory"` if any parameter type contains `*`,
   otherwise `"register"`.
2. **arith_expr** — is the body a single
   `return (a OP b) OP c;` statement? → `pattern_kind: "arith_expr"`.
3. **closed_form_loop** — is it a single `for` loop accumulating an
   induction variable? → `pattern_kind: "closed_form_loop"`.
4. **Fallback marker** — anything else gets a synthetic
   `__custom_<mnem>` wrapper so the pipeline still emits *one*
   custom instruction.

This is the same hierarchy that lets the project keep its slogan —
*plain C in, custom RISC-V instruction out* — for any operation the
user can express as a function call.

---

## Writing your own example

The minimum viable Way-2 C file is:

```c
/* my_demo.c */
extern long __custom_myop(long a, long b, long c);

long my_demo(long a, long b, long c)
{
    return __custom_myop(a, b, c);
}
```

That declares the marker, calls it once, and lets the analyser pick
`pattern_kind: "marker"`, `rtl_kind: "register"`, `num_inputs: 3`.
Run:

```bash
python3 scripts/customrv.py from-c my_demo.c --apply --build
```

For pointer-typed operands (i.e. an accelerator-style instruction
that touches memory of unknown size), declare the marker with
pointer parameters:

```c
extern void __custom_myop(float *in, float *aux, float *out);

void my_demo(float *in, float *aux, float *out) { __custom_myop(in, aux, out); }
```

The analyser will switch to `rtl_kind: "memory"` automatically and
emit a `(mem:BLK …)` RTL pattern.
