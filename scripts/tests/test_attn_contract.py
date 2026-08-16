#!/usr/bin/env python3
"""
test_attn_contract.py -- Repeatable pytest contract for the "attn" custom
instruction, checked straight against the binutils and GCC source trees.
No toolchain build required.

Covers:
  * MATCH_ATTN / MASK_ATTN values in riscv-opc.h
  * the {"attn", ...} row in riscv-opc.c
  * hand-assembling "attn a3,a0,a1,a2" from the R4-type field layout
  * disassembling the resulting word back to the same registers
  * the word appearing in demo/sdpa_test.s
  * MASK_ATTN not colliding with any other custom-0 R4-type entry
  * the __builtin_riscv_attn ftype row, builtin table row, and AVAIL
    predicate in riscv-builtins.cc / riscv-ftypes.def, and that they
    resolve to the "riscv_attn" define_insn in riscv.md
  * demo/attn.h's attn_ptrs/attn_dims/attn_cfg field order, types, and
    byte widths match tools/attn_model.py's ATTN_STRUCTS exactly -- the
    only ABI check that needs no compiler, catching drift between the
    C side and the model with a straight text diff

Run with:
  python -m pytest scripts/tests/test_attn_contract.py -q
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(REPO / "tools"))

from lib import opcodes  # noqa: E402
from attn_model import ATTN_STRUCTS  # noqa: E402

OPC_C = REPO / "binutils" / "opcodes" / "riscv-opc.c"
SDPA_S = REPO / "demo" / "sdpa_test.s"

OPC_H = opcodes.find_opc_h(REPO)
assert OPC_H is not None, "riscv-opc.h not found under repo root"
OPC_H_TEXT = OPC_H.read_text(encoding="utf-8")
MATCH_VALUES = opcodes.parse_match_values_from_opc_h(OPC_H)

_MASK_RE = re.compile(r"^\s*#\s*define\s+MASK_([A-Z0-9_]+)\s+0x([0-9a-fA-F]+)",
                       re.MULTILINE)
MASK_VALUES = {name: int(val, 16) for name, val in _MASK_RE.findall(OPC_H_TEXT)}

CUSTOM_0_OPCODE = opcodes.CUSTOM_SLOTS["custom-0"]

# ABI register names used by "attn a3,a0,a1,a2" -> x-register numbers.
ABI_REGS = {f"a{i}": 10 + i for i in range(8)}

RISCV_DIR = REPO / "gcc" / "gcc" / "config" / "riscv"
FTYPES_DEF = RISCV_DIR / "riscv-ftypes.def"
BUILTINS_CC = RISCV_DIR / "riscv-builtins.cc"
RISCV_MD = RISCV_DIR / "riscv.md"

ATTN_H = REPO / "demo" / "attn.h"

# Byte width on rv64 for every C type attn.h's blocks are allowed to use.
# A type missing here means attn.h used something the model doesn't know
# about -- fail loud rather than guess a width.
C_TYPE_WIDTH = {
    "const void *": 8,
    "uint64_t": 8,
    "uint32_t": 4,
}

_FIELD_RE = re.compile(
    r"^\s*(?P<type>[A-Za-z_][A-Za-z0-9_ ]*?)\s*(?P<star>\*)?\s*"
    r"(?P<name>[A-Za-z_]\w*)\s*;\s*$", re.MULTILINE)


def _parse_struct_fields(text: str, struct_name: str) -> list[tuple[str, str, int]]:
    """Pull the ordered (name, type, width) triples out of a
    "struct NAME { ... };" block in attn.h's own text, via C_TYPE_WIDTH --
    the same source-level shape test_...  and attn_model.py both read."""
    m = re.search(r"struct\s+" + re.escape(struct_name) + r"\s*\{(.*?)\};",
                   text, re.S)
    assert m is not None, f"struct {struct_name} not found in {ATTN_H}"
    fields = []
    for fm in _FIELD_RE.finditer(m.group(1)):
        c_type = fm.group("type").strip()
        if fm.group("star"):
            c_type += " *"
        name = fm.group("name")
        assert c_type in C_TYPE_WIDTH, \
            f"{struct_name}.{name}: unknown C type {c_type!r}, add it to C_TYPE_WIDTH"
        fields.append((name, c_type, C_TYPE_WIDTH[c_type]))
    return fields

# Name built by RISCV_FTYPE_NAME4 for DEF_RISCV_FTYPE (4, (VOID, VOID_PTR,
# VOID_PTR, VOID_PTR, VOID_PTR)) -- the __builtin_riscv_attn prototype.
ATTN_FTYPE = "RISCV_VOID_FTYPE_VOID_PTR_VOID_PTR_VOID_PTR_VOID_PTR"


def _assemble_attn(rd: str, rs1: str, rs2: str, rs3: str) -> int:
    """Encode an R4-type "attn rd,rs1,rs2,rs3" word from MATCH_ATTN's
    own opcode/funct3/funct2 bits, per the "d,s,t,r" operand order."""
    match = MATCH_VALUES["ATTN"]
    opcode = match & 0x7f
    funct3 = (match >> 12) & 0x7
    funct2 = (match >> 25) & 0x3
    return ((ABI_REGS[rs3] << 27) | (funct2 << 25) | (ABI_REGS[rs2] << 20)
             | (ABI_REGS[rs1] << 15) | (funct3 << 12) | (ABI_REGS[rd] << 7)
             | opcode)


def test_match_and_mask_values() -> None:
    assert MATCH_VALUES["ATTN"] == 0x0000000b
    assert MASK_VALUES["ATTN"] == 0x0600707f


def test_declared_in_riscv_opc_c() -> None:
    lines = OPC_C.read_text(encoding="utf-8").splitlines()
    line = lines[474 - 1]
    assert line == '{"attn",        0, INSN_CLASS_I,  "d,s,t,r",  ' \
                    'MATCH_ATTN, MASK_ATTN, match_opcode, 0 },'


def test_assemble_attn_a3_a0_a1_a2() -> None:
    word = _assemble_attn("a3", "a0", "a1", "a2")
    assert word == 0x60b5068b


def test_disassemble_roundtrip() -> None:
    word = 0x60b5068b
    match = MATCH_VALUES["ATTN"]
    mask = MASK_VALUES["ATTN"]
    assert (word & mask) == (match & mask)

    rd = (word >> 7) & 0x1f
    rs1 = (word >> 15) & 0x1f
    rs2 = (word >> 20) & 0x1f
    rs3 = (word >> 27) & 0x1f
    assert (rd, rs1, rs2, rs3) == (ABI_REGS["a3"], ABI_REGS["a0"],
                                    ABI_REGS["a1"], ABI_REGS["a2"])


def test_word_present_in_demo_sdpa() -> None:
    text = SDPA_S.read_text(encoding="utf-8")
    assert "0x60b5068b" in text
    assert re.search(r"\battn\s+a3\s*,\s*a0\s*,\s*a1\s*,\s*a2\b", text)


def test_mask_no_collision_with_other_custom0_entries() -> None:
    mask_attn = MASK_VALUES["ATTN"]
    match_attn = MATCH_VALUES["ATTN"] & mask_attn
    for name, mask in MASK_VALUES.items():
        if name == "ATTN" or mask != mask_attn:
            continue
        match = MATCH_VALUES.get(name)
        if match is None or (match & 0x7f) != CUSTOM_0_OPCODE:
            continue
        assert (match & mask_attn) != match_attn, \
            f"MASK_ATTN collides with MATCH_{name}"


def test_attn_ftype_row_exists() -> None:
    text = FTYPES_DEF.read_text(encoding="utf-8")
    assert re.search(
        r"DEF_RISCV_FTYPE\s*\(\s*4\s*,\s*\(\s*VOID\s*,\s*VOID_PTR\s*,"
        r"\s*VOID_PTR\s*,\s*VOID_PTR\s*,\s*VOID_PTR\s*\)\s*\)",
        text), f"ftype row for __builtin_riscv_attn missing from {FTYPES_DEF}"


def test_attn_avail_defined() -> None:
    text = BUILTINS_CC.read_text(encoding="utf-8")
    assert re.search(r"AVAIL\s*\(\s*attn\s*,\s*TARGET_ATTN\s*\)", text), \
        f"AVAIL (attn, TARGET_ATTN) missing from {BUILTINS_CC}"


def test_attn_builtin_row_references_ftype() -> None:
    text = BUILTINS_CC.read_text(encoding="utf-8")
    m = re.search(
        r"DIRECT_NO_TARGET_BUILTIN\s*\(\s*attn\s*,\s*([A-Za-z0-9_]+)\s*,"
        r"\s*attn\s*\)",
        text)
    assert m is not None, \
        f"DIRECT_NO_TARGET_BUILTIN (attn, ..., attn) missing from {BUILTINS_CC}"
    assert m.group(1) == ATTN_FTYPE, \
        f"builtin row uses ftype {m.group(1)!r}, expected {ATTN_FTYPE!r}"


def test_attn_code_for_matches_md_pattern() -> None:
    # DIRECT_NO_TARGET_BUILTIN (attn, ...) expands to CODE_FOR_riscv_attn,
    # which only exists if riscv.md defines a "riscv_attn" insn.
    text = RISCV_MD.read_text(encoding="utf-8")
    assert re.search(r'\(define_insn\s+"riscv_attn"', text), \
        f'define_insn "riscv_attn" missing from {RISCV_MD}'


@pytest.mark.parametrize("struct_name", sorted(ATTN_STRUCTS))
def test_attn_h_struct_matches_model(struct_name: str) -> None:
    text = ATTN_H.read_text(encoding="utf-8")
    parsed = _parse_struct_fields(text, struct_name)
    assert parsed == ATTN_STRUCTS[struct_name], \
        f"demo/attn.h struct {struct_name} drifted from " \
        f"tools/attn_model.py ATTN_STRUCTS[{struct_name!r}]:\n" \
        f"  attn.h : {parsed}\n  model  : {ATTN_STRUCTS[struct_name]}"


def test_attn_h_struct_sizes_are_8_byte_multiples() -> None:
    # Every block is pointed to from a register, so it must be
    # naturally addressable -- a stray odd byte count would misalign
    # whichever block follows it on the stack.
    for name, fields in ATTN_STRUCTS.items():
        size = sum(width for _, _, width in fields)
        assert size % 8 == 0, f"{name} size {size} is not 8-byte aligned"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
