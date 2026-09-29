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
One file can hold several instructions separated by a line of `---`; they are
allocated together so their encodings never overlap.

| Key | Meaning |
|-----|---------|
| `mnemonic` | lowercase name, `[a-z][a-z0-9_.]*` (GCC backend: no dots) |
| `format` | `R`, `R4`, `I`, `S`, `B`, `U` or `J` (default `R`) |
| `operands` | comma list from `rd,rs1,rs2,rs3,imm` in assembly order; registers left out are locked to 0 in MATCH/MASK |
| `opcode` | `custom-0`..`custom-3` or a 7-bit value ending in `0b11` (default: first slot with room) |
| `funct3`, `funct7`, `funct2` | fixed fields, auto-allocated when absent |
| `semantics` | C expression over `rs1 rs2 rs3 imm pc`. R, R4, I, U: value written to `rd`. S: value stored (default `rs2`). B: branch condition. J: unused |
| `semantics_py` | the same in Python syntax, for renode (default: `semantics`) |
| `semantics_sail` | the same in Sail over `X(rs1)` etc., required by the sail backend |
| `width` | store width in bits: 8, 16, 32 or 64 (S only, default 64) |
| `memory` | `yes` or `no`: touches memory in a way the compiler cannot see (default `yes` for S) |

The script reads `binutils/include/opcode/riscv-opc.h` and refuses any
encoding that overlaps an existing entry. CORE-V, T-Head and MIPS entries
already crowd `custom-0`, so the automatic choice often lands in
`custom-1`. Formats U and J lock a whole opcode and usually cannot be placed
in this tree.

## Backends

Files land in `scripts/out/<mnemonic>/<backend>/`. `out/<mnemonic>/spec` can
be passed back in to reproduce the same encoding. Backends that execute the
instruction (spike, qemu, renode, gem5) skip with a message when there are no
`semantics`.

| Backend | Output | Formats |
|---------|--------|---------|
| `insn` | C macro header and GAS macro using `.insn`; works on any stock toolchain, no rebuild | all (B and J: GAS macro only) |
| `binutils` | edits to `riscv-opc.h` and `riscv-opc.c` (tree edit with `--apply`) | all |
| `gcc` | `-m<name>` flag, `riscv.md` pattern, `__builtin_riscv_<name>` (tree edit with `--apply`); the path `attn` uses. Stores and result-less insns use `unspec_volatile` and a memory clobber | R, R4, I, S, U |
| `llvm` | TableGen `def`, intrinsic and pattern; needs a feature predicate added by hand | all |
| `spike` | extension plugin `.cc`, loaded with `--extlib`, no simulator rebuild | all |
| `qemu` | decode line, translator, helper | all |
| `customasm` | `#ruledef` | all |
| `opcodes` | riscv-opcodes line | all |
| `renode` | `InstallCustomInstructionHandlerFromString` line, no rebuild | R, R4, I, U |
| `sail` | sail-riscv union, encdec, assembly and execute clauses | R, R4, I, U, S |
| `gem5` | `decoder.isa` entry | R, I, S, B, U, J |

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
against the real opcode table, every backend for every format, batch specs,
edit idempotency, CRLF preservation and missing-anchor failure. The LLVM, QEMU,
Spike, renode, Sail and gem5 output follows the upstream file layouts but has
not been built.

Validated against real tools: the encoder and the `insn` output match stock
gas 2.44 for every format, and six instructions injected into a copy of this
tree's binutils 2.46 assemble to the same words `--encode` predicts. The suite
also passes under mawk on Debian. Not validated: GCC edits on the real tree
(only stand-in files), and the LLVM, QEMU, Spike, renode, Sail and gem5 output.
