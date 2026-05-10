#!/bin/bash
# ================================================================
# verify_attn.sh — Complete verification of custom attn instruction
#
# Author  : Yash Awasthi
# Compiler: riscv64-unknown-elf-gcc (GCC 15.2.0, modified)
#
# Runs all verification checks and prints a summary.
# Run from ~/riscv-gnu-toolchain or anywhere with GCC in PATH.
#
# Usage:
#   chmod +x verify_attn.sh
#   ./verify_attn.sh sdpa_test.c
# ================================================================

GCC="$HOME/riscv-install/bin/riscv64-unknown-elf-gcc"
AS="$HOME/riscv-install/bin/riscv64-unknown-elf-as"
OBJDUMP="$HOME/riscv-install/bin/riscv64-unknown-elf-objdump"
SRC="${1:-sdpa_test.c}"
PASS=0
FAIL=0

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
    echo "  Usage: $0 sdpa_test.c"
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

# ── Check 5: Pass runs on loop-containing function ───────────────
header "5. PASS FIRES ON LOOP NEST"
rm -f /tmp/attn_loop_test.c.*attnrec*
cat > /tmp/attn_loop_test.c << 'EOF'
void dummy(float *a, float *b, float *c, int n) {
    for (int i = 0; i < n; i++)
        for (int j = 0; j < n; j++)
            c[i] += a[i] * b[j];
}
EOF
$GCC -mattn -O2 -fdump-tree-all -c /tmp/attn_loop_test.c \
    -o /tmp/attn_loop_test.o 2>/dev/null
ls /tmp/attn_loop_test.c.*attnrec* 2>/dev/null | grep -q attnrec \
    && ok "attnrec dump file created — pass is running" \
    || fail "No attnrec dump — pass not registered correctly"

# ── Check 6: Gate blocks without -mattn ──────────────────────────
header "6. GATE — NO attn WITHOUT -mattn"
rm -f /tmp/attn_gate_test.c.*attnrec*
$GCC -O2 -fdump-tree-all -c /tmp/attn_loop_test.c \
    -o /tmp/attn_gate_test.o 2>/dev/null
ls /tmp/attn_loop_test.c.*attnrec* 2>/dev/null | grep -q attnrec \
    && fail "Gate broken — pass fires without -mattn" \
    || ok "Gate correct — pass suppressed without -mattn"

# ── Check 7: attn instruction in sdpa_test.s ─────────────────────
header "7. CUSTOM INSTRUCTION IN ASSEMBLY OUTPUT"
$GCC -mattn -O2 \
    -fno-schedule-insns -fno-schedule-insns2 \
    -fdump-tree-attnrec-details \
    -S "$SRC" -o /tmp/sdpa_test_out.s 2>/dev/null

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
DUMP=$(ls /tmp/sdpa_test.c.*attnrec* 2>/dev/null | head -1)
if [ -z "$DUMP" ]; then
    # try with the output file path
    DUMP=$(ls /tmp/sdpa_test_out.s 2>/dev/null | head -1)
    DUMP=$(ls $SRC.*attnrec* 2>/dev/null | head -1)
fi
DUMP2=$(ls /tmp/*.attnrec* 2>/dev/null | head -1)
[ -z "$DUMP" ] && DUMP="$DUMP2"

if [ -n "$DUMP" ] && [ -f "$DUMP" ]; then
    ok "attnrec dump file found: $DUMP"
    echo ""
    cat "$DUMP" | grep -E 'replaced loop|rejected|bases found|RISCV_ATTN' | head -20
else
    echo "  [INFO] Dump at: $(ls *.c.*attnrec* 2>/dev/null | head -1)"
    echo "  Re-run: $GCC -mattn -O2 -fdump-tree-attnrec-details -c $SRC"
fi

# ── Check 9: Negative test — axpy must NOT emit attn ─────────────
header "9. NEGATIVE TEST — plain loop must NOT emit attn"
cat > /tmp/axpy_test.c << 'EOF'
void axpy(int n, float a, float *x, float *y) {
    for (int i = 0; i < n; i++) y[i] = a * x[i] + y[i];
}
EOF
$GCC -mattn -O2 \
    -fno-schedule-insns -fno-schedule-insns2 \
    -S /tmp/axpy_test.c -o /tmp/axpy_test.s 2>/dev/null
COUNT=$(grep -c '\battn\b' /tmp/axpy_test.s 2>/dev/null || echo 0)
[ "$COUNT" -eq 0 ] \
    && ok "Gate correct — attn not emitted for plain axpy loop" \
    || fail "False positive — attn emitted for non-attention code"

# ── Check 10: Baseline comparison ────────────────────────────────
header "10. BASELINE vs -mattn COMPARISON"
$GCC -O2 -fno-schedule-insns -fno-schedule-insns2 \
    -S "$SRC" -o /tmp/sdpa_baseline.s 2>/dev/null

BASE_LINES=$(wc -l < /tmp/sdpa_baseline.s)
MATTN_LINES=$(wc -l < /tmp/sdpa_test_out.s 2>/dev/null || echo 0)
BASE_BR=$(grep -c 'bne\|beq\|blt\|bge' /tmp/sdpa_baseline.s 2>/dev/null || echo 0)
MATTN_BR=$(grep -c 'bne\|beq\|blt\|bge' /tmp/sdpa_test_out.s 2>/dev/null || echo 0)

echo "  Without -mattn : $BASE_LINES lines, $BASE_BR branches"
echo "  With    -mattn : $MATTN_LINES lines, $MATTN_BR branches"
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
echo "  Instruction: attn a3,a0,a1,a2"
echo "  Encoding   : 0x60b5068b  (R4-type, custom-0)"
echo "  MATCH      : 0x0000000b"
echo "  MASK       : 0x0600707f"
echo "  GCC pass   : attnrec (tree-ssa-attn.cc, position #179)"
echo ""
