"""
config.py — Config loading, normalisation and MATCH/MASK auto-allocation.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from . import opcodes


REQUIRED_FIELDS = ("mnemonic", "num_inputs", "pattern_kind")
SUPPORTED_PATTERN_KINDS = ("arith_expr", "closed_form_loop", "marker")


def derive_names(cfg: dict) -> dict:
    """Fill in derived fields (upper, flag, target_macro, ifn, operand_string)."""
    m = cfg["mnemonic"]
    cfg.setdefault("upper", m.upper())
    cfg.setdefault("flag", "m" + m)
    cfg.setdefault("target_macro", "TARGET_" + m.upper())
    cfg.setdefault("ifn", "RISCV_" + m.upper())
    cfg.setdefault("insn_class", "INSN_CLASS_I")
    cfg.setdefault("rtl_kind", "register")     # "register" or "memory"
    n = cfg["num_inputs"]
    op_strs = {3: "d,s,t,r", 2: "d,s,t", 1: "d,s", 0: "d"}
    op_human = {3: "rd, rs1, rs2, rs3", 2: "rd, rs1, rs2", 1: "rd, rs1", 0: "rd"}
    cfg.setdefault("operand_string", op_strs.get(n, "d"))
    cfg.setdefault("operand_string_human", op_human.get(n, "rd"))
    # Marker function name visible in C source (only used for pattern_kind=marker)
    cfg.setdefault("marker_fn", f"__custom_{m}")
    return cfg


def validate(cfg: dict) -> None:
    for f in REQUIRED_FIELDS:
        if f not in cfg:
            sys.exit(f"ERROR: config missing required field '{f}'")
    if cfg["pattern_kind"] not in SUPPORTED_PATTERN_KINDS:
        sys.exit(f"ERROR: unsupported pattern_kind '{cfg['pattern_kind']}'. "
                 f"Supported: {SUPPORTED_PATTERN_KINDS}")
    if not (0 <= cfg["num_inputs"] <= 3):
        sys.exit("ERROR: num_inputs must be 0..3 (0=no inputs, 3=R4-type).")
    if cfg["pattern_kind"] == "arith_expr" and "arith" not in cfg:
        sys.exit("ERROR: pattern_kind=arith_expr requires 'arith' block.")
    if cfg["pattern_kind"] == "closed_form_loop" and "loop" not in cfg:
        sys.exit("ERROR: pattern_kind=closed_form_loop requires 'loop' block.")


def allocate_match_mask(cfg: dict, repo_root: Path) -> dict:
    """If cfg.match/mask are null/missing, run the opcode finder."""
    if cfg.get("match") is not None and cfg.get("mask") is not None:
        return cfg
    opc_h = opcodes.find_opc_h(repo_root)
    if not opc_h:
        sys.exit(f"ERROR: cannot auto-allocate MATCH/MASK — riscv-opc.h not found "
                 f"under {repo_root}.\n"
                 f"       Initialise the binutils submodule first, OR fill "
                 f"'match' and 'mask' explicitly in your config.")
    existing = opcodes.parse_match_values_from_opc_h(opc_h)
    free = opcodes.find_free_slots(
        existing,
        num_inputs=cfg["num_inputs"],
        preferred_slot=cfg.get("preferred_slot", "custom-0"),
        limit=1,
    )
    if not free:
        sys.exit("ERROR: no free opcode slot found in any custom-N base.")
    slot = free[0]
    cfg["match"] = slot["match"]
    cfg["mask"] = slot["mask"]
    cfg["_allocated_from"] = slot["base"]
    print(f"  Allocated free slot in {slot['base']} ({slot['label']}): "
          f"MATCH=0x{slot['match']:08x} MASK=0x{slot['mask']:08x}")
    return cfg


def load(path: Path) -> dict:
    """Load + validate + derive a config JSON file."""
    cfg = json.loads(Path(path).read_text())
    validate(cfg)
    return derive_names(cfg)
