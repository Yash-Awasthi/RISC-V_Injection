#!/usr/bin/env python3
"""
customrv.py — End-to-end driver: C file → custom RISC-V instruction.

This is the "way 2" entry point.  Give it a .c file (or just a mnemonic
and a JSON config) and it walks the entire pipeline:

  1. Analyse the C source (or load the JSON config).
  2. Allocate a free MATCH/MASK from custom-0..custom-3.
  3. Emit the 11 patch artefacts under scripts/out/<mnemonic>/.
  4. Optionally apply them to a working riscv-gnu-toolchain tree.
  5. Optionally rebuild the toolchain and run the smoke + pattern tests.

For arbitrary instructions whose mathematics we do **not** simplify
(FMA / batch-norm / integration of sin x / GEMM / ...), the analyser
falls back to a "marker" pattern: the user calls
    extern long __custom_<mnemonic>(...);
in their C code, and the generated GIMPLE pass rewrites every such
call into IFN_RISCV_<UPPER>, which the RTL backend lowers to a single
machine instruction.  This keeps the project's deliberate constraint —
plain C in, custom RISC-V instruction out — for any operation the
user can express as a function call.

Usage examples:

  # Full auto from a C file (recommended):
  python3 customrv.py from-c examples/fma_demo.c --apply --build

  # Same, but only generate artefacts (no toolchain modification):
  python3 customrv.py from-c examples/batchnorm_demo.c

  # From a JSON config (the original "way 1"):
  python3 customrv.py from-config configs/fds.json --apply

  # Just preview what the patcher would do:
  python3 customrv.py from-config configs/nsum.json --apply --dry-run

  # Print the free opcode slots for an R4-type instruction:
  python3 customrv.py free-opcodes --inputs 3
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from lib import c_analyzer, config as cfgmod, opcodes, patcher, snippets  # noqa: E402

DEFAULT_REPO_ROOT = SCRIPT_DIR.parent
TEMPLATE_DIR = SCRIPT_DIR / "templates"


# ── shared helpers ─────────────────────────────────────────────────

def _generate(cfg: dict, repo_root: Path, out_root: Path | None) -> Path:
    cfg = cfgmod.derive_names(cfg)
    cfgmod.validate(cfg)
    cfg = cfgmod.allocate_match_mask(cfg, repo_root)
    out_root = out_root or (SCRIPT_DIR / "out" / cfg["mnemonic"])
    snippets.write_all(cfg, out_root, TEMPLATE_DIR)

    new_cc = out_root / "new_files" / f"tree-ssa-{cfg['mnemonic']}.cc"
    print(f"\n  Mnemonic:    {cfg['mnemonic']}")
    print(f"  MATCH/MASK:  0x{cfg['match']:08x} / 0x{cfg['mask']:08x}")
    print(f"  Pattern:     {cfg['pattern_kind']}"
          + (f" [{cfg['_strategy']}]" if "_strategy" in cfg else ""))
    print(f"  Patches:     {out_root / 'patches'}")
    print(f"  New file:    {new_cc}")
    print(f"  Resolved:    {out_root / 'resolved_config.json'}")

    # Auto-generate a tests/<mnemonic>.c for marker-style configs.
    if cfg["pattern_kind"] == "marker":
        test_c = SCRIPT_DIR / "tests" / f"{cfg['mnemonic']}.c"
        if not test_c.exists():
            c_analyzer.emit_marker_test_c(cfg, test_c)
            print(f"  Test C:      {test_c} (generated)")
    return out_root


def _apply(out_dir: Path, repo_root: Path, *, dry_run: bool, yes: bool,
           force: bool) -> int:
    print(f"\n  Repo root: {repo_root}")
    print(f"  Patch set: {out_dir}\n")
    patcher.check_versions(repo_root, force=force)

    cfg = json.loads((out_dir / "resolved_config.json").read_text())
    print(f"\n  Mnemonic: {cfg['mnemonic']}   "
          f"MATCH=0x{cfg['match']:08x}   MASK=0x{cfg['mask']:08x}   "
          f"pattern={cfg['pattern_kind']}\n")

    results: list[str] = []
    patch_files = sorted((out_dir / "patches").glob("*.json"), key=lambda p: p.name)
    for p in patch_files:
        patch = json.loads(p.read_text())
        results.append(patcher.apply_one(repo_root, patch,
                                         dry_run=dry_run, assume_yes=yes))
    for nf in (out_dir / "new_files").glob("*.cc"):
        results.append(patcher.install_new_file(repo_root, nf,
                                                dry_run=dry_run, assume_yes=yes))

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
    return 1 if (n_fail and not dry_run) else 0


def _build_and_test(mnemonic: str, repo_root: Path, install: Path) -> int:
    build = SCRIPT_DIR / "04_build.sh"
    test = SCRIPT_DIR / "05_test.sh"
    rc = subprocess.call(["bash", str(build), mnemonic, str(repo_root), str(install)])
    if rc != 0:
        return rc
    return subprocess.call(["bash", str(test), mnemonic, str(install)])


# ── subcommands ────────────────────────────────────────────────────

def cmd_free_opcodes(args) -> int:
    repo_root = Path(args.repo_root).expanduser().resolve()
    opc_h = opcodes.find_opc_h(repo_root)
    if not opc_h:
        sys.exit(f"ERROR: riscv-opc.h not found under {repo_root}")
    print(f"  riscv-opc.h: {opc_h}")
    existing = opcodes.parse_match_values_from_opc_h(opc_h)
    free = opcodes.find_free_slots(existing,
                                   num_inputs=args.inputs,
                                   limit=args.limit,
                                   preferred_slot=args.preferred_slot)
    if not free:
        sys.exit("  No free slots found.")
    print(f"\n  Free slots (num_inputs={args.inputs}):")
    for s in free:
        print(f"    {s['base']:<10} MATCH=0x{s['match']:08x}  "
              f"MASK=0x{s['mask']:08x}    {s['label']}")
    return 0


def cmd_from_config(args) -> int:
    repo_root = Path(args.repo_root).expanduser().resolve()
    cfg = json.loads(Path(args.config).read_text())
    if args.match:
        cfg["match"] = int(args.match, 16)
    if args.mask:
        cfg["mask"] = int(args.mask, 16)
    out_root = Path(args.out) if args.out else None
    out_root = _generate(cfg, repo_root, out_root)

    if args.apply:
        rc = _apply(out_root, repo_root, dry_run=args.dry_run,
                    yes=args.yes, force=args.force)
        if rc != 0:
            return rc

    if args.build and not args.dry_run:
        cfg2 = json.loads((out_root / "resolved_config.json").read_text())
        return _build_and_test(cfg2["mnemonic"], repo_root,
                               Path(args.install).expanduser().resolve())
    return 0


def cmd_from_c(args) -> int:
    repo_root = Path(args.repo_root).expanduser().resolve()
    c_file = Path(args.c_file).expanduser().resolve()
    if not c_file.exists():
        sys.exit(f"ERROR: {c_file} does not exist.")
    cfg = c_analyzer.analyze(c_file, mnemonic=args.mnemonic)
    print(f"  C analyser:  {cfg['_strategy']} → pattern_kind={cfg['pattern_kind']}, "
          f"mnemonic={cfg['mnemonic']}, num_inputs={cfg['num_inputs']}")

    out_root = Path(args.out) if args.out else None
    out_root = _generate(cfg, repo_root, out_root)

    # If the analyser produced a marker stub, copy the user's C file into
    # tests/ as well so 05_test.sh has something to compile.
    test_c = SCRIPT_DIR / "tests" / f"{cfg['mnemonic']}.c"
    if cfg.get("_strategy") in ("explicit_marker", "marker_fallback") \
            and not test_c.exists():
        shutil.copy2(c_file, test_c)
        print(f"  Test C:      {test_c} (copied from {c_file.name})")

    if args.apply:
        rc = _apply(out_root, repo_root, dry_run=args.dry_run,
                    yes=args.yes, force=args.force)
        if rc != 0:
            return rc

    if args.build and not args.dry_run:
        cfg2 = json.loads((out_root / "resolved_config.json").read_text())
        return _build_and_test(cfg2["mnemonic"], repo_root,
                               Path(args.install).expanduser().resolve())
    return 0


# ── argparse ───────────────────────────────────────────────────────

def _add_common(p):
    p.add_argument("--repo-root", default=str(DEFAULT_REPO_ROOT),
                   help="Path to riscv-gnu-toolchain root (default: %(default)s)")
    p.add_argument("--out", default=None,
                   help="Output dir for patches (default: scripts/out/<mnemonic>)")
    p.add_argument("--apply", action="store_true",
                   help="Also run the interactive patcher.")
    p.add_argument("--dry-run", action="store_true",
                   help="With --apply, only preview.")
    p.add_argument("--yes", action="store_true",
                   help="With --apply, accept all prompts.")
    p.add_argument("--force", action="store_true",
                   help="Skip GCC/binutils version check.")
    p.add_argument("--build", action="store_true",
                   help="After applying, run 04_build.sh + 05_test.sh.")
    p.add_argument("--install", default="$HOME/riscv-install",
                   help="Install prefix for --build (default: %(default)s)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    # free-opcodes
    p_free = sub.add_parser("free-opcodes",
                            help="Print free MATCH/MASK slots in custom-0..custom-3.")
    p_free.add_argument("--inputs", type=int, default=3)
    p_free.add_argument("--limit", type=int, default=4)
    p_free.add_argument("--preferred-slot", default="custom-0",
                        choices=list(opcodes.CUSTOM_SLOTS.keys()))
    p_free.add_argument("--repo-root", default=str(DEFAULT_REPO_ROOT))
    p_free.set_defaults(func=cmd_free_opcodes)

    # from-config (way 1)
    p_cfg = sub.add_parser("from-config",
                           help="Run the pipeline from a JSON config file.")
    p_cfg.add_argument("config")
    p_cfg.add_argument("--match", help="Override MATCH (hex)")
    p_cfg.add_argument("--mask",  help="Override MASK  (hex)")
    _add_common(p_cfg)
    p_cfg.set_defaults(func=cmd_from_config)

    # from-c (way 2)
    p_c = sub.add_parser("from-c",
                         help="Run the pipeline from a C source file.")
    p_c.add_argument("c_file", help="Path to the user's .c file.")
    p_c.add_argument("--mnemonic",
                     help="Override mnemonic (default: derive from file/function name)")
    _add_common(p_c)
    p_c.set_defaults(func=cmd_from_c)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
