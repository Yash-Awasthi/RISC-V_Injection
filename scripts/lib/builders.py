"""
builders.py — Renders the per-instruction RTL/internal-fn snippets.

Two flavours are supported, controlled by cfg["rtl_kind"]:

  * "register" — classic ALU-style: rd is a destination register, all
                 inputs are register operands.  Use this for pure
                 arithmetic instructions (fds, nsum, fma, …).
  * "memory"   — accelerator-style: every operand is a pointer; the
                 instruction reads/writes memory of unknown size.
                 Use this for things like `attn` whose semantics span
                 whole arrays.  This is the form actually used by the
                 reference attn implementation in this repo.

Both forms emit `set_attr "type" "ghost"` because non-`ghost` types
trigger `riscv_sched_variable_issue` ICEs in the RISC-V scheduler
(see docs/05-troubleshooting.md, Issue 7).
"""
from __future__ import annotations


# ── riscv.md (define_insn block) ────────────────────────────────────

def build_riscv_md_snippet(cfg: dict) -> str:
    """Construct the `(define_insn …)` block for riscv.md."""
    n = cfg["num_inputs"]
    kind = cfg.get("rtl_kind", "register")
    upper = cfg["upper"]
    mnem = cfg["mnemonic"]
    target_macro = cfg["target_macro"]

    asm_args = ",".join(["%0"] + [f"%{i}" for i in range(1, n + 1)])

    if kind == "memory":
        # Accelerator form — every operand is a register-held pointer
        # and the instruction touches memory of unknown size.
        operand_decls = []
        unspec_inputs = []
        operand_decls.append('(mem:BLK (match_operand:DI 0 "register_operand" "r"))')
        for i in range(1, n + 1):
            decl = f'(mem:BLK (match_operand:DI {i} "register_operand" "r"))'
            unspec_inputs.append(decl)
        unspec_body = "\n           ".join(unspec_inputs) if unspec_inputs else ""
        return (
            f'(define_insn "riscv_{mnem}"\n'
            f'  [(set {operand_decls[0]}\n'
            f'        (unspec:BLK\n'
            f'          [{unspec_body}]\n'
            f'          UNSPEC_RISCV_{upper}))]\n'
            f'  "{target_macro}"\n'
            f'  "{mnem}\\t{asm_args}"\n'
            f'  [(set_attr "type" "ghost")\n'
            f'   (set_attr "mode" "DI")])\n'
        )

    # Default: register-style (rd <- unspec:DI [rs1, rs2, ...])
    ops_decl = ['(match_operand:DI 0 "register_operand" "=r")']
    ops_use = []
    for i in range(1, n + 1):
        ops_decl.append(f'(match_operand:DI {i} "register_operand" "r")')
        ops_use.append(f'(match_operand:DI {i})')
    unspec_body = ", ".join(ops_use) if ops_use else ""
    return (
        f'(define_insn "riscv_{mnem}"\n'
        f'  [(set {ops_decl[0]}\n'
        f'        (unspec:DI [{unspec_body}] UNSPEC_RISCV_{upper}))]\n'
        f'  "{target_macro}"\n'
        f'  "{mnem}\\t{asm_args}"\n'
        f'  [(set_attr "type" "ghost")\n'
        f'   (set_attr "mode" "DI")])\n'
    )


# ── internal-fn.cc expander ─────────────────────────────────────────

def build_internal_fn_expander(cfg: dict) -> str:
    """Build the static expand_RISCV_<UPPER> function for internal-fn.cc."""
    n = cfg["num_inputs"]
    kind = cfg.get("rtl_kind", "register")
    ifn = cfg["ifn"]
    mnem = cfg["mnemonic"]

    arg_lines = []
    for i in range(n):
        arg_lines.append(
            f"  rtx op{i+1} = expand_normal (gimple_call_arg (stmt, {i}));"
        )

    # For memory-style instructions, operands are pointers — force into
    # Pmode-sized regs.  For register-style, force them into a register
    # of their natural mode.
    if kind == "memory":
        force_lines = [f"  op{i+1} = force_reg (Pmode, op{i+1});" for i in range(n)]
        emit_args = ", ".join(["target"] + [f"op{i+1}" for i in range(n)])
        prologue = (
            "  tree lhs = gimple_call_lhs (stmt);\n"
            "  rtx target = lhs ? expand_normal (lhs) : NULL_RTX;\n"
            "  if (target) target = force_reg (Pmode, target);\n"
        )
    else:
        force_lines = [f"  op{i+1} = force_reg (DImode, op{i+1});" for i in range(n)]
        emit_args = ", ".join(["target"] + [f"op{i+1}" for i in range(n)])
        prologue = (
            "  tree lhs = gimple_call_lhs (stmt);\n"
            "  rtx target = lhs ? expand_normal (lhs) : NULL_RTX;\n"
        )

    body = "\n".join(arg_lines + force_lines)

    return (
        f"static void\n"
        f"expand_{ifn} (internal_fn, gcall *stmt)\n"
        f"{{\n"
        f"{prologue}"
        f"{body}\n"
        f"  emit_insn (gen_riscv_{mnem} ({emit_args}));\n"
        f"}}\n"
    )
