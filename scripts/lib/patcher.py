"""
patcher.py — Anchor-based, interactive patch applier.

Design rules (project requirements):
  * NO grep / sed / regex search-and-replace on file content.
  * For each patch: print (file:line) + 3 lines of context, ask y/N.
  * If the anchor is ambiguous (multiple candidate matches and the
    "which" key isn't strict enough), prompt the user for an explicit
    line number — UNLESS the anchor kind is `eof` or there is exactly
    one match (no ambiguity).
  * Refuse to run unless GCC == 15.2.x and binutils == 2.46 (override
    with --force).
  * Make a .bak before each in-place edit (idempotent: skip if already
    applied).
  * --dry-run: show what would happen without writing.
"""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

EXPECTED_GCC      = "15.2"
EXPECTED_BINUTILS = "2.46"


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
                 f"       Re-run with --force to bypass this check.")


# ── anchor resolution ──────────────────────────────────────────────

def _all_matches(lines: list[str], anchor: dict) -> list[int]:
    """Return ALL 1-based line numbers that match the anchor (best-effort)."""
    kind = anchor["kind"]
    text = anchor["text"]
    out: list[int] = []
    if kind == "eof":
        return [len(lines)]
    for i, line in enumerate(lines, start=1):
        stripped = line.lstrip()
        if kind == "startswith" and stripped.startswith(text):
            out.append(i)
        elif kind == "contains" and text in line:
            out.append(i)
        elif kind == "open_brace_after" and text in line:
            out.append(i)
        elif kind == "return_type_before" and text in line:
            out.append(i)
    return out


def _resolve_special(lines: list[str], anchor: dict, raw_line: int) -> int | None:
    """Apply the post-processing for `open_brace_after` / `return_type_before`."""
    kind = anchor["kind"]
    if kind == "open_brace_after":
        text = anchor["text"]
        line = lines[raw_line - 1]
        if "{" in line.split(text, 1)[1]:
            return raw_line
        for j in range(raw_line, len(lines)):
            s = lines[j].lstrip()
            if s.startswith("{"):
                return j + 1
        return None
    if kind == "return_type_before":
        # Walk UP to find a non-blank line that looks like a C return type.
        for j in range(raw_line - 2, -1, -1):
            s = lines[j].rstrip()
            if not s.strip():
                continue
            if any(c in s for c in "{};,"):
                return raw_line
            return j + 1
        return raw_line
    return raw_line


def find_anchor(lines: list[str], anchor: dict, *,
                interactive: bool = True,
                target_label: str = "") -> int | None:
    """
    Resolve a (possibly ambiguous) anchor to a single 1-based line number.

    If multiple raw matches exist and the anchor's `which` is not "first":
        - ambiguity → prompt the user to pick one.
    For `eof`, return len(lines).
    """
    raw = _all_matches(lines, anchor)
    if not raw:
        return None

    which = anchor.get("which", "first")
    if anchor["kind"] == "eof" or len(raw) == 1 or which == "first":
        return _resolve_special(lines, anchor, raw[0])

    # Ambiguity — prompt
    if not interactive:
        return _resolve_special(lines, anchor, raw[0])

    print(f"\n  ⚠  Ambiguous anchor for {target_label} — {len(raw)} candidates:")
    for n, ln in enumerate(raw, start=1):
        print(f"     [{n}] line {ln:5d} | {lines[ln - 1].rstrip()[:80]}")
    while True:
        ans = input(f"  Pick candidate [1-{len(raw)}] or enter explicit line "
                    f"number: ").strip()
        if ans.isdigit():
            v = int(ans)
            if 1 <= v <= len(raw):
                return _resolve_special(lines, anchor, raw[v - 1])
            if 1 <= v <= len(lines):
                return v
        print("  Invalid choice. Try again.")


def find_pop_after(lines: list[str], from_line: int) -> int | None:
    for i in range(from_line, len(lines) + 1):
        if "POP_INSERT_PASSES" in lines[i - 1] and "(" in lines[i - 1]:
            return i
    return None


def show_context(target: Path, lines: list[str], lineno: int, label: str,
                 *, span: int = 1) -> None:
    print(f"\n  ─── {label} → {target}:{lineno} ───")
    lo = max(1, lineno - span)
    hi = min(len(lines), lineno + span)
    for i in range(lo, hi + 1):
        marker = ">>" if i == lineno else "  "
        print(f"  {marker} {i:5d} | {lines[i - 1].rstrip()}")


# ── idempotency: skip if already applied ──────────────────────────

def already_applied(lines: list[str], block: str) -> bool:
    """Skip if a *distinctive* line of `block` is already present."""
    candidates = []
    for bl in block.splitlines():
        s = bl.strip()
        if len(s) < 12:
            continue
        if any(tok in s for tok in ("MATCH_", "MASK_", "DECLARE_INSN(",
                                    "IFN_RISCV_", "RISCV_", "riscv_",
                                    "recognize_", "TARGET_", "UNSPEC_RISCV_")):
            candidates.append(s)
    if not candidates:
        return False
    return all(any(c in L for L in lines) for c in candidates[:2])


# ── apply one patch ────────────────────────────────────────────────

def apply_one(repo_root: Path, patch: dict, *,
              dry_run: bool, assume_yes: bool) -> str:
    target = repo_root / patch["target_file"]
    if not target.exists():
        return f"SKIP  {patch['id']:>4}  missing target: {target}"

    lines = target.read_text().splitlines(keepends=True)

    if already_applied(lines, patch["block"]):
        return f"SKIP  {patch['id']:>4}  already applied in {target.name}"

    anchor_line = find_anchor(lines, patch["anchor"],
                              interactive=not assume_yes,
                              target_label=f"{target.name} (patch {patch['id']})")
    if anchor_line is None:
        return f"FAIL  {patch['id']:>4}  anchor not found in {target.name}: {patch['anchor']}"

    position = patch["position"]
    if position == "below_next_pop":
        pop_line = find_pop_after(lines, anchor_line)
        if pop_line is None:
            return f"FAIL  {patch['id']:>4}  no POP_INSERT_PASSES below anchor in {target.name}"
        insert_at = pop_line
        position = "below"
        show_context(target, lines, anchor_line, f"patch {patch['id']} anchor")
        show_context(target, lines, pop_line, f"patch {patch['id']} POP target")
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

    if position == "above":
        idx = insert_at - 1
    elif position == "below":
        idx = insert_at
    else:
        return f"FAIL  {patch['id']:>4}  unknown position '{position}'"

    block = patch["block"]
    if not block.endswith("\n"):
        block += "\n"

    bak = target.with_suffix(target.suffix + ".bak")
    if not bak.exists():
        shutil.copy2(target, bak)

    new_lines = lines[:idx] + [block] + lines[idx:]
    target.write_text("".join(new_lines))
    return f"OK    {patch['id']:>4}  inserted into {target.name} at line {insert_at}"


def install_new_file(repo_root: Path, src: Path, *,
                     dry_run: bool, assume_yes: bool) -> str:
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
