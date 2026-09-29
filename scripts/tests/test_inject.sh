#!/usr/bin/env bash
# Self-check for inject.sh. No toolchain needed; runs on Linux and Windows Git Bash.
# Set INJECT_TEST_PREFIX=<install dir> to also assemble with a real toolchain.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
INJ="$HERE/../inject.sh"
ROOT=$(cd "$HERE/../.." && pwd)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
EMPTY="$TMP/empty"; mkdir -p "$EMPTY"
PASS=0 FAIL=0

check() {  # check NAME COMMAND...
  local name=$1; shift
  if "$@" >"$TMP/log" 2>&1; then PASS=$((PASS + 1)); echo "  ok    $name"
  else FAIL=$((FAIL + 1)); echo "  FAIL  $name"; sed 's/^/        /' "$TMP/log" | tail -8; fi
}
inj() { bash "$INJ" --out "$TMP/out" "$@"; }
enc() { bash "$INJ" --tree "$EMPTY" --out "$TMP/out" "$@" | tail -n1; }
expect() { [ "$1" = "$2" ] || { echo "got $1, want $2"; return 1; }; }
count() { grep -cF -- "$1" "$2"; }
have_tree() { [ -f "$ROOT/binutils/include/opcode/riscv-opc.h" ]; }

# 1. encoder against hand-checked RV32I/F words
t_vectors() {
  expect "$(enc --mnemonic add --format R --opcode 0x33 --funct3 0 --funct7 0 --encode rd=3,rs1=1,rs2=2)" 0x002081b3 &&
  expect "$(enc --mnemonic addi --format I --opcode 0x13 --funct3 0 --encode rd=1,rs1=0,imm=42)" 0x02a00093 &&
  expect "$(enc --mnemonic addi --format I --opcode 0x13 --funct3 0 --encode rd=1,rs1=1,imm=-1)" 0xfff08093 &&
  expect "$(enc --mnemonic sw --format S --opcode 0x23 --funct3 2 --encode rs2=3,rs1=1,imm=16)" 0x0030a823 &&
  expect "$(enc --mnemonic beq --format B --opcode 0x63 --funct3 0 --encode rs1=1,rs2=2,imm=8)" 0x00208463 &&
  expect "$(enc --mnemonic beq --format B --opcode 0x63 --funct3 0 --encode rs1=1,rs2=2,imm=-4)" 0xfe208ee3 &&
  expect "$(enc --mnemonic lui --format U --opcode 0x37 --encode rd=1,imm=0x12345)" 0x123450b7 &&
  expect "$(enc --mnemonic jal --format J --opcode 0x6f --encode rd=1,imm=8)" 0x008000ef &&
  expect "$(enc --mnemonic fmadd.s --format R4 --opcode 0x43 --funct3 0 --funct2 0 --encode rd=1,rs1=2,rs2=3,rs3=4)" 0x203100c3
}
check "encoder matches known RV words" t_vectors

t_range() { ! bash "$INJ" --tree "$EMPTY" --out "$TMP/out" --mnemonic addi --format I --opcode 0x13 --funct3 0 --encode rd=1,rs1=1,imm=4096 >/dev/null 2>&1; }
check "out-of-range immediate rejected" t_range

# 2. collisions against the real tree
t_overlap() {
  local out
  out=$(inj --mnemonic x --format R4 --opcode custom-0 --funct3 0 --funct2 0 --backend opcodes 2>&1) && return 1
  case $out in *overlaps*) return 0;; esac
  echo "$out"; return 1
}
have_tree && check "overlap with attn detected" t_overlap

t_alloc() {
  local fmt out m k
  for fmt in R R4 I S B; do
    out=$(inj --mnemonic zz$(echo $fmt | tr A-Z a-z) --format $fmt --backend opcodes) || return 1
    m=$(printf '%s\n' "$out" | sed -n "s/.*MATCH=\(0x[0-9a-f]*\).*/\1/p")
    k=$(printf '%s\n' "$out" | sed -n "s/.*MASK=\(0x[0-9a-f]*\).*/\1/p")
    [ $((m & k)) -eq $((m)) ] || { echo "$fmt: match outside mask"; return 1; }
  done
}
have_tree && check "auto-allocation in the real tree (R R4 I S B)" t_alloc

# 3. every backend, every format
t_backends() {
  local fmt f fl
  for fmt in R R4 I S B U J; do
    fl=$(echo "$fmt" | tr 'A-Z' 'a-z')
    inj --tree "$EMPTY" --mnemonic "gen$fl" --format "$fmt" --semantics 'rs1 + rs2' >"$TMP/o" 2>&1 || { cat "$TMP/o"; return 1; }
    for f in "insn/gen$fl.h" "insn/gen$fl.inc.S" binutils/patches.list opcodes/rv_custom; do
      [ -s "$TMP/out/gen$fl/$f" ] || { echo "missing gen$fl/$f"; return 1; }
    done
  done
  for fmt in R R4 I; do
    fl=$(echo "$fmt" | tr 'A-Z' 'a-z')
    for f in "gcc/gen$fl.c" gcc/patches.list "llvm/RISCVInstrGEN$(echo "$fl" | tr 'a-z' 'A-Z').td" "spike/gen${fl}_ext.cc" \
             qemu/insn32.decode.add "customasm/gen$fl.asm"; do
      [ -s "$TMP/out/gen$fl/$f" ] || { echo "missing gen$fl/$f"; return 1; }
    done
  done
}
check "all backends produce files for every format" t_backends

t_forms() {
  grep -q '.insn r4 0x0b, 0, 0,' "$TMP/out/genr4/insn/genr4.inc.S" &&
  grep -q '.insn s 0x0b, 0, \\rs2, \\imm(\\rs1)' "$TMP/out/gens/insn/gens.inc.S" &&
  grep -q 'WRITE_RD(rs1 + rs2)' "$TMP/out/genr/spike/genr_ext.cc" &&
  grep -q 'if (rs1 + rs2) return pc + imm' "$TMP/out/genb/spike/genb_ext.cc"
}
check "generated asm and spike text is right" t_forms

# 4. spec file round trip keeps the same encoding
t_spec() {
  local a b
  a=$(inj --tree "$EMPTY" --mnemonic rt --format R4 --backend opcodes | grep MATCH=)
  b=$(inj --tree "$EMPTY" "$TMP/out/rt/spec" --out "$TMP/out2" --backend opcodes | grep MATCH=)
  expect "$a" "$b"
}
check "spec file reproduces the encoding" t_spec

# 5. binutils edits: apply twice, edit once, CRLF preserved
mini_binutils() {
  local d=$1 crlf=${2:-} f
  mkdir -p "$d/binutils/include/opcode" "$d/binutils/opcodes"
  printf '#define MATCH_ADD 0x33\n#define MASK_ADD 0xfe00707f\nDECLARE_INSN(add, MATCH_ADD, MASK_ADD)\n' > "$d/binutils/include/opcode/riscv-opc.h"
  printf '{"unimp", 0, INSN_CLASS_I, "", 0, 0, match_opcode, 0 },\n' > "$d/binutils/opcodes/riscv-opc.c"
  [ -n "$crlf" ] || return 0
  for f in "$d/binutils/include/opcode/riscv-opc.h" "$d/binutils/opcodes/riscv-opc.c"; do
    awk '{ printf "%s\r\n", $0 }' "$f" > "$f.t" && mv "$f.t" "$f"
  done
}

t_binutils() {
  local d="$TMP/bu" h c
  mini_binutils "$d"
  inj --tree "$d" --mnemonic zz --format R --backend binutils --apply --yes --force >/dev/null || return 1
  inj --tree "$d" --mnemonic zz --format R --backend binutils --apply --yes --force >"$TMP/second" || return 1
  h=$d/binutils/include/opcode/riscv-opc.h; c=$d/binutils/opcodes/riscv-opc.c
  [ "$(count 'MATCH_ZZ 0x' "$h")" -eq 1 ] && [ "$(count 'DECLARE_INSN(zz,' "$h")" -eq 1 ] &&
  [ "$(count '{"zz"' "$c")" -eq 1 ] && grep -q SKIP "$TMP/second" &&
  [ -f "$h.bak" ] && [ "$(count MATCH_ZZ "$h.bak")" -eq 0 ] &&
  [ "$(sed -n '1p' "$h")" = '#define MATCH_ZZ 0x0000000b' ]
}
check "binutils apply is idempotent and keeps a .bak" t_binutils

t_crlf() {
  local d="$TMP/bucr" c
  mini_binutils "$d" crlf
  inj --tree "$d" --mnemonic zz --format R --backend binutils --apply --yes --force >/dev/null || return 1
  c=$d/binutils/opcodes/riscv-opc.c
  [ "$(tr -cd '\r' < "$c" | wc -c | tr -d ' ')" -eq 2 ] && [ "$(wc -l < "$c" | tr -d ' ')" -eq 2 ]
}
check "CRLF files stay CRLF" t_crlf

# 6. gcc edits land where the builtin path needs them
t_gcc() {
  local d="$TMP/gcc" r md bi
  r=$d/gcc/gcc/config/riscv; mkdir -p "$r"
  printf 'mfoo\nTarget Var(TARGET_FOO)\nhelp\n' > "$r/riscv.opt"
  printf '(define_c_enum "unspec" [\n  UNSPEC_X\n])\n(define_insn "nop"\n  [(const_int 0)]\n  "" "nop")\n' > "$r/riscv.md"
  printf 'AVAIL (hint_pause, (!0))\n\nstatic const struct x[] = {\n  DIRECT_BUILTIN (frflags, RISCV_USI_FTYPE, hard_float),\n};\n' > "$r/riscv-builtins.cc"
  printf 'DEF_RISCV_FTYPE (0, (USI))\nDEF_RISCV_FTYPE (0, (VOID))\n' > "$r/riscv-ftypes.def"
  inj --tree "$d" --mnemonic mac --format R4 --backend gcc --apply --yes --force >/dev/null || return 1
  inj --tree "$d" --mnemonic mac --format R4 --backend gcc --apply --yes --force >"$TMP/second" || return 1
  md=$r/riscv.md; bi=$r/riscv-builtins.cc
  [ "$(count 'define_insn "riscv_mac"' "$md")" -eq 1 ] &&
  [ "$(count 'UNSPEC_RISCV_MAC' "$md")" -eq 2 ] &&
  [ "$(sed -n '2p' "$md")" = '  UNSPEC_RISCV_MAC' ] &&
  [ "$(count '"mac\t%0,%1,%2,%3"' "$md")" -eq 1 ] &&
  [ "$(sed -n '2p' "$bi")" = 'AVAIL (x_mac, TARGET_MAC && TARGET_64BIT)' ] &&
  [ "$(count 'DIRECT_BUILTIN (mac, RISCV_UDI_FTYPE_UDI_UDI_UDI, x_mac)' "$bi")" -eq 1 ] &&
  [ "$(count 'DEF_RISCV_FTYPE (3, (UDI, UDI, UDI, UDI))' "$r/riscv-ftypes.def")" -eq 1 ] &&
  [ "$(tail -n1 "$r/riscv.opt")" = 'Enable the custom mac instruction and __builtin_riscv_mac.' ] &&
  ! grep -q FAIL "$TMP/second"
}
check "gcc builtin edits land at their anchors, twice-safe" t_gcc

t_missing_anchor() {
  local d="$TMP/bad"
  mini_binutils "$d"; : > "$d/binutils/opcodes/riscv-opc.c"
  ! inj --tree "$d" --mnemonic zz --format R --backend binutils --apply --yes --force >/dev/null 2>&1
}
check "missing anchor fails loudly" t_missing_anchor

t_dry() {
  local d="$TMP/dry"
  mini_binutils "$d"
  inj --tree "$d" --mnemonic zz --format R --backend binutils --dry-run --force >/dev/null || return 1
  [ "$(count MATCH_ZZ "$d/binutils/include/opcode/riscv-opc.h")" -eq 0 ]
}
check "--dry-run leaves the tree alone" t_dry

echo
echo "  $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
