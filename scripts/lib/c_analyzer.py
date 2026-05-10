"""
c_analyzer.py — "Way 2": derive a config from a user-supplied C file.

The user gives us a .c file with a single function they want to be
fused into one custom RISC-V instruction.  We do **not** try to do
the maths.  Instead we apply a hierarchy of detection strategies, in
order:

  1. **Explicit marker** — if the source contains a call to a magic
     function like  __custom_<mnemonic>(args)  or just a single
     pragma comment  // @custom: <mnemonic>  → use pattern_kind="marker"
     and replace those marker calls with IFN_RISCV_<UPPER>.

  2. **arith_expr** — single-statement function of the form
        return (a OP1 b) OP2 c;
     We detect ((SLASH | STAR | PLUS | MINUS) on the inner pair and
     (PLUS | MINUS | STAR | DIV) on the outer pair.

  3. **closed_form_loop** — a single for-loop that accumulates the
     induction variable into a local, e.g.  acc += i;

  4. **fallback to marker** — for anything we can't classify (FMA,
     batchnorm, sin x integration, GEMM …) we automatically wrap the
     user's function so it issues a marker call to
        __custom_<mnemonic>(...)
     and the matcher fragment recognises THAT call rather than trying
     to understand the body.  The user gets a working
     `tests/<mnemonic>.c` and a single instruction emitted; the
     hardware semantics are left to the simulator (matches the
     project's existing "compiler emits the mnemonic, equivalence is
     future work" stance).

This keeps the deliberate constraint of the project intact: the user
writes plain C; the compiler emits one machine instruction.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

# ── tiny regex tokeniser (good enough for our shapes) ──────────────

_FUNC_RE = re.compile(
    r"""
    (?P<rettype>(?:[A-Za-z_][\w\s\*]*?))\s+        # return type
    (?P<name>[A-Za-z_]\w*)\s*                       # function name
    \(\s*(?P<args>[^)]*)\)\s*                       # args
    \{                                              # opening brace
    """,
    re.VERBOSE,
)

_MARKER_RE = re.compile(r"//\s*@custom:\s*(?P<mnem>[a-z_]\w*)\s*$", re.MULTILINE)
_MARKER_CALL_RE = re.compile(
    r"\b(?P<fn>__custom_[a-z_]\w*)\s*\(\s*(?P<args>[^)]*)\)"
)


def _strip_comments(src: str) -> str:
    """Strip C/C++ comments cheaply (good enough for shape detection)."""
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.DOTALL)
    src = re.sub(r"//[^\n]*", "", src)
    return src


def _find_first_function(src: str) -> Optional[dict]:
    src_nc = _strip_comments(src)
    m = _FUNC_RE.search(src_nc)
    if not m:
        return None
    # Walk balanced braces to find the body end
    start = m.end() - 1   # position of '{'
    depth = 0
    end = None
    for i in range(start, len(src_nc)):
        if src_nc[i] == "{":
            depth += 1
        elif src_nc[i] == "}":
            depth -= 1
            if depth == 0:
                end = i
                break
    if end is None:
        return None
    body = src_nc[start + 1:end]
    args = [a.strip() for a in m.group("args").split(",") if a.strip()]
    return {
        "rettype": m.group("rettype").strip(),
        "name": m.group("name"),
        "args": args,
        "body": body,
    }


# ── strategy 1: explicit marker ────────────────────────────────────

_DECL_KEYWORDS = ("extern", "static", "inline")


def _looks_like_decl(args_raw: str) -> bool:
    """True if args look like a C function-declaration (typed) parameter list."""
    # "long a, long b, long c" → has type names → declaration
    # "a, b, c"                → bare identifiers → call site
    # ""                       → could be either; treat as decl-ish (skip)
    if not args_raw.strip():
        return True
    for arg in args_raw.split(","):
        toks = arg.strip().split()
        if len(toks) >= 2:           # "<type> <name>"
            return True
        if any(toks[0] == kw for kw in ("void",)):
            return True
    return False


def detect_marker(src: str) -> Optional[dict]:
    """Look for a `__custom_<mnem>(...)` call site in the source.

    Comments are stripped first so doc-strings don't poison detection.
    Among matches, we prefer call sites (bare identifier args) over
    extern declarations (typed parameter lists).  We also infer
    rtl_kind by inspecting the declaration: if any parameter type is
    a pointer, the instruction touches memory and we generate a
    (mem:BLK ...) RTL pattern instead of a register pattern.
    """
    src_nc = _strip_comments(src)
    matches = list(_MARKER_CALL_RE.finditer(src_nc))
    if not matches:
        return None

    call_site = None
    decl = None
    for m in matches:
        if _looks_like_decl(m.group("args")):
            decl = decl or m
        else:
            call_site = call_site or m
    chosen = call_site or decl
    fn = chosen.group("fn")
    args = [a.strip() for a in chosen.group("args").split(",") if a.strip()]
    mnem = fn[len("__custom_"):]

    # Infer rtl_kind from the (preferably typed) declaration.
    rtl_kind = "register"
    if decl is not None and "*" in decl.group("args"):
        rtl_kind = "memory"

    return {
        "pattern_kind": "marker",
        "mnemonic": mnem,
        "marker_fn": fn,
        "num_inputs": min(3, len(args)),
        "rtl_kind": rtl_kind,
    }


# ── strategy 2: arith_expr (single return statement) ───────────────

_ARITH_OUTER_OPS = {
    "+": "PLUS_EXPR", "-": "MINUS_EXPR", "*": "MULT_EXPR", "/": "RDIV_EXPR",
}
_ARITH_INNER_OPS = _ARITH_OUTER_OPS

_RETURN_BIN_RE = re.compile(
    # Supports both `return (a op1 b) op2 c;` and `return a op1 b op2 c;`
    r"return\s*\(?\s*"
    r"(?P<a>[A-Za-z_]\w*)\s*(?P<op_in>[+\-*/])\s*(?P<b>[A-Za-z_]\w*)"
    r"\s*\)?\s*(?P<op_out>[+\-*/])\s*(?P<c>[A-Za-z_]\w*)\s*;",
)


def detect_arith_expr(fn: dict, mnemonic: str) -> Optional[dict]:
    if not fn:
        return None
    m = _RETURN_BIN_RE.search(fn["body"])
    if not m:
        return None
    return {
        "pattern_kind": "arith_expr",
        "mnemonic": mnemonic,
        "num_inputs": 3,
        "rtl_kind": "register",
        "arith": {
            "outer_op": _ARITH_OUTER_OPS[m.group("op_out")],
            "inner_op": _ARITH_INNER_OPS[m.group("op_in")],
            "inner_pos": 0,
        },
    }


# ── strategy 3: closed_form_loop ───────────────────────────────────

_LOOP_RE = re.compile(
    r"for\s*\(\s*\w[\w\s]*\s+(?P<iv>[A-Za-z_]\w*)\s*=\s*0\s*;"
    r"\s*\1\s*<\s*(?P<n>[A-Za-z_]\w*)\s*;\s*\+\+?\1\)?",
)
_REDUC_RE = re.compile(
    r"(?P<acc>[A-Za-z_]\w*)\s*\+=\s*(?P<rhs>[A-Za-z_]\w*)\s*;"
)


def detect_closed_form_loop(fn: dict, mnemonic: str) -> Optional[dict]:
    if not fn:
        return None
    if not _LOOP_RE.search(fn["body"]):
        return None
    if not _REDUC_RE.search(fn["body"]):
        return None
    return {
        "pattern_kind": "closed_form_loop",
        "mnemonic": mnemonic,
        "num_inputs": 1,
        "rtl_kind": "register",
        "loop": {
            "reduction_op": "PLUS_EXPR",
            "step_is_iv": True,
        },
    }


# ── strategy 4: fallback marker ────────────────────────────────────

def fallback_marker(fn: Optional[dict], mnemonic: str) -> dict:
    """Generate a marker-based config; the user's body stays as documentation.

    rtl_kind is inferred from the parameter types: any pointer parameter
    promotes the RTL pattern to memory-style (mem:BLK ...).
    """
    nargs = len(fn["args"]) if fn else 0
    nargs = max(0, min(3, nargs))
    has_ptr = bool(fn and any("*" in a for a in fn["args"]))
    return {
        "pattern_kind": "marker",
        "mnemonic": mnemonic,
        "marker_fn": f"__custom_{mnemonic}",
        "num_inputs": nargs,
        "rtl_kind": "memory" if has_ptr else "register",
    }


# ── public API ─────────────────────────────────────────────────────

def analyze(c_file: Path, mnemonic: Optional[str] = None) -> dict:
    """
    Return a partial config dict (caller will fill in match/mask & derived
    fields).  Always returns a usable config — falls back to "marker" if
    nothing more specific matches.
    """
    src = c_file.read_text()

    # 0. honour an explicit @custom: marker pragma
    pragma = _MARKER_RE.search(src)
    if pragma and not mnemonic:
        mnemonic = pragma.group("mnem")

    fn = _find_first_function(src)
    if not mnemonic:
        mnemonic = fn["name"] if fn else c_file.stem
    mnemonic = mnemonic.lower()

    # 1. explicit __custom_xxx() call wins; prefer the marker's mnemonic
    cfg = detect_marker(src)
    if cfg:
        # Honour the marker's mnemonic unless caller passed an override
        # via a non-default mnemonic argument.
        if mnemonic and mnemonic != (fn["name"] if fn else c_file.stem).lower():
            cfg["mnemonic"] = mnemonic
            cfg["marker_fn"] = f"__custom_{mnemonic}"
        cfg["_strategy"] = "explicit_marker"
        return cfg

    # 2. single-stmt arith expression
    cfg = detect_arith_expr(fn, mnemonic)
    if cfg:
        cfg["_strategy"] = "arith_expr_autodetect"
        return cfg

    # 3. iv-reduction loop
    cfg = detect_closed_form_loop(fn, mnemonic)
    if cfg:
        cfg["_strategy"] = "closed_form_loop_autodetect"
        return cfg

    # 4. fallback — generate a marker stub
    cfg = fallback_marker(fn, mnemonic)
    cfg["_strategy"] = "marker_fallback"
    return cfg


def emit_marker_test_c(cfg: dict, out: Path) -> Path:
    """Generate a `tests/<mnemonic>.c` that hits the marker matcher."""
    n = cfg["num_inputs"]
    fn = cfg.get("marker_fn", f"__custom_{cfg['mnemonic']}")
    arg_decls = ", ".join(f"long a{i+1}" for i in range(n)) or "void"
    arg_use = ", ".join(f"a{i+1}" for i in range(n))
    body = (
        f"/* tests/{cfg['mnemonic']}.c — marker-based pattern test.\n"
        f"   The matcher rewrites calls to {fn}() into IFN_{cfg.get('ifn', 'RISCV_'+cfg['mnemonic'].upper())},\n"
        f"   which the RISC-V backend lowers to a single `{cfg['mnemonic']}` instruction. */\n\n"
        f"extern long {fn}({arg_decls});\n\n"
        f"long {cfg['mnemonic']}_demo({arg_decls})\n"
        f"{{\n"
        f"    return {fn}({arg_use});\n"
        f"}}\n"
    )
    out.write_text(body)
    return out
