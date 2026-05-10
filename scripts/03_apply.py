#!/usr/bin/env python3
"""
03_apply.py — Anchor-based, interactive patch applier.

Reads the patch records produced by 02_generate.py and applies them
to a working riscv-gnu-toolchain tree.

  * NO grep / sed / regex search-and-replace.
  * For each patch: print (file:line) + 3 lines of context, ask y/N.
  * On ambiguous anchors (multiple matches), prompt the user to pick a
    specific candidate or enter an explicit line number.
    Exception: anchor.kind == "eof" never prompts.
  * Refuse to run unless GCC == 15.2.x and binutils == 2.46
    (override with --force).

Usage:
  python3 03_apply.py out/fds                 [--repo-root ...]
  python3 03_apply.py out/nsum --dry-run
  python3 03_apply.py out/fds --yes           (non-interactive)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
from lib import patcher  # noqa: E402

DEFAULT_REPO_ROOT = SCRIPT_DIR.parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir", help="Output dir from 02_generate.py (e.g. out/fds)")
    ap.add_argument("--repo-root", default=str(DEFAULT_REPO_ROOT))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--yes", action="store_true",
                    help="Apply all patches non-interactively.")
    ap.add_argument("--force", action="store_true",
                    help="Skip GCC/binutils version check (DANGEROUS).")
    args = ap.parse_args()

    repo_root = Path(args.repo_root).expanduser().resolve()
    out_dir   = Path(args.out_dir).expanduser().resolve()
    if not out_dir.exists():
        sys.exit(f"ERROR: {out_dir} does not exist (run 02_generate.py first).")

    print(f"\n  Repo root: {repo_root}")
    print(f"  Patch set: {out_dir}\n")
    patcher.check_versions(repo_root, force=args.force)

    cfg = json.loads((out_dir / "resolved_config.json").read_text())
    print(f"\n  Mnemonic: {cfg['mnemonic']}   "
          f"MATCH=0x{cfg['match']:08x}   MASK=0x{cfg['mask']:08x}   "
          f"pattern={cfg['pattern_kind']}\n")

    patch_dir = out_dir / "patches"
    patches = sorted(patch_dir.glob("*.json"), key=lambda p: p.name)

    results: list[str] = []
    for p in patches:
        patch = json.loads(p.read_text())
        results.append(patcher.apply_one(repo_root, patch,
                                         dry_run=args.dry_run,
                                         assume_yes=args.yes))

    new_files = list((out_dir / "new_files").glob("*.cc"))
    for nf in new_files:
        results.append(patcher.install_new_file(repo_root, nf,
                                                dry_run=args.dry_run,
                                                assume_yes=args.yes))

    print("\n" + "═" * 60)
    print("  SUMMARY")
    print("═" * 60)
    for r in results:
        print("  " + r)
    n_ok   = sum(1 for r in results if r.startswith("OK"))
    n_fail = sum(1 for r in results if r.startswith("FAIL"))
    n_skip = sum(1 for r in results if r.startswith("SKIP"))
    n_dry  = sum(1 for r in results if r.startswith("DRY"))
    print(f"\n  OK={n_ok}  FAIL={n_fail}  SKIP={n_skip}  DRY={n_dry}\n")
    if n_fail and not args.dry_run:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
