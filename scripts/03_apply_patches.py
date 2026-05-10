#!/usr/bin/env python3
"""
03_apply_patches.py — Anchor-based, interactive patch applier
=============================================================
Group 9 | RISC-V GNU Toolchain

Reads the patch records produced by 02_generate_snippets.py and applies
them to a working riscv-gnu-toolchain tree.

Design rules (from project requirements):
  * NO grep / sed / regex search-and-replace. Pure line-by-line scan.
  * For each patch: print (file:line) + 3 lines of context, ask y/N.
  * Refuse to run unless GCC == 15.2.x and binutils == 2.46.
  * Make a .bak before each in-place edit (idempotent: skip if .bak exists
    AND the target text is already present).
  * --dry-run: show what would happen without writing.

Usage:
  python3 03_apply_patches.py out/fds                 [--repo-root ...]
  python3 03_apply_patches.py out/nsum --dry-run
  python3 03_apply_patches.py out/fds --yes           (non-interactive, dangerous)
"""

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_REPO_ROOT = SCRIPT_DIR.parent.parent

EXPECTED_GCC      = "15.2"      # prefix-match against gcc/gcc/BASE-VER
EXPECTED_BINUTILS = "2.46"      # prefix-match against binutils/bfd/version.m4


# ── version gate ────────────────────────────────────────────────────

def check_versions(repo_root: Path, force: bool) -> None:
    gcc_ver_file = repo_root / "gcc" / "gcc" / "BASE-VER"
    bu_ver_file  = repo_root / "binutils" / "bfd" / "version.m4"

    gcc_ver = gcc_ver_file.read_text().strip() if gcc_ver_file.exists() else "?"
    bu_ver_raw = bu_ver_file.read_text() if bu_ver_file.exists() else ""
    m = re.search(r"\[([\d.]+)\]", bu_ver_raw)
    bu_ver = m.group(1) if m else "?"

    print(f"  GCC version (gcc/gcc/BASE-VER):       {gcc_ver}")
    print(f"  Binutils version (bfd/version.m4):    {bu_ver}")

    ok = gcc_ver.startswith(EXPECTED_GCC) and bu_ver.startswith(EXPECTED_BINUTILS)
    if not ok and not force:
        sys.exit(f"\nERROR: expected GCC {EXPECTED_GCC}.x and binutils {EXPECTED_BINUTILS}.x.\n"
                 f"       Got GCC={gcc_ver}, binutils={bu_ver}.\n"
                 f"       Re-run with --force to bypass this check (anchors may not match).")


# ── anchor resolution (no regex on the file content) ────────────────

def find_anchor(lines: list[str], anchor: dict) -> int | None:
    """
    Returns 1-based line number of the first matching anchor, or None.
    anchor kinds:
      startswith       — first line whose lstrip() startswith text
      contains         — first line that contains text
      eof              — end-of-file (used with position='below' to append)
      open_brace_after — find first line containing text, then walk down
                         until we see a line that is exactly '{' (or starts
                         with '{'); return that brace line.  Used for
                         function-body insertions that must work for both
                         K&R 'foo() {' and Allman 'foo()\n{' styles.
    """
    kind = anchor["kind"]
    text = anchor["text"]
    if kind == "eof":
        return len(lines)
    for i, line in enumerate(lines, start=1):
        stripped = line.lstrip()
        if kind == "startswith" and stripped.startswith(text):
            return i
        if kind == "contains" and text in line:
            if kind == "contains":
                return i
        if kind == "open_brace_after" and text in line:
            # If '{' is on the same line, return this line.
            if "{" in line.split(text, 1)[1]:
                return i
            # Otherwise walk down looking for the opening brace.
            for j in range(i, len(lines)):
                s = lines[j].lstrip()
                if s.startswith("{"):
                    return j + 1   # 1-based
            return None
        if kind == "return_type_before" and text in line:
            # Walk UP to find a non-blank line that looks like a C return type
            # (ends with whitespace + nothing, no '{', '(', ';', or ',').
            for j in range(i - 2, -1, -1):    # j is 0-based
                s = lines[j].rstrip()
                if not s.strip():
                    continue
                if any(c in s for c in "{};,"):
                    # Crossed a statement/decl boundary; give up & fall back
                    # to the line itself.
                    return i
                # First non-blank, non-boundary line above is the return type.
                return j + 1   # 1-based
            return i
    return None


def find_pop_after(lines: list[str], from_line: int) -> int | None:
    """
    For passes.def position='below_next_pop': starting from from_line (1-based),
    walk DOWN until we hit the line containing 'POP_INSERT_PASSES ()' (or
    'POP_INSERT_PASSES()'); return that line number.
    """
    for i in range(from_line, len(lines) + 1):
        if "POP_INSERT_PASSES" in lines[i - 1] and "(" in lines[i - 1]:
            return i
    return None


def show_context(target: Path, lines: list[str], lineno: int, label: str) -> None:
    print(f"\n  ─── {label} → {target}:{lineno} ───")
    lo = max(1, lineno - 1)
    hi = min(len(lines), lineno + 1)
    for i in range(lo, hi + 1):
        marker = ">>" if i == lineno else "  "
        print(f"  {marker} {i:5d} | {lines[i - 1].rstrip()}")


# ── apply one patch ─────────────────────────────────────────────────

def already_applied(lines: list[str], block: str) -> bool:
    """Skip if a *distinctive* line of `block` is already present.
    We require a line that is at least 12 chars after stripping AND contains
    a token uniquely identifying the patch (mnemonic-bearing tokens).
    Conservative: when in doubt, return False and let the user confirm."""
    candidates = []
    for bl in block.splitlines():
        s = bl.strip()
        if len(s) < 12:
            continue
        # Prefer lines that look mnemonic-specific.
        if any(tok in s for tok in ("MATCH_", "MASK_", "DECLARE_INSN(",
                                    "IFN_RISCV_", "RISCV_", "riscv_",
                                    "recognize_", "TARGET_")):
            candidates.append(s)
    if not candidates:
        return False
    # Require ALL candidates to be present somewhere — much stricter.
    return all(any(c in L for L in lines) for c in candidates[:2])


def apply_one(repo_root: Path, patch: dict, *, dry_run: bool, assume_yes: bool) -> str:
    target = repo_root / patch["target_file"]
    if not target.exists():
        return f"SKIP  {patch['id']:>4}  missing target: {target}"

    lines = target.read_text().splitlines(keepends=True)

    if already_applied(lines, patch["block"]):
        return f"SKIP  {patch['id']:>4}  already applied in {target.name}"

    anchor_line = find_anchor(lines, patch["anchor"])
    if anchor_line is None:
        return f"FAIL  {patch['id']:>4}  anchor not found in {target.name}: {patch['anchor']}"

    position = patch["position"]
    if position == "below_next_pop":
        pop_line = find_pop_after(lines, anchor_line)
        if pop_line is None:
            return f"FAIL  {patch['id']:>4}  no POP_INSERT_PASSES below anchor in {target.name}"
        insert_at = pop_line  # insert AFTER this line
        position = "below"
        show_context(target, lines, anchor_line, f"patch {patch['id']} anchor")
        show_context(target, lines, pop_line,    f"patch {patch['id']} POP target")
    else:
        show_context(target, lines, anchor_line, f"patch {patch['id']} anchor")
        insert_at = anchor_line

    print(f"  position: {position}    block ({len(patch['block'].splitlines())} line(s)):")
    for bl in patch["block"].splitlines():
        print(f"    + {bl}")
    if patch.get("_note"):
        print(f"  note: {patch['_note']}")

    if dry_run:
        return f"DRY   {patch['id']:>4}  would insert at {target.name}:{insert_at}"

    if not assume_yes:
        ans = input("  Apply this patch? [y/N] ").strip().lower()
        if ans != "y":
            return f"SKIP  {patch['id']:>4}  user declined"

    # Compute insertion index (0-based)
    if position == "above":
        idx = insert_at - 1
    elif position == "below":
        idx = insert_at         # after the matched line
    else:
        return f"FAIL  {patch['id']:>4}  unknown position '{position}'"

    block = patch["block"]
    if not block.endswith("\n"):
        block += "\n"

    # Backup once
    bak = target.with_suffix(target.suffix + ".bak")
    if not bak.exists():
        shutil.copy2(target, bak)

    new_lines = lines[:idx] + [block] + lines[idx:]
    target.write_text("".join(new_lines))
    return f"OK    {patch['id']:>4}  inserted into {target.name} at line {insert_at}"


# ── new-file copy (file #11) ────────────────────────────────────────

def install_new_file(repo_root: Path, src: Path, *, dry_run: bool, assume_yes: bool) -> str:
    dest = repo_root / "gcc" / "gcc" / src.name
    print(f"\n  ─── new file → {dest} ───")
    print(f"  source: {src} ({src.stat().st_size} bytes)")
    if dest.exists():
        print(f"  (target already exists; will overwrite after .bak)")
    if dry_run:
        return f"DRY   new   would copy {src.name} -> {dest}"
    if not assume_yes:
        ans = input("  Install new file? [y/N] ").strip().lower()
        if ans != "y":
            return f"SKIP  new   user declined"
    if dest.exists():
        bak = dest.with_suffix(dest.suffix + ".bak")
        if not bak.exists():
            shutil.copy2(dest, bak)
    shutil.copy2(src, dest)
    return f"OK    new   installed {dest}"


# ── main ────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir", help="Output dir from 02_generate_snippets.py (e.g. out/fds)")
    ap.add_argument("--repo-root", default=str(DEFAULT_REPO_ROOT))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--yes", action="store_true",
                    help="Apply all patches without per-file prompt (still prints context).")
    ap.add_argument("--force", action="store_true",
                    help="Skip GCC/binutils version check (DANGEROUS).")
    args = ap.parse_args()

    repo_root = Path(args.repo_root).expanduser().resolve()
    out_dir   = Path(args.out_dir).expanduser().resolve()
    if not out_dir.exists():
        sys.exit(f"ERROR: {out_dir} does not exist (run 02_generate_snippets.py first).")

    print(f"\n  Repo root: {repo_root}")
    print(f"  Patch set: {out_dir}\n")
    check_versions(repo_root, force=args.force)

    cfg = json.loads((out_dir / "resolved_config.json").read_text())
    print(f"\n  Mnemonic: {cfg['mnemonic']}   "
          f"MATCH=0x{cfg['match']:08x}   MASK=0x{cfg['mask']:08x}   "
          f"pattern={cfg['pattern_kind']}\n")

    patch_dir = out_dir / "patches"
    patches = sorted(patch_dir.glob("*.json"), key=lambda p: p.name)

    results = []
    for p in patches:
        patch = json.loads(p.read_text())
        results.append(apply_one(repo_root, patch,
                                 dry_run=args.dry_run, assume_yes=args.yes))

    # Install the new .cc file
    new_files = list((out_dir / "new_files").glob("*.cc"))
    for nf in new_files:
        results.append(install_new_file(repo_root, nf,
                                        dry_run=args.dry_run, assume_yes=args.yes))

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
        sys.exit(1)


if __name__ == "__main__":
    main()
