"""
snippets.py — Build the 11 patch records (10 in-place edits + 1 new file).
"""
from __future__ import annotations

import json
from pathlib import Path

from . import builders


# ── trivial {{KEY}} renderer ────────────────────────────────────────

def render(template: str, cfg: dict) -> str:
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
        "MARKER_FN": cfg.get("marker_fn", f"__custom_{cfg['mnemonic']}"),
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
    out = template
    for k, v in flat.items():
        out = out.replace("{{" + k + "}}", v)
    return out


# ── the 10 patch records ────────────────────────────────────────────

def build_patches(cfg: dict) -> list[dict]:
    M = cfg["mnemonic"]
    U = cfg["upper"]
    patches: list[dict] = []

    # 1a/1b. binutils/include/opcode/riscv-opc.h
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

    # 4a/4b. gcc/gcc/config/riscv/riscv.md
    patches.append({
        "id": "04a",
        "target_file": "gcc/gcc/config/riscv/riscv.md",
        "anchor": {"kind": "contains", "text": 'define_c_enum "unspec"', "which": "first"},
        "position": "below",
        "block": f"  UNSPEC_RISCV_{U}\n",
        "_note": "Inserted just inside the (define_c_enum \"unspec\" [ ... ]) list.",
    })
    patches.append({
        "id": "04b",
        "target_file": "gcc/gcc/config/riscv/riscv.md",
        "anchor": {"kind": "startswith", "text": '(define_insn "nop"', "which": "first"},
        "position": "above",
        "block": builders.build_riscv_md_snippet(cfg) + "\n",
    })

    # 5. gcc/gcc/internal-fn.def
    patches.append({
        "id": "05",
        "target_file": "gcc/gcc/internal-fn.def",
        "anchor": {"kind": "startswith", "text": "DEF_INTERNAL_FN (UBSAN_NULL", "which": "first"},
        "position": "above",
        "block": f"DEF_INTERNAL_FN ({cfg['ifn']}, ECF_NOTHROW, NULL)\n",
        "_note": "IFN ordering is by convention; UBSAN_NULL is just a stable landmark.",
    })

    # 6. gcc/gcc/internal-fn.cc
    patches.append({
        "id": "06",
        "target_file": "gcc/gcc/internal-fn.cc",
        "anchor": {"kind": "return_type_before",
                   "text": "expand_UBSAN_NULL", "which": "first"},
        "position": "above",
        "block": builders.build_internal_fn_expander(cfg) + "\n",
        "_note": "Anchor walks UP from 'expand_UBSAN_NULL' to its preceding "
                 "return-type line so the new function lands above the whole "
                 "UBSAN_NULL definition.",
    })

    # 7. gcc/gcc/config/riscv/riscv.cc
    patches.append({
        "id": "07",
        "target_file": "gcc/gcc/config/riscv/riscv.cc",
        "anchor": {"kind": "open_brace_after",
                   "text": "riscv_option_override (void)", "which": "first"},
        "position": "below",
        "block": (
            f"  /* {cfg['flag']} validation. */\n"
            f"  if ({cfg['target_macro']} && !TARGET_64BIT)\n"
            f"    warning (0, \"%<-{cfg['flag']}%> has only been validated on RV64; \"\n"
            f"             \"rv32 codegen is experimental\");\n"
        ),
        "_note": "Anchor finds the '{' that opens riscv_option_override's body; "
                 "works for both K&R and Allman brace styles.",
    })

    # 8. gcc/gcc/passes.def — insert AFTER POP_INSERT_PASSES of pass_graphite
    patches.append({
        "id": "08",
        "target_file": "gcc/gcc/passes.def",
        "anchor": {"kind": "contains", "text": "NEXT_PASS (pass_graphite)", "which": "first"},
        "position": "below_next_pop",
        "block": f"\t  NEXT_PASS (pass_recognize_{M});\n",
        "_note": "Insert AFTER the matching POP_INSERT_PASSES () that closes "
                 "the pass_graphite block, not immediately after pass_graphite.",
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


# ── new .cc file (file #11) ─────────────────────────────────────────

def build_new_cc_file(cfg: dict, template_dir: Path) -> str:
    """Render templates/tree_ssa_template.cc.tmpl with the per-pattern fragment."""
    skeleton = (template_dir / "tree_ssa_template.cc.tmpl").read_text()

    if cfg["pattern_kind"] == "arith_expr":
        frag_path = template_dir / "matcher_arith_expr.cc.frag"
    elif cfg["pattern_kind"] == "closed_form_loop":
        frag_path = template_dir / "matcher_closed_form_loop.cc.frag"
    elif cfg["pattern_kind"] == "marker":
        frag_path = template_dir / "matcher_marker.cc.frag"
    else:
        raise SystemExit(f"ERROR: unknown pattern_kind '{cfg['pattern_kind']}'")

    frag = frag_path.read_text()
    execute_body = f"try_recognize_{cfg['mnemonic']}_in_function (fun)"
    skeleton = skeleton.replace("{{MATCHER_BODY}}", frag)
    skeleton = skeleton.replace("{{EXECUTE_BODY}}", execute_body)
    return render(skeleton, cfg)


# ── orchestrator ────────────────────────────────────────────────────

def write_all(cfg: dict, out_root: Path, template_dir: Path) -> None:
    (out_root / "patches").mkdir(parents=True, exist_ok=True)
    (out_root / "new_files").mkdir(parents=True, exist_ok=True)
    (out_root / "resolved_config.json").write_text(json.dumps(cfg, indent=2))

    for p in build_patches(cfg):
        slug = Path(p["target_file"]).name.replace("/", "_")
        (out_root / "patches" / f"{p['id']}_{slug}.json").write_text(
            json.dumps(p, indent=2)
        )

    cc = build_new_cc_file(cfg, template_dir)
    (out_root / "new_files" / f"tree-ssa-{cfg['mnemonic']}.cc").write_text(cc)
