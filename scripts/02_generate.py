#!/usr/bin/env python3
"""
02_generate.py — Read a JSON config, emit 10 anchor-based patch records
                 + 1 new tree-ssa-<mnemonic>.cc.

Usage:
  python3 02_generate.py configs/fds.json
  python3 02_generate.py configs/nsum.json --repo-root ~/riscv-gnu-toolchain
  python3 02_generate.py configs/fma.json  --match 0x0200000b --mask 0xfe00707f
  python3 02_generate.py configs/fds.json  --out /tmp/fds_out
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from lib import config as cfgmod  # noqa: E402
from lib import snippets          # noqa: E402

DEFAULT_REPO_ROOT = SCRIPT_DIR.parent       # <repo>/scripts/ -> <repo>/
TEMPLATE_DIR = SCRIPT_DIR / "templates"


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate 11 patch records for a "
                                              "custom RISC-V instruction.")
    ap.add_argument("config", help="Path to JSON config (e.g. configs/fds.json)")
    ap.add_argument("--repo-root", default=str(DEFAULT_REPO_ROOT),
                    help="Path to riscv-gnu-toolchain root (default: %(default)s)")
    ap.add_argument("--match", help="Override MATCH (hex, e.g. 0x0200000b)")
    ap.add_argument("--mask",  help="Override MASK  (hex, e.g. 0xfe00707f)")
    ap.add_argument("--out", default=None,
                    help="Output dir (default: <scripts>/out/<mnemonic>)")
    args = ap.parse_args()

    cfg = cfgmod.load(Path(args.config))
    if args.match:
        cfg["match"] = int(args.match, 16)
    if args.mask:
        cfg["mask"] = int(args.mask, 16)

    repo_root = Path(args.repo_root).expanduser().resolve()
    cfg = cfgmod.allocate_match_mask(cfg, repo_root)

    out_root = Path(args.out) if args.out else (SCRIPT_DIR / "out" / cfg["mnemonic"])
    snippets.write_all(cfg, out_root, TEMPLATE_DIR)

    new_cc = out_root / "new_files" / f"tree-ssa-{cfg['mnemonic']}.cc"
    print(f"\n  Generated patches in {out_root / 'patches'}")
    print(f"  Generated new file:   {new_cc}")
    print(f"  Resolved config:      {out_root / 'resolved_config.json'}")
    print(f"\n  Next: python3 03_apply.py {out_root} --repo-root {repo_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
