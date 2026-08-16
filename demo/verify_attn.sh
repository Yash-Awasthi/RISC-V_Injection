#!/bin/bash
# ================================================================
# verify_attn.sh — Complete verification of custom attn instruction
#
# Author  : Yash Awasthi
# Compiler: riscv64-unknown-elf-gcc (GCC 15.2.0, modified)
#
# Two independent surfaces are gated by two separate flags:
#   -mattn              makes the `attn` instruction and the explicit
#                        __builtin_riscv_attn() builtin available.
#                        This is the primary, non-experimental path.
#   -mattn -mattn-recognize
#                        additionally turns on the attnrec idiom
#                        recognizer, which tries to spot SDPA written
#                        as plain loops and rewrite it to `attn` with
#                        no source change. Experimental, off by
#                        default even when -mattn is given alone.
#
# Runs all verification checks and prints a summary.
# Run from the repository root, or from demo/ with a bare filename.
#
# Usage:
#   chmod +x verify_attn.sh
#   ./verify_attn.sh demo/sdpa_builtin.c      # from repo root
#   ./verify_attn.sh sdpa_builtin.c           # from demo/
# ================================================================

GCC="$HOME/riscv-install/bin/riscv64-unknown-elf-gcc"
AS="$HOME/riscv-install/bin/riscv64-unknown-elf-as"
OBJDUMP="$HOME/riscv-install/bin/riscv64-unknown-elf-objdump"
SRC="${1:-sdpa_builtin.c}"
PASS=0
FAIL=0

# Fixed reference files for the contract checks (5, 6, 9) below — these
# always test the same two known-good sources regardless of $SRC, the
# same way the original negative test always used its own axpy source.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SDPA_BUILTIN="$SCRIPT_DIR/sdpa_builtin.c"
SDPA_TEST="$SCRIPT_DIR/sdpa_test.c"

header() { echo ""; echo "════════════════════════════════════════════════════"; echo "  $1"; echo "════════════════════════════════════════════════════"; }
ok()     { echo "  [PASS] $1"; PASS=$((PASS+1)); }
fail()   { echo "  [FAIL] $1"; FAIL=$((FAIL+1)); }

# ── Check 1: Compiler version ────────────────────────────────────
header "1. COMPILER VERSION"
$GCC --version
VER=$($GCC --version | head -1)
echo "  $VER"
[[ "$VER" == *"15.2.0"* ]] && ok "GCC 15.2.0 confirmed" || fail "Unexpected version"

# ── Check 2: Source file exists ──────────────────────────────────
header "2. SOURCE FILE"
if [ -f "$SRC" ]; then
    ok "Found $SRC ($(wc -l < $SRC) lines)"
else
    fail "Source file $SRC not found"
    echo "  Usage: $0 sdpa_builtin.c"
    exit 1
fi

# ── Check 3: -mattn flag accepted ────────────────────────────────
header "3. -mattn FLAG ACCEPTED"
echo 'int main(){return 0;}' > /tmp/attn_flag_test.c
$GCC -mattn -O2 /tmp/attn_flag_test.c -o /tmp/attn_flag_test.elf 2>/dev/null \
    && ok "-mattn flag accepted by compiler" \
    || fail "-mattn flag rejected — check riscv.opt"

# ── Check 4: Assembler recognizes attn ───────────────────────────
header "4. ASSEMBLER ENCODING"
cat > /tmp/attn_asm.S << 'EOF'
    .text
    .globl _start
_start:
    attn a3,a0,a1,a2
EOF
$AS /tmp/attn_asm.S -o /tmp/attn_asm.o 2>/dev/null
if [ $? -eq 0 ]; then
    ok "attn assembled without error"
    echo ""
    echo "  Disassembly:"
    $OBJDUMP -d /tmp/attn_asm.o
    ENCODING=$($OBJDUMP -d /tmp/attn_asm.o | grep attn | awk '{print $2}')
    echo ""
    echo "  Encoding: 0x$ENCODING"
    [[ "$ENCODING" == "60b5068b" ]] \
        && ok "Binary encoding 0x60b5068b correct" \
        || fail "Unexpected encoding: 0x$ENCODING (expected 0x60b5068b)"
else
    fail "Assembly failed — check riscv-opc.h and riscv-opc.c"
fi

# ── Check 5: Builtin emits attn (primary path) ───────────────────
header "5. BUILTIN EMITS attn — PRIMARY PATH"
if [ -f "$SDPA_BUILTIN" ]; then
    $GCC -mattn -O2 -S "$SDPA_BUILTIN" -o /tmp/sdpa_builtin_test.s 2>/dev/null
    if grep -q '\battn\b' /tmp/sdpa_builtin_test.s 2>/dev/null; then
        ok "__builtin_riscv_attn emits attn under plain -mattn"
    else
        fail "builtin call did not produce attn — check AVAIL(attn, TARGET_ATTN) in riscv-builtins.cc and riscv.md"
    fi
else
    fail "reference file not found: $SDPA_BUILTIN"
fi

# ── Check 6: Builtin without -mattn is a compile error ───────────
header "6. BUILTIN WITHOUT -mattn ERRORS"
if [ -f "$SDPA_BUILTIN" ]; then
    $GCC -O2 -S "$SDPA_BUILTIN" -o /tmp/sdpa_builtin_nomattn.s \
        2>/tmp/sdpa_builtin_nomattn.err
    RC=$?
    if [ $RC -ne 0 ] && grep -qi 'attn' /tmp/sdpa_builtin_nomattn.err; then
        ok "compile fails without -mattn (exit $RC) — builtin correctly ungated"
        echo "  $(grep -i 'attn' /tmp/sdpa_builtin_nomattn.err | head -1)"
    else
        fail "builtin call did not error without -mattn — AVAIL(attn, TARGET_ATTN) gate is broken"
    fi
else
    fail "reference file not found: $SDPA_BUILTIN"
fi

# ── Check 7: attn instruction from the recognizer, both flags ────
header "7. CUSTOM INSTRUCTION IN ASSEMBLY OUTPUT — RECOGNIZER"
$GCC -mattn -mattn-recognize -O2 \
    -fdump-tree-attnrec-details \
    -S "$SDPA_TEST" -o /tmp/sdpa_test_out.s 2>/dev/null

if grep -q '\battn\b' /tmp/sdpa_test_out.s 2>/dev/null; then
    ok "attn instruction found in assembly"
    echo ""
    echo "  Instruction line:"
    grep -n '\battn\b' /tmp/sdpa_test_out.s
    echo ""
    echo "  Context (5 lines around attn):"
    grep -n -B 3 -A 3 '\battn\b' /tmp/sdpa_test_out.s
else
    fail "attn not found in assembly — matcher did not fire"
fi

# ── Check 8: GIMPLE dump confirms replacement ─────────────────────
header "8. GIMPLE DUMP — attnrec PASS INTERNALS"
DUMP=$(ls ./sdpa_test.c.*attnrec* 2>/dev/null | head -1)
[ -z "$DUMP" ] && DUMP=$(ls "$SCRIPT_DIR"/sdpa_test.c.*attnrec* 2>/dev/null | head -1)
[ -z "$DUMP" ] && DUMP=$(ls /tmp/sdpa_test.c.*attnrec* 2>/dev/null | head -1)
[ -z "$DUMP" ] && DUMP=$(ls /tmp/*.attnrec* 2>/dev/null | head -1)

if [ -n "$DUMP" ] && [ -f "$DUMP" ]; then
    ok "attnrec dump file found: $DUMP"
    echo ""
    cat "$DUMP" | grep -E 'replaced loop|rejected|bases found|RISCV_ATTN' | head -20
else
    echo "  [INFO] No dump file found next to the source or in /tmp."
    echo "  GCC writes -fdump-tree dumps into the current directory by"
    echo "  default; re-run from demo/ or pass -fdump-dir=/tmp/ explicitly:"
    echo "  $GCC -mattn -mattn-recognize -O2 -fdump-tree-attnrec-details -c $SDPA_TEST"
fi

# ── Check 9: -mattn alone must NOT transform a loop;              ─
# ──          -mattn -mattn-recognize must.                       ─
header "9. GATE — -mattn ALONE MUST NOT RECOGNIZE; -mattn -mattn-recognize MUST"
if [ -f "$SDPA_TEST" ]; then
    $GCC -mattn -O2 -S "$SDPA_TEST" -o /tmp/sdpa_test_mattn_only.s 2>/dev/null
    COUNT_ONLY=$(grep -c '\battn\b' /tmp/sdpa_test_mattn_only.s 2>/dev/null || echo 0)
    [ "$COUNT_ONLY" -eq 0 ] \
        && ok "-mattn alone: attnrec did not fire on sdpa_test.c (0 attn emitted)" \
        || fail "-mattn alone transformed a loop — TARGET_ATTN_RECOGNIZE gate is broken"

    $GCC -mattn -mattn-recognize -O2 -S "$SDPA_TEST" \
        -o /tmp/sdpa_test_recognize.s 2>/dev/null
    COUNT_BOTH=$(grep -c '\battn\b' /tmp/sdpa_test_recognize.s 2>/dev/null || echo 0)
    [ "$COUNT_BOTH" -gt 0 ] \
        && ok "-mattn -mattn-recognize: attnrec transformed sdpa_test.c ($COUNT_BOTH occurrence(s))" \
        || fail "-mattn -mattn-recognize did not transform sdpa_test.c — matcher or gate broken"
else
    fail "reference file not found: $SDPA_TEST"
fi

# ── Check 10: Baseline comparison ────────────────────────────────
header "10. BASELINE vs -mattn -mattn-recognize COMPARISON"
$GCC -O2 \
    -S "$SDPA_TEST" -o /tmp/sdpa_baseline.s 2>/dev/null

BASE_LINES=$(wc -l < /tmp/sdpa_baseline.s)
MATTN_LINES=$(wc -l < /tmp/sdpa_test_out.s 2>/dev/null || echo 0)
BASE_BR=$(grep -c 'bne\|beq\|blt\|bge' /tmp/sdpa_baseline.s 2>/dev/null || echo 0)
MATTN_BR=$(grep -c 'bne\|beq\|blt\|bge' /tmp/sdpa_test_out.s 2>/dev/null || echo 0)

echo "  Without any flag        : $BASE_LINES lines, $BASE_BR branches"
echo "  With -mattn -mattn-recognize : $MATTN_LINES lines, $MATTN_BR branches"
echo "  attn instruction present: $(grep -c '\battn\b' /tmp/sdpa_test_out.s 2>/dev/null) occurrence(s)"
ok "Comparison complete"

# ── SUMMARY ──────────────────────────────────────────────────────
header "SUMMARY"
TOTAL=$((PASS+FAIL))
echo "  Passed : $PASS / $TOTAL"
echo "  Failed : $FAIL / $TOTAL"
echo ""
if [ $FAIL -eq 0 ]; then
    echo "  ALL CHECKS PASSED"
    echo "  Custom attn instruction is correctly implemented."
else
    echo "  $FAIL CHECK(S) FAILED — see details above."
fi
echo ""
echo "  Instruction : attn a3,a0,a1,a2"
echo "  Encoding    : 0x60b5068b  (R4-type, custom-0)"
echo "  MATCH       : 0x0000000b"
echo "  MASK        : 0x0600707f"
echo "  Primary path: -mattn alone (__builtin_riscv_attn, riscv-builtins.cc)"
echo "  Recognizer  : -mattn -mattn-recognize (attnrec, tree-ssa-attn.cc, #179, experimental)"
echo ""
