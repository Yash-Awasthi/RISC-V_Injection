<p align="center">
  <a href="./README.md"><strong>Overview</strong></a> ·
  <a href="./docs/README.md">Docs index</a> ·
  <a href="./demo/README.md">Reference demo</a> ·
  <a href="./scripts/README.md">Generic pipeline</a>
</p>

<p align="center">
  <a href="./docs/00-background.md">Background</a> ·
  <a href="./docs/01-instruction-spec.md">Instruction Spec</a> ·
  <a href="./docs/02-compiler-pass.md">Compiler Pass</a> ·
  <a href="./docs/03-build-and-run.md">Build &amp; Run</a> ·
  <a href="./docs/04-patches-and-files.md">Patches &amp; Files</a> ·
  <a href="./docs/05-troubleshooting.md">Troubleshooting</a> ·
  <a href="./docs/06-extending-toolchain.md">Extending</a> ·
  <a href="./docs/07-research-context.md">Research Context</a> ·
  <a href="./docs/08-glossary.md">Glossary</a>
</p>

# `attn` — A Custom RISC-V Instruction for Transformer Attention

> A modified `riscv-gnu-toolchain` that adds a custom `attn` machine
> instruction and exposes it two ways: an explicit
> `__builtin_riscv_attn` builtin (the primary, non-experimental
> path — no inline assembly, no `.insn` directives), and an
> experimental, off-by-default GIMPLE pass that tries to recognise
> the scaled dot-product attention (SDPA) pattern in ordinary,
> unmodified C code and rewrite it to the same instruction. The
> instruction itself has never executed anywhere — there is no
> simulator or hardware model for it (see the status table below).

---

## How to run the program

This section is the shortest possible recipe for going from a fresh
clone to a verified `attn` instruction in compiler output. For the
narrative version with troubleshooting, see
[`docs/03-build-and-run.md`](docs/03-build-and-run.md).

### 0. Prerequisites (once per machine)

```bash
sudo apt-get update
sudo apt-get install -y \
    autoconf automake autotools-dev curl python3 python3-pip python3-tomli \
    libmpc-dev libmpfr-dev libgmp-dev gawk build-essential bison flex \
    texinfo gperf libtool patchutils bc zlib1g-dev libexpat-dev ninja-build \
    git cmake libglib2.0-dev libslirp-dev libncurses-dev
```

Disk: ≥ 12 GiB free. RAM: ≥ 8 GiB. Build time: 45–90 min on 8 cores.
Tested on Ubuntu 24.04 / WSL2.

### 1. Clone the repository

```bash
git clone https://github.com/Yash-Awasthi/CD.git riscv-attn
cd riscv-attn
```

### 2. Configure and build the modified toolchain

```bash
./configure --prefix=$HOME/riscv-install \
            --with-arch=rv64gc --with-abi=lp64d --disable-gdb
make -j$(nproc) 2>&1 | tee build.log
```

Produces `riscv64-unknown-elf-{gcc,as,objdump,…}` under
`$HOME/riscv-install/bin/`.

### 3. Verify the assembler accepts `attn` (Layer 1)

```bash
echo '_start: attn a3, a0, a1, a2' > /tmp/t.S
$HOME/riscv-install/bin/riscv64-unknown-elf-as /tmp/t.S -o /tmp/t.o
$HOME/riscv-install/bin/riscv64-unknown-elf-objdump -d /tmp/t.o | grep attn
# Expect: ... 0000000b ... attn  a3,a0,a1,a2
```

### 4. Verify GCC emits `attn` automatically from plain C (Layer 2)

```bash
$HOME/riscv-install/bin/riscv64-unknown-elf-gcc \
    -mattn -mattn-recognize -O2 \
    -fno-schedule-insns -fno-schedule-insns2 \
    -S demo/sdpa_test.c -o /tmp/sdpa_test.s
grep -n '\battn\b' /tmp/sdpa_test.s
# Expect: a single line such as:
#   ...:        attn    a3,a0,a1,a2
```

### 5. Run the full verification harness (recommended)

```bash
chmod +x demo/verify_attn.sh
./demo/verify_attn.sh demo/sdpa_test.c
# Expect: all PASS lines and a final “verification complete”.
```

### 6. Add *another* custom instruction (optional)

The `scripts/` directory is a generic pipeline that turns either a
JSON config or a plain C file into a fully patched toolchain tree
that recognises a *new* custom mnemonic. End-to-end recipe:

```bash
# Sanity test (does NOT need a built toolchain — only the source tree):
python3 scripts/tests/test_pipeline.py        # 7/7 should pass

# Real use: from a JSON config (Way 1)
python3 scripts/customrv.py from-config scripts/configs/fds.json --apply --build

# Real use: from a C source file (Way 2 — recommended)
python3 scripts/customrv.py from-c scripts/examples/fma_demo.c --apply --build
```

See [`scripts/README.md`](scripts/README.md) for the full surface.

---

## At a glance

| Item | Value |
|------|-------|
| Mnemonic | `attn` |
| Format | R4-type (4 register operands, like `fmadd`) |
| Opcode slot | `custom-0` (`opcode[6:0] = 0x0b`) |
| MATCH / MASK | `0x0000000b` / `0x0600707f` |
| Compiler flag | `-mattn` (instruction + `__builtin_riscv_attn`, primary path), `-mattn-recognize` (experimental idiom recognizer, off by default) |
| GCC version | 15.2.0 (riscv-gnu-toolchain fork) |
| Binutils version | 2.46 |
| Pass position | `attnrec` idiom recognizer: #179 in the GIMPLE pipeline (after Graphite) |
| Builtin | `__builtin_riscv_attn(rd, rs1, rs2, rs3)`, maps 1:1 onto the `attn` RTL pattern — no internal function involved |
| Status | Encoding, builtin, and recognizer are toolchain-side complete; the instruction has never executed on hardware or a simulator; the recognizer is experimental and known-unsound (see status table below) |

The instruction is the compiler-visible counterpart of the operation
implemented by every Transformer self-attention block:

$$
\text{Attention}(Q, K, V) \;=\; \mathrm{softmax}\!\left(\frac{Q K^{\top}}{\sqrt{d_k}}\right) V
$$

A plain C implementation of this expression — four loop nests, an
`expf` call, a division, several thousand multiply-adds — is reduced
by the compiler to a single 32-bit machine word:

```asm
attn  a3, a0, a1, a2     # rd = O,  rs1 = Q,  rs2 = K,  rs3 = V
```

---

## Repository layout

```
CD/
├── README.md                 ← you are here (overview + how to run)
├── docs/                     ← deep-dive documentation set
│   └── README.md             ← documentation index (audience guide)
├── demo/                     ← reference attn demonstration
│   ├── README.md
│   ├── sdpa_test.c           ← canonical SDPA test source
│   ├── sdpa_test.s           ← expected assembly output
│   ├── sdpa_test.c.179t.attnrec  ← GIMPLE dump after pass #179
│   └── verify_attn.sh        ← end-to-end verification harness
├── scripts/                  ← generic “add a new custom RISC-V insn” pipeline
│   ├── README.md
│   ├── customrv.py           ← unified driver
│   ├── 01_find_opcodes.py …  ← stage-by-stage helpers
│   ├── configs/              ← Way-1 JSON configs (worked examples)
│   ├── examples/             ← Way-2 C input files (worked examples)
│   ├── lib/                  ← shared Python library (analyser, patcher, …)
│   ├── templates/            ← matcher fragments + tree-ssa skeleton
│   └── tests/                ← sanity tests + generated C tests
├── gcc/                      ← upstream GCC 15.2 source tree (modified) — DO NOT TOUCH MANUALLY
└── binutils/                 ← upstream binutils 2.46 source tree (modified) — DO NOT TOUCH MANUALLY
```

The `gcc/` and `binutils/` directories are vendored upstream sources
with the project's modifications applied in place. Every change in
those trees is enumerated, with line numbers and rationales, in
[`docs/04-patches-and-files.md`](docs/04-patches-and-files.md).

---

## What this project actually contains

This repository is a fork of [`riscv-gnu-toolchain`](https://github.com/riscv-collab/riscv-gnu-toolchain).
On top of the upstream sources, it adds:

1. **A new GIMPLE optimisation pass** (`attnrec`,
   `gcc/gcc/tree-ssa-attn.cc`, ~500 lines) that walks every loop nest
   in the function being compiled and decides whether it implements
   SDPA.
2. **An explicit GCC builtin**, `__builtin_riscv_attn`
   (`gcc/gcc/config/riscv/riscv-builtins.cc`,
   `gcc/gcc/config/riscv/riscv-ftypes.def`), that maps its four
   `void *` arguments straight onto the RTL pattern below — no
   internal function, no hand-written expander. Used directly by
   [`demo/attn.h`](demo/attn.h) and [`demo/sdpa_builtin.c`](demo/sdpa_builtin.c).
   An earlier revision routed this through a dedicated internal
   function, `IFN_RISCV_ATTN`; that internal function has since been
   deleted from the tree in favour of the direct builtin call.
3. **A new RTL pattern** (`define_insn "riscv_attn"` in
   `gcc/gcc/config/riscv/riscv.md`) that emits the assembly mnemonic.
4. **Two new compiler flags** (`gcc/gcc/config/riscv/riscv.opt`):
   `-mattn` makes the `attn` instruction and the builtin available —
   this is the primary, non-experimental path; `-mattn-recognize`
   separately enables the experimental idiom recognizer described
   above, and is off by default.
5. **A new binutils opcode table entry** so that GAS can encode `attn`
   and `objdump` can disassemble it
   (`binutils/include/opcode/riscv-opc.h`,
   `binutils/opcodes/riscv-opc.c`).

Two entry points exist side by side. The **explicit builtin**
(`__builtin_riscv_attn`, via [`demo/attn.h`](demo/attn.h)) requires
the programmer to state the operation, in exchange for no
pattern-matching risk — this is the recommended path. The
**idiom recognizer** (`attnrec`) instead leaves the user's C code
unchanged — no header, no intrinsic call, no inline-asm block — and
tries to detect SDPA automatically during normal `-O2` compilation.
It is gated by the separate, off-by-default `-mattn-recognize` flag
precisely because that automatic detection is experimental and known
to be unsound in at least one documented case — see the
known-limitation note in
[`docs/02-compiler-pass.md`](docs/02-compiler-pass.md) and the
known-false-positive case in
[`demo/failures/README.md`](demo/failures/README.md#known-false-positive-fail_scattered-signature-known-false-positivec)
before enabling it on real code.

---

## The documentation set

The long-form documentation under `docs/` is written for two
audiences in parallel: a CS undergraduate who has never opened a
compiler source tree, and a research supervisor or reviewer who
wants the depth and the citations. Every file is self-contained;
the numbering below is a suggested reading order, not a hard
dependency chain.

| File | Audience | Read it for |
|------|----------|-------------|
| [`README.md`](README.md) | everyone | this overview |
| [`docs/README.md`](docs/README.md) | everyone | docs index + reading order |
| [`docs/00-background.md`](docs/00-background.md) | undergraduate | RISC-V, attention, the GCC pipeline, GIMPLE/SSA, custom instructions — from zero |
| [`docs/01-instruction-spec.md`](docs/01-instruction-spec.md) | undergraduate + supervisor | the ISA-level specification of `attn` (encoding, semantics, ABI, worked decoding) |
| [`docs/02-compiler-pass.md`](docs/02-compiler-pass.md) | undergraduate + supervisor | the `attnrec` pass — how it detects SDPA and emits the instruction |
| [`docs/03-build-and-run.md`](docs/03-build-and-run.md) | undergraduate | how to build the toolchain and verify each layer |
| [`docs/04-patches-and-files.md`](docs/04-patches-and-files.md) | undergraduate + supervisor | every file that changed, the exact diff, and *why* |
| [`docs/05-troubleshooting.md`](docs/05-troubleshooting.md) | implementer | every ICE / build error encountered, with root cause and fix |
| [`docs/06-extending-toolchain.md`](docs/06-extending-toolchain.md) | researcher | template for adding *any* new RISC-V custom instruction |
| [`docs/07-research-context.md`](docs/07-research-context.md) | supervisor | related work, novelty claim, limitations, future research |
| [`docs/08-glossary.md`](docs/08-glossary.md) | undergraduate | every acronym and term used in this repo, defined |

---

## Suggested reading order

**If you have ~30 minutes and want the gist**

1. This README.
2. [`docs/00-background.md`](docs/00-background.md) §1–§4
   (just enough RISC-V and just enough attention).
3. [`docs/01-instruction-spec.md`](docs/01-instruction-spec.md) §1–§3.
4. [`docs/02-compiler-pass.md`](docs/02-compiler-pass.md) §1–§4
   (the high-level idea of the pass).

**If you are evaluating this as research output**

1. [`docs/07-research-context.md`](docs/07-research-context.md) (positioning).
2. [`docs/02-compiler-pass.md`](docs/02-compiler-pass.md) (the contribution).
3. [`docs/01-instruction-spec.md`](docs/01-instruction-spec.md) (the artefact).
4. [`docs/05-troubleshooting.md`](docs/05-troubleshooting.md) (depth of engagement with GCC internals).

**If you want to reproduce the build**

1. [`docs/03-build-and-run.md`](docs/03-build-and-run.md).
2. [`docs/05-troubleshooting.md`](docs/05-troubleshooting.md) when something
   inevitably goes wrong.

---

## What the result looks like

Compiling a clean C implementation of fused SDPA (`demo/sdpa_test.c`)
with the modified toolchain:

```bash
$HOME/riscv-install/bin/riscv64-unknown-elf-gcc \
    -mattn -mattn-recognize -O2 \
    -fno-schedule-insns -fno-schedule-insns2 \
    -S demo/sdpa_test.c -o /tmp/sdpa_test.s
```

produces, among the usual prologue/epilogue, a single line:

```asm
        attn    a3,a0,a1,a2
```

That one instruction stands in for what is otherwise hundreds of
lines of unrolled vectorised loop body. The original loops are still
present in the output — the compiler matched the pattern but did not
*prove* the hardware is semantically equivalent — see
[§7 of `docs/02-compiler-pass.md`](docs/02-compiler-pass.md#7-why-the-loop-body-stays-and-what-removing-it-would-take)
for why this is the correct behaviour and how to take the next step.

---

## Project status and what is *not* in this repository

| Layer | Status | Where it lives |
|-------|--------|----------------|
| Toolchain-side encoding (assembler / disassembler) | Complete | this repo |
| Explicit builtin (`__builtin_riscv_attn`, primary path) | Complete; ABI (`attn_ptrs`/`attn_dims`/`attn_cfg`) matches [`docs/01-instruction-spec.md` §4](docs/01-instruction-spec.md#4-operand-convention-and-abi) | this repo, see [`demo/attn.h`](demo/attn.h) |
| Compiler pattern matching (`attnrec` pass) | Experimental, off by default (`-mattn-recognize`). **Known unsound**, not just incomplete: [`demo/failures/fail_scattered-signature-known-false-positive.c`](demo/failures/fail_scattered-signature-known-false-positive.c) is a documented, currently-unfixed case where the matcher accepts non-attention code. It also does not build the block ABI above — it emits the pre-ABI raw O/Q/K/V pointer form, a known limitation stated in the pass's own source. Do not enable on real code; see `plan.md` item 1. | this repo |
| RTL / IR plumbing (`define_insn`, builtin expansion) | Complete | this repo |
| Generic pipeline for *new* custom instructions | Complete | `scripts/` |
| Semantics: executable Python model, no RTL | Complete | [`tools/attn_model.py`](tools/attn_model.py) |
| **Instruction executed anywhere** (simulator or hardware) | **Never executed. No Spike/QEMU model and no hardware exist for `attn`** — Phase 4, not started | future work |
| Synthesisable RTL (Verilog/Chisel) for an accelerator | Out of scope | future work |
| Equivalence proof / verified loop deletion | Out of scope — blocked on the row above | future work |

Because the instruction has never executed, nothing in this repo has
measured whether `attn` is correct, let alone whether it is faster
than the loop it replaces — the compiled output always contains
*both* the `attn` call and the original loop body (see below), and
the loop body is what actually runs today, on every path, including
the primary builtin one. The contribution of this project is
therefore precisely the **software-side custom-instruction
infrastructure**, demonstrated end to end on a non-trivial
computation, not a working accelerator. The hardware accelerator is
the natural next step in a hardware/software co-design pipeline; see
[§5 of `docs/07-research-context.md`](docs/07-research-context.md#5-future-work)
and `plan.md`.

---

## Author and credits

* **Author:** Yash Awasthi
* **Upstream toolchain:** `riscv-gnu-toolchain` (RISC-V International)
* **Compiler:** GCC 15.2.0
* **Binutils:** 2.46
* **Host platform tested:** Ubuntu 24.04 / WSL2

Bug reports and questions are welcome via the repository issue tracker.

---

## License

The toolchain sources retain their original licenses (GPLv3 for GCC,
GPLv3 / LGPL for binutils, etc.). The project-specific additions
(`gcc/gcc/tree-ssa-attn.cc`, the documentation set under `docs/`,
the reference test program in `demo/`, and the generic pipeline in
`scripts/`) are released under the same license as the component
they extend.
