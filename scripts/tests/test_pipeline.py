#!/usr/bin/env python3
"""
test_pipeline.py — Sanity tests for the customrv pipeline.

Run with:
  python3 scripts/tests/test_pipeline.py

These do NOT need a built RISC-V toolchain; they exercise the
generation + dry-run patcher against the source tree.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO  = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
sys.path.insert(0, str(SCRIPTS))

from lib import c_analyzer, config as cfgmod, opcodes, snippets  # noqa: E402

TEMPLATES = SCRIPTS / "templates"


def _say(msg: str, ok: bool) -> None:
    mark = "✔" if ok else "✘"
    print(f"  [{mark}] {msg}")


def _run(cmd: list[str]) -> tuple[int, str]:
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def test_opcode_finder() -> bool:
    print("\n[1] opcode finder")
    opc_h = opcodes.find_opc_h(REPO)
    ok = opc_h is not None
    _say(f"riscv-opc.h located ({opc_h})", ok)
    if not ok:
        return False
    existing = opcodes.parse_match_values_from_opc_h(opc_h)
    ok = "ATTN" in existing and existing["ATTN"] == 0x0b
    _say(f"existing[ATTN] == 0x0b ({len(existing)} entries total)", ok)
    free = opcodes.find_free_slots(existing, num_inputs=3, limit=2)
    ok = len(free) >= 1
    _say(f"found {len(free)} free R4-type slot(s) — first: {free[0] if free else None}", ok)
    return ok


def test_config_validation() -> bool:
    print("\n[2] config validation")
    fds = cfgmod.load(SCRIPTS / "configs" / "fds.json")
    ok = fds["mnemonic"] == "fds" and fds["pattern_kind"] == "arith_expr"
    _say("fds.json loads & validates", ok)
    ok2 = "RISCV_FDS" == fds["ifn"]
    _say(f"derived fields populated: ifn={fds['ifn']} flag={fds['flag']}", ok2)
    return ok and ok2


def test_arith_generation() -> bool:
    print("\n[3] arith_expr generation (fds)")
    cfg = cfgmod.load(SCRIPTS / "configs" / "fds.json")
    cfg = cfgmod.allocate_match_mask(cfg, REPO)
    out = Path(tempfile.mkdtemp(prefix="fds_"))
    snippets.write_all(cfg, out, TEMPLATES)
    new_cc = (out / "new_files" / "tree-ssa-fds.cc").read_text()
    ok = "try_recognize_fds_in_function" in new_cc and "MINUS_EXPR" in new_cc \
         and "RDIV_EXPR" in new_cc and "IFN_RISCV_FDS" in new_cc
    _say("tree-ssa-fds.cc contains expected matcher tokens", ok)
    n_patches = len(list((out / "patches").glob("*.json")))
    ok2 = n_patches == 12   # 10 records, but #1 and #4 are split into 1a/1b/4a/4b → 12 files
    _say(f"emitted {n_patches} patch JSONs", ok2)
    shutil.rmtree(out)
    return ok and ok2


def test_marker_generation() -> bool:
    print("\n[4] marker generation (bnorm, mem:BLK form)")
    cfg = c_analyzer.analyze(SCRIPTS / "examples" / "batchnorm_demo.c",
                             mnemonic="bnorm")
    ok = cfg["pattern_kind"] == "marker" and cfg["rtl_kind"] == "memory" \
         and cfg["num_inputs"] == 3
    _say(f"analyzer → pattern={cfg['pattern_kind']} rtl_kind={cfg['rtl_kind']} "
         f"num_inputs={cfg['num_inputs']}", ok)
    cfg = cfgmod.derive_names(cfg)
    cfg = cfgmod.allocate_match_mask(cfg, REPO)
    out = Path(tempfile.mkdtemp(prefix="bnorm_"))
    snippets.write_all(cfg, out, TEMPLATES)
    md_block = json.loads((out / "patches" / "04b_riscv.md.json").read_text())["block"]
    ok2 = "(mem:BLK" in md_block and "ghost" in md_block
    _say("riscv.md uses (mem:BLK ...) and type ghost", ok2)
    shutil.rmtree(out)
    return ok and ok2


def test_arith_autodetect() -> bool:
    print("\n[5] arith_expr autodetect from raw C")
    src = "long my_op(long a, long b, long c) { return a * b + c; }\n"
    tf = Path(tempfile.mktemp(suffix=".c"))
    tf.write_text(src)
    cfg = c_analyzer.analyze(tf)
    tf.unlink()
    ok = cfg["pattern_kind"] == "arith_expr" \
         and cfg["arith"]["outer_op"] == "PLUS_EXPR" \
         and cfg["arith"]["inner_op"] == "MULT_EXPR"
    _say(f"auto-detected MULT_EXPR + PLUS_EXPR (mnem={cfg['mnemonic']})", ok)
    return ok


def test_dry_run_apply() -> bool:
    print("\n[6] end-to-end dry-run via 03_apply.py")
    out = Path(tempfile.mkdtemp(prefix="t6_"))
    rc1, _ = _run(["python3", str(SCRIPTS / "02_generate.py"),
                   str(SCRIPTS / "configs" / "fds.json"),
                   "--repo-root", str(REPO),
                   "--out", str(out)])
    rc2, output = _run(["python3", str(SCRIPTS / "03_apply.py"),
                        str(out),
                        "--repo-root", str(REPO),
                        "--dry-run", "--yes"])
    ok = rc1 == 0 and rc2 == 0 and "OK=0  FAIL=0" in output and "DRY=13" in output
    _say(f"02_generate rc={rc1}, 03_apply --dry-run rc={rc2}, "
         f"all anchors resolved", ok)
    shutil.rmtree(out)
    return ok


def test_customrv_from_c() -> bool:
    print("\n[7] customrv.py from-c (way 2)")
    out = Path(tempfile.mkdtemp(prefix="t7_"))
    rc, output = _run(["python3", str(SCRIPTS / "customrv.py"),
                       "from-c", str(SCRIPTS / "examples" / "fma_demo.c"),
                       "--repo-root", str(REPO),
                       "--out", str(out)])
    ok = rc == 0 and "explicit_marker" in output and "MATCH/MASK" in output
    _say(f"customrv from-c rc={rc}, strategy detected", ok)
    shutil.rmtree(out)
    return ok


def main() -> int:
    print("═" * 60)
    print("  scripts/tests/test_pipeline.py")
    print("═" * 60)
    results = [
        test_opcode_finder(),
        test_config_validation(),
        test_arith_generation(),
        test_marker_generation(),
        test_arith_autodetect(),
        test_dry_run_apply(),
        test_customrv_from_c(),
    ]
    passed = sum(results)
    total = len(results)
    print("\n" + "═" * 60)
    print(f"  {passed}/{total} tests passed")
    print("═" * 60)
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
