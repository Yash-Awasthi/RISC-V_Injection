# `scripts/` — inject any custom RISC-V instruction

`inject.sh` is a single bash script, with no Python, that runs the same on
Linux, macOS, WSL and Windows Git Bash. It takes one instruction spec and
writes support for it into every tool in the flow. The reference `attn`
instruction was added by hand; this script is that work made repeatable.

```bash
scripts/inject.sh --mnemonic mac --format R4 --semantics "rs1 * rs2 + rs3"
scripts/inject.sh spec.txt --backend spike,insn
scripts/inject.sh spec.txt --backend binutils,gcc --apply      # asks per edit; --yes to skip
scripts/inject.sh spec.txt --encode rd=3,rs1=1,rs2=2,rs3=4     # print the machine word
scripts/inject.sh spec.txt --verify $HOME/riscv-install        # assemble + compile with the built toolchain
```

## Spec

Either flags or a `key=value` file (flags win). Only `mnemonic` is required.

| Key | Meaning |
|-----|---------|
| `mnemonic` | lowercase name, `[a-z][a-z0-9_.]*` (GCC backend: no dots) |
| `format` | `R`, `R4`, `I`, `S`, `B`, `U` or `J` (default `R`) |
| `operands` | comma list from `rd,rs1,rs2,rs3,imm` in assembly order; registers left out are locked to 0 in MATCH/MASK |
| `opcode` | `custom-0`..`custom-3` or a 7-bit value ending in `0b11` (default: first slot with room) |
| `funct3`, `funct7`, `funct2` | fixed fields, auto-allocated when absent |
| `semantics` | C expression over `rs1 rs2 rs3 imm pc`; result goes to `rd` (format B: branch condition) |

The script reads `binutils/include/opcode/riscv-opc.h` and refuses any
encoding that overlaps an existing entry. CORE-V, T-Head and MIPS entries
already crowd `custom-0`, so the automatic choice often lands in
`custom-1`. Formats U and J lock a whole opcode and usually cannot be placed
in this tree.

## Backends

Files land in `scripts/out/<mnemonic>/<backend>/`. `out/<mnemonic>/spec` can
be passed back in to reproduce the same encoding.

| Backend | Output | Notes |
|---------|--------|-------|
| `insn` | C macro header and GAS macro using `.insn` | works on any stock toolchain, no rebuild |
| `binutils` | edits to `riscv-opc.h` and `riscv-opc.c` | tree edit with `--apply` |
| `gcc` | `-m<name>` flag, `riscv.md` pattern, `__builtin_riscv_<name>` | tree edit with `--apply`; R, R4, I only; the same path `attn` uses |
| `llvm` | TableGen `def`, intrinsic and pattern | R, R4, I; needs a feature predicate added by hand |
| `spike` | extension plugin `.cc` | loaded with `--extlib`, no simulator rebuild |
| `qemu` | decode line, translator, helper | R, R4, I, U |
| `customasm` | `#ruledef` | R, R4, I, U |
| `opcodes` | riscv-opcodes line | all formats |

Unsupported format and backend pairs are skipped with a message.

## Applying to a tree

`--apply` edits only the binutils and gcc backends. Each edit is anchored on
a line of the target file, skipped if already present, backed up once as
`<file>.bak`, and CRLF-safe. The tree must be binutils 2.46 and GCC 15.2
unless `--force` is given. `--dry-run` shows every edit without writing.

After `--apply`, rebuild with `04_build.sh <mnemonic>`, then run
`inject.sh ... --verify <install prefix>`. That assembles the instruction with
the built `as`, compares the word to `--encode`, and compiles the generated
`gcc/<name>.c` to check the builtin emits the mnemonic.

## Checks

```bash
bash scripts/tests/test_inject.sh
```

Covers the encoder against hand-checked RISC-V words, collision detection
against the real opcode table, every backend for every format, edit
idempotency, CRLF preservation and missing-anchor failure. The LLVM, QEMU and
Spike output follows the upstream file layouts but has not been built.
