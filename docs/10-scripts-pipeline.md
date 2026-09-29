# 10 — The `scripts/` injector

`scripts/inject.sh` generalises the recipe in
[`06-extending-toolchain.md`](06-extending-toolchain.md): one spec in, support
for a new custom instruction out, for every tool that needs to know about it.
Usage, spec keys and the backend table are in
[`../scripts/README.md`](../scripts/README.md). This page explains how it works.

## Pipeline

1. **Spec.** Flags and an optional `key=value` file are merged and validated.
2. **Encoding.** `MATCH` and `MASK` come from the format: opcode, the fixed
   funct fields, and every register field the instruction does not use.
3. **Allocation.** The script reads every `MATCH_*` and `MASK_*` pair from
   `riscv-opc.h`. Two encodings overlap when
   `(match1 ^ match2) & mask1 & mask2 == 0`. It walks `custom-0..3` and the free
   funct fields and takes the first combination that overlaps nothing.
4. **Backends.** Each backend renders text from the resolved spec.
5. **Apply (optional).** The binutils and gcc backends write a `patches.list`;
   `--apply` runs each entry through the anchor engine.

## The edit engine

Each entry is `id|file|kind|anchor text|position`. `kind` is `startswith`,
`contains` or `eof`; `position` is `above` or `below`. An entry is skipped when
every non-trivial line of its block is already in the file, so re-running is
safe. The first edit to a file saves `<file>.bak`. Insertion uses `head` and
`tail`, and a file whose first line ends in CR gets CRLF blocks.

## GCC edits

The GCC backend mirrors how `attn` is wired, not the old internal-function
route:

| File | Edit |
|------|------|
| `riscv.opt` | `-m<name>` flag and `TARGET_<NAME>` variable |
| `riscv.md` | `UNSPEC_RISCV_<NAME>` and a `define_insn "riscv_<name>"` with real operand predicates |
| `riscv-builtins.cc` | `AVAIL (x_<name>, ...)` and a `DIRECT_BUILTIN` row |
| `riscv-ftypes.def` | the function type, skipped if it already exists |

## Checks

`scripts/tests/test_inject.sh` needs no toolchain. `--verify <prefix>` is the
end-to-end check against a built toolchain.
