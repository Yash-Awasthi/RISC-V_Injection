"""
opcodes.py — Free MATCH/MASK slot allocator for RISC-V custom-N opcodes.

Used by:
  * 01_find_opcodes.py    (CLI front-end)
  * 02_generate.py        (auto-allocation when config.match/mask is null)

The function `find_free_slots(...)` returns a list of dicts:
    [{"base": "custom-0", "match": 0x0200000b, "mask": 0xfe00707f}, ...]

Strategy
--------
For each of the four custom slots (0x0b, 0x2b, 0x5b, 0x7b):
  * Read every existing #define MATCH_<NAME> from binutils riscv-opc.h
    that has the slot's opcode bits.
  * For R-type (num_inputs in {1,2}): the variable field is funct7
    [31:25].  Try funct7 = 0..127 until we find a (match, mask) tuple
    that does not collide with any existing entry.
  * For R4-type (num_inputs == 3): the variable field is funct2
    [26:25].  Try funct2 = 0..3 then walk funct3 = 0..7.
  * For I-type / single-input: only funct3 [14:12] is locked.

This file is the missing piece that the original `01_identify_free_opcodes.py`
referenced from the README — it has been rewritten as a proper library
module so both the CLI and `02_generate.py` can import it.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

# ── opcode-slot constants ───────────────────────────────────────────

CUSTOM_SLOTS = {
    "custom-0": 0x0b,
    "custom-1": 0x2b,
    "custom-2": 0x5b,
    "custom-3": 0x7b,
}

# Mask bit-templates by format.  Funct fields locked → 1 in MASK.
MASK_R_TYPE   = 0xfe00707f   # funct7[31:25] | funct3[14:12] | opcode[6:0]
MASK_R4_TYPE  = 0x0600707f   # funct2[26:25] | funct3[14:12] | opcode[6:0]
MASK_I_TYPE   = 0x0000707f   # funct3[14:12] | opcode[6:0]


def find_opc_h(repo_root: Path) -> Optional[Path]:
    """Locate binutils/include/opcode/riscv-opc.h under repo_root."""
    candidates = [
        repo_root / "binutils" / "include" / "opcode" / "riscv-opc.h",
        repo_root / "binutils-gdb" / "include" / "opcode" / "riscv-opc.h",
    ]
    for c in candidates:
        if c.exists():
            return c
    # last-ditch glob
    for h in repo_root.rglob("riscv-opc.h"):
        if "include/opcode" in str(h):
            return h
    return None


_MATCH_RE = re.compile(r"^\s*#\s*define\s+MATCH_([A-Z0-9_]+)\s+0x([0-9a-fA-F]+)")


def parse_match_values_from_opc_h(opc_h: Path) -> dict[str, int]:
    """Return {NAME: match_value_int} for every MATCH_<NAME> in the file."""
    out: dict[str, int] = {}
    for line in opc_h.read_text().splitlines():
        m = _MATCH_RE.match(line)
        if m:
            out[m.group(1)] = int(m.group(2), 16)
    return out


def _slot_of(match_val: int) -> int:
    return match_val & 0x7f


def _mask_for(num_inputs: int) -> int:
    if num_inputs == 3:
        return MASK_R4_TYPE
    if num_inputs in (1, 2):
        return MASK_R_TYPE
    return MASK_I_TYPE


def _candidate_matches(opcode: int, num_inputs: int):
    """Yield candidate (match, label) for the given format, in stable order."""
    if num_inputs == 3:
        # R4-type: vary funct2 ∈ 0..3 first, then funct3 ∈ 0..7
        for f3 in range(8):
            for f2 in range(4):
                m = opcode | (f3 << 12) | (f2 << 25)
                yield m, f"f3={f3} f2={f2}"
    elif num_inputs in (1, 2):
        # R-type: vary funct7, then funct3
        for f3 in range(8):
            for f7 in range(128):
                m = opcode | (f3 << 12) | (f7 << 25)
                yield m, f"f3={f3} f7=0x{f7:02x}"
    else:
        # I-type: vary funct3 only (imm is variable)
        for f3 in range(8):
            yield opcode | (f3 << 12), f"f3={f3}"


def find_free_slots(existing: dict[str, int], num_inputs: int,
                    *, limit: int = 4,
                    preferred_slot: str = "custom-0") -> list[dict]:
    """
    Return up to `limit` free (base, match, mask) tuples — preferring the
    requested base first, then walking the rest in order.
    """
    used = set(existing.values())
    mask = _mask_for(num_inputs)
    slots = [preferred_slot] + [s for s in CUSTOM_SLOTS if s != preferred_slot]
    out: list[dict] = []
    for base in slots:
        opcode = CUSTOM_SLOTS[base]
        for cand_match, label in _candidate_matches(opcode, num_inputs):
            # Check this match isn't already declared verbatim, AND its
            # *masked* version doesn't collide with any existing entry.
            if cand_match in used:
                continue
            collision = False
            for ev in used:
                if (ev & 0x7f) != opcode:
                    continue
                # If the locked-field portions agree, it's a collision.
                if (cand_match & mask) == (ev & mask):
                    collision = True
                    break
            if collision:
                continue
            out.append({"base": base, "match": cand_match, "mask": mask,
                        "label": label})
            if len(out) >= limit:
                return out
    return out
