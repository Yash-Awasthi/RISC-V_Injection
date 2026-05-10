#!/usr/bin/env python3
"""
01_find_opcodes.py — Print free MATCH/MASK slots for the four custom
                      opcode bases (custom-0..custom-3).

Usage:
  python3 01_find_opcodes.py --inputs 3
  python3 01_find_opcodes.py --inputs 2 --limit 8 --repo-root ~/riscv-gnu-toolchain
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from lib import opcodes  # noqa: E402

DEFAULT_REPO_ROOT = SCRIPT_DIR.parent  # scripts/ is at <repo>/scripts/


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--inputs", type=int, default=3,
                    help="number of input registers (1=R-type, 2=R-type, 3=R4-type, 0=I-type)")
    ap.add_argument("--limit", type=int, default=4,
                    help="how many free slots to print")
    ap.add_argument("--repo-root", default=str(DEFAULT_REPO_ROOT),
                    help="path to riscv-gnu-toolchain root (default: %(default)s)")
    ap.add_argument("--preferred-slot", default="custom-0",
                    choices=list(opcodes.CUSTOM_SLOTS.keys()))
    args = ap.parse_args()

    repo_root = Path(args.repo_root).expanduser().resolve()
    opc_h = opcodes.find_opc_h(repo_root)
    if not opc_h:
        sys.exit(f"ERROR: riscv-opc.h not found under {repo_root}")
    print(f"  riscv-opc.h: {opc_h}")

    existing = opcodes.parse_match_values_from_opc_h(opc_h)
    print(f"  Existing MATCH_<NAME> entries: {len(existing)}")

    free = opcodes.find_free_slots(existing,
                                   num_inputs=args.inputs,
                                   limit=args.limit,
                                   preferred_slot=args.preferred_slot)
    if not free:
        sys.exit("  No free slots found.")

    print(f"\n  Free slots (num_inputs={args.inputs}):")
    print(f"  {'base':<10} {'MATCH':<14} {'MASK':<14} label")
    print(f"  {'-'*10} {'-'*14} {'-'*14} {'-'*30}")
    for s in free:
        print(f"  {s['base']:<10} 0x{s['match']:08x}    0x{s['mask']:08x}    {s['label']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
