#!/usr/bin/env python3
"""
02_generate_snippets.py — Emit the 11 patch records + new .cc file
==================================================================
Group 9 | RISC-V GNU Toolchain — generic custom-instruction generator

Reads a JSON config (e.g. configs/fds.json or configs/nsum.json),
optionally calls 01_identify_free_opcodes to allocate a free MATCH/MASK,
and emits:
  out/<mnemonic>/patches/01..10_*.json   (one anchor-based insert each)
  out/<mnemonic>/new_files/tree-ssa-<mnemonic>.cc

The patch JSON files are consumed by 03_apply_patches.py — they contain
no regex/sed; just (anchor_text, position, block) tuples that the
applier will resolve by line-by-line scan.

Usage:
  python3 02_generate_snippets.py configs/fds.json
  python3 02_generate_snippets.py configs/nsum.json --repo-root ~/riscv-gnu-toolchain
  python3 02_generate_snippets.py configs/fds.json --match 0x0200000b --mask 0xfe00707f
"""

import argparse
import importlib.util
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = SCRIPT_DIR / "templates"
DEFAULT_REPO_ROOT = SCRIPT_DIR.parent.parent  # custom_attn/scripts/ -> repo root


# ── helpers ─────────────────────────────────────────────────────────

def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def derive_names(cfg: dict) -> dict:
    """Fill in derived fields (upper, flag, target_macro, ifn, operand_string)."""
    m = cfg["mnemonic"]
    cfg.setdefault("upper", m.upper())
    cfg.setdefault("flag", "m" + m)
    cfg.setdefault("target_macro", "TARGET_" + m.upper())
    cfg.setdefault("ifn", "RISCV_" + m.upper())
    n = cfg["num_inputs"]
    if n == 3:
        cfg.setdefault("operand_string", "d,s,t,r")
        cfg.setdefault("operand_string_human", "rd, rs1, rs2, rs3")
    elif n == 2:
        cfg.setdefault("operand_string", "d,s,t")
        cfg.setdefault("operand_string_human", "rd, rs1, rs2")
    elif n == 1:
        cfg.setdefault("operand_string", "d,s")
        cfg.setdefault("operand_string_human", "rd, rs1")
    else:
        cfg.setdefault("operand_string", "d")
        cfg.setdefault("operand_string_human", "rd")
    return cfg


def allocate_match_mask(cfg: dict, repo_root: Path) -> dict:
    """If cfg.match/mask are null, run 01_identify_free_opcodes to pick one."""
    if cfg.get("match") is not None and cfg.get("mask") is not None:
        return cfg
    finder_path = SCRIPT_DIR / "01_identify_free_opcodes.py"
    if not finder_path.exists():
        sys.exit(f"ERROR: cannot auto-allocate MATCH/MASK — {finder_path} not found.\n"
                 f"       Either place 01_identify_free_opcodes.py next to this script,\n"
                 f"       or fill 'match' and 'mask' in your config explicitly.")
    finder = load_module(finder_path, "identify_free_opcodes")
    finder.REPO_ROOT = repo_root
    opc_h = finder.find_opc_h()
    if not opc_h:
        sys.exit("ERROR: riscv-opc.h not found — initialise the binutils submodule first.")
    existing = finder.parse_match_values_from_opc_h(opc_h)
    free = finder.find_free_slots(existing, num_inputs=cfg["num_inputs"], limit=1)
    if not free:
        sys.exit("ERROR: no free opcode slots in any custom-N base (this should never happen).")
    slot = free[0]
    cfg["match"] = slot["match"]
    cfg["mask"] = slot["mask"]
    cfg["_allocated_from"] = slot["base"]
    print(f"  Allocated free slot in {slot['base']}: "
          f"MATCH=0x{slot['match']:08x} MASK=0x{slot['mask']:08x}")
    return cfg


def render(template: str, cfg: dict) -> str:
    """Trivial {{KEY}} replacement; no Jinja dependency."""
    out = template
    flat = {
        "MNEMONIC": cfg["mnemonic"],
        "UPPER": cfg["upper"],
        "FLAG": cfg["flag"],
        "TARGET_MACRO": cfg["target_macro"],
        "IFN": cfg["ifn"],
        "INSN_CLASS": cfg["insn_class"],
        "OPERAND_STRING": cfg["operand_string"],
        "OPERAND_STRING_HUMAN": cfg["operand_string_human"],
        "NUM_INPUTS": str(cfg["num_inputs"]),
        "PATTERN_KIND": cfg["pattern_kind"],
        "MATCH": f"0x{cfg['match']:08x}",
        "MASK": f"0x{cfg['mask']:08x}",
    }
    if cfg["pattern_kind"] == "arith_expr":
        a = cfg["arith"]
        flat.update({
            "ARITH_OUTER_OP": a["outer_op"],
            "ARITH_INNER_OP": a["inner_op"],
            "ARITH_INNER_POS": str(a["inner_pos"]),
        })
    elif cfg["pattern_kind"] == "closed_form_loop":
        l = cfg["loop"]
        flat.update({
            "LOOP_REDUCTION_OP": l["reduction_op"],
            "LOOP_STEP_IS_IV": "true" if l["step_is_iv"] else "false",
        })
    for k, v in flat.items():
        out = out.replace("{{" + k + "}}", v)
    return out


# ── snippet builders for the 10 in-place patches ────────────────────

def build_riscv_md_snippet(cfg: dict) -> str:
    """Construct the (define_insn ...) block for riscv.md."""
    n = cfg["num_inputs"]
    # Build operand list and unspec body.
    # rd is always (match_operand:DI 0 ...), inputs are 1..n.
    ops_decl = ['(match_operand:DI 0 "register_operand" "=r")']
    ops_use  = []
    for i in range(1, n + 1):
        ops_decl.append(f'(match_operand:DI {i} "register_operand" "r")')
        ops_use.append(f'(match_operand:DI {i})')
    operands = "\n           ".join(ops_decl)
    unspec_body = "\n                       ".join(ops_use) if ops_use else ""
    asm_template_args = ",".join(["%0"] + [f"%{i}" for i in range(1, n + 1)])
    return (
        f'(define_insn "riscv_{cfg["mnemonic"]}"\n'
        f'  [(set {ops_decl[0]}\n'
        f'        (unspec:DI [{unspec_body}] UNSPEC_RISCV_{cfg["upper"]}))]\n'
        f'  "{cfg["target_macro"]}"\n'
        f'  "{cfg["mnemonic"]}\\t{asm_template_args}"\n'
        f'  [(set_attr "type" "arith")])\n'
    )


def build_internal_fn_expander(cfg: dict) -> str:
    """Build the static expand_RISCV_<UPPER> function for internal-fn.cc."""
    n = cfg["num_inputs"]
    arg_lines = []
    for i in range(n):
        arg_lines.append(
            f"  rtx op{i+1} = expand_normal (gimple_call_arg (stmt, {i}));"
        )
    args_for_emit = ", ".join(["target"] + [f"op{i+1}" for i in range(n)])
    args_str = "\n".join(arg_lines)
    return (
        f"static void\n"
        f"expand_{cfg['ifn']} (internal_fn, gcall *stmt)\n"
        f"{{\n"
        f"  tree lhs = gimple_call_lhs (stmt);\n"
        f"  rtx target = lhs ? expand_normal (lhs) : NULL_RTX;\n"
        f"{args_str}\n"
        f"  emit_insn (gen_riscv_{cfg['mnemonic']} ({args_for_emit}));\n"
        f"}}\n"
    )


# ── the 10 patch records ────────────────────────────────────────────

def build_patches(cfg: dict) -> list[dict]:
    M = cfg["mnemonic"]
    U = cfg["upper"]
    patches = []

    # 1. binutils/include/opcode/riscv-opc.h — TWO insertions
    patches.append({
        "id": "01a",
        "target_file": "binutils/include/opcode/riscv-opc.h",
        "anchor": {"kind": "startswith", "text": "#define MATCH_ADD ", "which": "first"},
        "position": "above",
        "block": (f"#define MATCH_{U} 0x{cfg['match']:08x}\n"
                  f"#define MASK_{U} 0x{cfg['mask']:08x}\n"),
    })
    patches.append({
        "id": "01b",
        "target_file": "binutils/include/opcode/riscv-opc.h",
        "anchor": {"kind": "startswith", "text": "DECLARE_INSN(add,", "which": "first"},
        "position": "above",
        "block": f"DECLARE_INSN({M}, MATCH_{U}, MASK_{U})\n",
    })

    # 2. binutils/opcodes/riscv-opc.c
    patches.append({
        "id": "02",
        "target_file": "binutils/opcodes/riscv-opc.c",
        "anchor": {"kind": "contains", "text": '{"unimp"', "which": "first"},
        "position": "above",
        "block": (f'{{"{M}", 0, {cfg["insn_class"]}, "{cfg["operand_string"]}", '
                  f'MATCH_{U}, MASK_{U}, match_opcode, 0 }},\n'),
    })

    # 3. gcc/gcc/config/riscv/riscv.opt — EOF append
    patches.append({
        "id": "03",
        "target_file": "gcc/gcc/config/riscv/riscv.opt",
        "anchor": {"kind": "eof", "text": "", "which": "first"},
        "position": "below",
        "block": (f"\n{cfg['flag']}\n"
                  f"Target Var({cfg['target_macro']}) Init(0)\n"
                  f"Enable the custom {M} instruction.\n"),
    })

    # 4. gcc/gcc/config/riscv/riscv.md — TWO insertions
    patches.append({
        "id": "04a",
        "target_file": "gcc/gcc/config/riscv/riscv.md",
        "anchor": {"kind": "contains", "text": 'define_c_enum "unspec"', "which": "first"},
        "position": "below",
        "block": f"  UNSPEC_RISCV_{U}\n",
        "_note": "Inserted just inside the (define_c_enum \"unspec\" [ ... ]) list. "
                 "If your file uses 'unspecv' or has multiple enums, verify the prompt context.",
    })
    patches.append({
        "id": "04b",
        "target_file": "gcc/gcc/config/riscv/riscv.md",
        "anchor": {"kind": "startswith", "text": '(define_insn "nop"', "which": "first"},
        "position": "above",
        "block": build_riscv_md_snippet(cfg) + "\n",
    })

    # 5. gcc/gcc/internal-fn.def
    patches.append({
        "id": "05",
        "target_file": "gcc/gcc/internal-fn.def",
        "anchor": {"kind": "startswith", "text": "DEF_INTERNAL_FN (UBSAN_NULL", "which": "first"},
        "position": "above",
        "block": f"DEF_INTERNAL_FN ({U}_RISCV_PLACEHOLDER, ECF_NOTHROW, NULL)\n".replace(
            f"{U}_RISCV_PLACEHOLDER", cfg["ifn"]),
        "_note": "Soft anchor — the IFN ordering is alphabetical by convention but not enforced. "
                 "Any DEF_INTERNAL_FN line will work; UBSAN_NULL is just a stable landmark.",
    })

    # 6. gcc/gcc/internal-fn.cc
    #    Anchor on the 'static void' line that PRECEDES expand_UBSAN_NULL
    #    so the new function block lands cleanly above the whole function,
    #    not between its return type and its name.
    patches.append({
        "id": "06",
        "target_file": "gcc/gcc/internal-fn.cc",
        "anchor": {"kind": "return_type_before",
                   "text": "expand_UBSAN_NULL",
                   "which": "first"},
        "position": "above",
        "block": build_internal_fn_expander(cfg) + "\n",
        "_note": "Anchor walks UP from 'expand_UBSAN_NULL' to the preceding "
                 "return-type line ('static void' / 'void' / etc.) so the "
                 "new function block lands above the whole UBSAN_NULL definition.",
    })

    # 7. gcc/gcc/config/riscv/riscv.cc
    #    Anchor on the open-brace of riscv_option_override so insertion
    #    lands INSIDE the function body regardless of K&R vs Allman style.
    patches.append({
        "id": "07",
        "target_file": "gcc/gcc/config/riscv/riscv.cc",
        "anchor": {"kind": "open_brace_after",
                   "text": "riscv_option_override (void)",
                   "which": "first"},
        "position": "below",
        "block": (
            f"  /* {cfg['flag']} validation. */\n"
            f"  if ({cfg['target_macro']} && !TARGET_64BIT)\n"
            f"    warning (0, \"%<-{cfg['flag']}%> has only been validated on RV64; \"\n"
            f"             \"rv32 codegen is experimental\");\n"
        ),
        "_note": "Anchor finds the '{' that opens riscv_option_override's body, "
                 "then inserts immediately below it — works for both K&R and "
                 "Allman brace styles.",
    })

    # 8. gcc/gcc/passes.def
    patches.append({
        "id": "08",
        "target_file": "gcc/gcc/passes.def",
        "anchor": {"kind": "contains", "text": "NEXT_PASS (pass_graphite)", "which": "first"},
        "position": "below_next_pop",
        "block": f"      NEXT_PASS (pass_recognize_{M});\n",
        "_note": "Special position: insert AFTER the matching POP_INSERT_PASSES () "
                 "that closes the pass_graphite block, not immediately after pass_graphite.",
    })

    # 9. gcc/gcc/tree-pass.h
    patches.append({
        "id": "09",
        "target_file": "gcc/gcc/tree-pass.h",
        "anchor": {"kind": "contains", "text": "make_pass_graphite ", "which": "first"},
        "position": "below",
        "block": f"extern gimple_opt_pass *make_pass_recognize_{M} (gcc::context *ctxt);\n",
    })

    # 10. gcc/gcc/Makefile.in
    patches.append({
        "id": "10",
        "target_file": "gcc/gcc/Makefile.in",
        "anchor": {"kind": "contains", "text": "tree-ssa-math-opts.o", "which": "first"},
        "position": "below",
        "block": f"\ttree-ssa-{M}.o \\\n",
    })

    return patches


# ── new file (file #11) ─────────────────────────────────────────────

def build_new_cc_file(cfg: dict) -> str:
    """Render templates/tree_ssa_template.cc.tmpl with the per-pattern fragment."""
    skeleton = (TEMPLATE_DIR / "tree_ssa_template.cc.tmpl").read_text()

    if cfg["pattern_kind"] == "arith_expr":
        frag = (TEMPLATE_DIR / "matcher_arith_expr.cc.frag").read_text()
        execute_body = f"try_recognize_{cfg['mnemonic']}_in_function (fun)"
    elif cfg["pattern_kind"] == "closed_form_loop":
        frag = (TEMPLATE_DIR / "matcher_closed_form_loop.cc.frag").read_text()
        execute_body = f"try_recognize_{cfg['mnemonic']}_in_function (fun)"
    else:
        sys.exit(f"ERROR: unknown pattern_kind '{cfg['pattern_kind']}'. "
                 f"Supported in v1: arith_expr, closed_form_loop.")

    skeleton = skeleton.replace("{{MATCHER_BODY}}", frag)
    skeleton = skeleton.replace("{{EXECUTE_BODY}}", execute_body)
    return render(skeleton, cfg)


# ── main ────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description="Generate 11 patch records for a custom RISC-V instruction.")
    ap.add_argument("config", help="Path to JSON config (e.g. configs/fds.json)")
    ap.add_argument("--repo-root", default=str(DEFAULT_REPO_ROOT),
                    help="Path to riscv-gnu-toolchain root (default: auto-detect)")
    ap.add_argument("--match", help="Override MATCH (hex, e.g. 0x0200000b)")
    ap.add_argument("--mask",  help="Override MASK  (hex, e.g. 0xfe00707f)")
    ap.add_argument("--out", default=None, help="Output dir (default: ./out/<mnemonic>)")
    args = ap.parse_args()

    cfg = json.loads(Path(args.config).read_text())
    if args.match: cfg["match"] = int(args.match, 16)
    if args.mask:  cfg["mask"]  = int(args.mask,  16)

    cfg = derive_names(cfg)
    cfg = allocate_match_mask(cfg, Path(args.repo_root).expanduser())

    out_root = Path(args.out) if args.out else (SCRIPT_DIR / "out" / cfg["mnemonic"])
    (out_root / "patches").mkdir(parents=True, exist_ok=True)
    (out_root / "new_files").mkdir(parents=True, exist_ok=True)

    # Snapshot resolved config
    (out_root / "resolved_config.json").write_text(json.dumps(cfg, indent=2))

    # Emit 10 patch JSONs
    for p in build_patches(cfg):
        (out_root / "patches" / f"{p['id']}_{Path(p['target_file']).name}.json").write_text(
            json.dumps(p, indent=2)
        )

    # Emit the new .cc file
    (out_root / "new_files" / f"tree-ssa-{cfg['mnemonic']}.cc").write_text(
        build_new_cc_file(cfg)
    )

    new_cc = out_root / 'new_files' / f"tree-ssa-{cfg['mnemonic']}.cc"
    print(f"\n  Generated patches in {out_root / 'patches'}")
    print(f"  Generated new file: {new_cc}")
    print(f"  Resolved config:   {out_root / 'resolved_config.json'}")
    print(f"\n  Next: python3 03_apply_patches.py {out_root} --repo-root {args.repo_root}")


if __name__ == "__main__":
    main()
