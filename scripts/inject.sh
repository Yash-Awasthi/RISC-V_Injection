#!/usr/bin/env bash
# inject.sh - one instruction spec in, custom RISC-V support for every tool out.
#
# Pure bash + POSIX tools (awk sed head tail tr od mktemp). Runs unchanged on
# Linux, macOS, WSL and Windows Git Bash / MSYS2 / Cygwin. No Python.
#
#   inject.sh [SPEC_FILE] [options]
#   inject.sh --mnemonic mac --format R4 --semantics 'rs1 * rs2 + rs3'
#
# Spec (SPEC_FILE lines are key=value, '#' comments; every flag overrides the file):
#   --mnemonic N     lowercase name, [a-z][a-z0-9_.]*         (gcc backend: no dots)
#   --format F       R | R4 | I | S | B | U | J               (default R)
#   --operands LIST  comma list from rd,rs1,rs2,rs3,imm in assembly order
#                    (default: the whole format). Registers left out are locked
#                    to 0 in MATCH/MASK.
#   --opcode O       custom-0..custom-3 or a 7-bit int ending in 0b11
#                    (default: first slot with room; CORE-V, T-Head and MIPS
#                    entries already crowd custom-0)
#   --funct3/--funct7/--funct2 N   fixed fields (auto-allocated when absent)
#   --semantics E    C expression over rs1 rs2 rs3 imm pc, result goes to rd
#                    (format B: branch condition; J: ignored). Used by spike, qemu.
#
# Backends (--backend LIST|all, default all): insn binutils gcc llvm spike qemu
# customasm opcodes. Files land in OUT/<mnemonic>/<backend>/. Only binutils and
# gcc edit a source tree, and only with --apply.
#
# Other modes:
#   --encode 'rd=3,rs1=1,imm=16'   print the 32-bit word for these operands
#   --verify PREFIX                assemble/compile with an installed toolchain
#                                  (PREFIX/bin/<triple>-as|objdump|gcc) and
#                                  compare against --encode
#
# Options: --tree DIR (default: repo root)  --out DIR (default: scripts/out)
#          --apply  --yes  --dry-run  --force  --triple T (default riscv64-unknown-elf)

set -eu
LC_ALL=C
export LC_ALL

SELF=$(cd "$(dirname "$0")" && pwd)

die() { echo "ERROR: $*" >&2; exit 1; }
say() { echo "  $*"; }

# ── small helpers ──────────────────────────────────────────────────

upper() { printf '%s' "$1" | tr 'a-z' 'A-Z'; }
symof() { printf '%s' "$1" | tr '.' '_'; }
trim()  { local s=$1; s=${s#"${s%%[![:space:]]*}"}; s=${s%"${s##*[![:space:]]}"}; printf '%s' "$s"; }
hex8()  { printf '0x%08x' "$1"; }

has_op() { case " $OPS " in *" $1 "*) return 0;; esac; return 1; }

is_num() { case $1 in ''|*[!0-9a-fA-FxX]*) return 1;; esac; return 0; }

# awk-based @KEY@ substitution: no regex/backslash/ampersand surprises in values.
# Keys come from $RKEYS; values from exported V_<KEY>.
render() {
  awk 'BEGIN { n = split(ENVIRON["RKEYS"], K, " ") }
       { line = $0
         for (i = 1; i <= n; i++) {
           key = "@" K[i] "@"; val = ENVIRON["V_" K[i]]; out = ""
           while ((p = index(line, key)) > 0) {
             out = out substr(line, 1, p - 1) val
             line = substr(line, p + length(key))
           }
           line = out line
         }
         print line }'
}

emit() {  # emit PATH : write stdin to OUT/MN/BACKEND/PATH
  local p="$OUT/$MN/$BE/$1"
  mkdir -p "$(dirname "$p")"
  cat > "$p"
  say "$BE: $p"
}

skip() { say "$BE: skipped ($*)"; }

# ── format tables ──────────────────────────────────────────────────

fmt_regs() {
  case $1 in
    R) echo "rd rs1 rs2";; R4) echo "rd rs1 rs2 rs3";; I) echo "rd rs1";;
    S|B) echo "rs1 rs2";; U|J) echo "rd";;
  esac
}
fmt_default_ops() {
  case $1 in
    R) echo "rd rs1 rs2";; R4) echo "rd rs1 rs2 rs3";; I) echo "rd rs1 imm";;
    S) echo "rs2 rs1 imm";; B) echo "rs1 rs2 imm";; U|J) echo "rd imm";;
  esac
}
reg_mask() {
  case $1 in
    rd) echo $((0x1f << 7));; rs1) echo $((0x1f << 15));;
    rs2) echo $((0x1f << 20));; rs3) echo $((0x1f << 27));;
  esac
}
slot_value() {
  case $1 in
    custom-0) echo $((0x0b));; custom-1) echo $((0x2b));;
    custom-2) echo $((0x5b));; custom-3) echo $((0x7b));;
    *) is_num "$1" || die "bad opcode '$1'"; echo $(($1));;
  esac
}

# compute_enc: MATCH/MASK from OPC F3 F7 F2 FMT OPS
compute_enc() {
  MATCH=$OPC
  MASK=$((0x7f))
  case $FMT in
    R)  MATCH=$((MATCH | F3 << 12 | F7 << 25)); MASK=$((MASK | 7 << 12 | 0x7f << 25));;
    R4) MATCH=$((MATCH | F3 << 12 | F2 << 25)); MASK=$((MASK | 7 << 12 | 3 << 25));;
    I|S|B) MATCH=$((MATCH | F3 << 12)); MASK=$((MASK | 7 << 12));;
  esac
  local r
  for r in $(fmt_regs "$FMT"); do
    has_op "$r" || MASK=$((MASK | $(reg_mask "$r")))
  done
}

# ── spec loading ───────────────────────────────────────────────────

C_MN= C_FMT= C_OPC= C_F3= C_F7= C_F2= C_OPS= C_SEM=
MN= FMT= OPC_IN= F3= F7= F2= OPS= SEM=

spec_set() {  # spec_set KEY VALUE (source: file)
  case $1 in
    mnemonic) MN=$2;; format) FMT=$2;; opcode) OPC_IN=$2;;
    funct3) F3=$2;; funct7) F7=$2;; funct2) F2=$2;;
    operands) OPS=$(printf '%s' "$2" | tr ',' ' ');;
    semantics) SEM=$2;;
    *) die "unknown spec key '$1'";;
  esac
}

load_spec_file() {
  local line k v
  [ -f "$1" ] || die "spec file not found: $1"
  while IFS= read -r line || [ -n "$line" ]; do
    line=${line%$'\r'}
    case $(trim "$line") in ''|'#'*) continue;; esac
    case $line in *=*) ;; *) die "bad spec line (need key=value): $line";; esac
    k=$(trim "${line%%=*}"); v=$(trim "${line#*=}")
    spec_set "$k" "$v"
  done < "$1"
}

normalise() {
  [ -n "$MN" ] || die "need --mnemonic (or a spec file)"
  case $MN in [a-z]*) ;; *) die "mnemonic must start with a-z";; esac
  case $MN in *[!a-z0-9_.]*) die "mnemonic may only use a-z 0-9 _ .";; esac
  [ -n "$FMT" ] || FMT=R
  case $FMT in R|R4|I|S|B|U|J) ;; *) die "format must be R R4 I S B U J";; esac
  [ -n "$OPS" ] || OPS=$(fmt_default_ops "$FMT")
  local allowed o seen=" "
  allowed="$(fmt_regs "$FMT")"
  case $FMT in I|S|B|U|J) allowed="$allowed imm";; esac
  for o in $OPS; do
    case " $allowed " in *" $o "*) ;; *) die "operand '$o' not valid for format $FMT (allowed: $allowed)";; esac
    case "$seen" in *" $o "*) die "operand '$o' listed twice";; esac
    seen="$seen$o "
  done
  local v
  for v in F3:7 F7:127 F2:3; do
    eval "val=\${${v%%:*}}"
    [ -z "$val" ] && continue
    is_num "$val" || die "${v%%:*} must be a number"
    [ $((val)) -ge 0 ] && [ $((val)) -le ${v##*:} ] || die "${v%%:*} out of range 0..${v##*:}"
  done
  if [ -n "$OPC_IN" ]; then
    OPC=$(slot_value "$OPC_IN")
    [ $((OPC & 3)) -eq 3 ] && [ "$OPC" -le 127 ] || die "opcode must be 7 bits ending in 0b11"
  else
    OPC=
  fi
  [ -n "$SEM" ] || SEM=0
  case $SEM in *$'\n'*|*$'\r'*) die "semantics must be one line";; esac
}

# ── existing encodings (collision check) ───────────────────────────

find_opc_h() {
  local h
  for h in "$TREE/binutils/include/opcode/riscv-opc.h" "$TREE/include/opcode/riscv-opc.h"; do
    [ -f "$h" ] && { echo "$h"; return 0; }
  done
  return 1
}

EN=(); EM=(); EK=()
load_existing() {
  local h n m k own
  own=$(symof "$MN")
  h=$(find_opc_h) || return 0
  while read -r n m k; do
    [ "$n" = "$own" ] && continue
    EN+=("$n"); EM+=($((m))); EK+=($((k)))
  done < <(awk '{ sub(/\r$/, "") }
       $1 == "#define" && $2 ~ /^MATCH_/ && $3 ~ /^0[xX][0-9a-fA-F]+$/ { m[substr($2, 7)] = $3 }
       $1 == "#define" && $2 ~ /^MASK_/  && $3 ~ /^0[xX][0-9a-fA-F]+$/ { k[substr($2, 6)] = $3 }
       END { for (n in m) if (n in k) print tolower(n), m[n], k[n] }' "$h")
  say "checking against ${#EN[@]} encodings from $h"
}

# FN/FM/FK: existing entries that can overlap the given opcode (cheap prefilter)
FN=(); FM=(); FK=()
filter_for_opcode() {
  local i
  FN=(); FM=(); FK=()
  for ((i = 0; i < ${#EN[@]}; i++)); do
    if [ $((EK[i] & 0x7f)) -eq 127 ] && [ $((EM[i] & 0x7f)) -ne "$1" ]; then continue; fi
    FN+=("${EN[i]}"); FM+=("${EM[i]}"); FK+=("${EK[i]}")
  done
}

COLLIDE_WITH=
collides() {  # collides MATCH MASK -> 0 if overlapping an entry in FN/FM/FK
  local i
  for ((i = 0; i < ${#FN[@]}; i++)); do
    if [ $((($1 ^ FM[i]) & $2 & FK[i])) -eq 0 ]; then COLLIDE_WITH=${FN[i]}; return 0; fi
  done
  return 1
}

allocate() {
  local slots opc f3 f7 f2 r3 r7 r2 found=0
  if [ -n "$OPC" ]; then slots=$OPC; else slots="11 43 91 123"; fi   # custom-0..3
  r3=${F3:-"0 1 2 3 4 5 6 7"}
  r7=${F7:-$(seq 0 127)}
  r2=${F2:-"0 1 2 3"}
  case $FMT in R) r2=0;; R4) r7=0;; I|S|B) r7=0; r2=0;; U|J) r3=0; r7=0; r2=0;; esac
  for opc in $slots; do
    OPC=$opc
    filter_for_opcode "$OPC"
    for f3 in $r3; do for f7 in $r7; do for f2 in $r2; do
      F3=$((f3)); F7=$((f7)); F2=$((f2))
      compute_enc
      if ! collides "$MATCH" "$MASK"; then found=1; break 4; fi
    done; done; done
  done
  if [ $found -eq 0 ]; then
    if [ -n "$OPC_IN" ] || [ -n "$C_OPC" ]; then
      compute_enc
      collides "$MATCH" "$MASK" && die "encoding $(hex8 "$MATCH")/$(hex8 "$MASK") overlaps existing '$COLLIDE_WITH'"
    fi
    die "no free encoding left${OPC_IN:+ in opcode $OPC_IN}; try another --opcode, or --operands with fewer register fields"
  fi
}

# ── encode ─────────────────────────────────────────────────────────

do_encode() {
  local kv k v w val regs=" " imm=0
  for kv in $(printf '%s' "$1" | tr ',' ' '); do
    k=${kv%%=*}; v=${kv#*=}
    has_op "$k" || die "'$k' is not an operand of $MN ($OPS)"
    v=${v#x}
    is_num "${v#-}" || die "bad value for $k: $v"
    if [ "$k" = imm ]; then imm=$((v))
    else
      [ $((v)) -ge 0 ] && [ $((v)) -le 31 ] || die "$k must be 0..31"
      regs="$regs$k=$((v)) "
    fi
  done
  w=$MATCH
  local sh
  for k in rd rs1 rs2 rs3; do
    case $regs in *" $k="*)
      val=${regs#*" $k="}; val=${val%% *}
      case $k in rd) sh=7;; rs1) sh=15;; rs2) sh=20;; rs3) sh=27;; esac
      w=$((w | val << sh));;
    esac
  done
  if has_op imm; then
    case $FMT in
      I) [ $imm -ge -2048 ] && [ $imm -le 2047 ] || die "imm out of range for I (-2048..2047)"
         w=$((w | (imm & 0xfff) << 20));;
      S) [ $imm -ge -2048 ] && [ $imm -le 2047 ] || die "imm out of range for S (-2048..2047)"
         w=$((w | ((imm >> 5) & 0x7f) << 25 | (imm & 0x1f) << 7));;
      B) [ $imm -ge -4096 ] && [ $imm -le 4094 ] && [ $((imm & 1)) -eq 0 ] || die "imm for B must be even, -4096..4094"
         w=$((w | ((imm >> 12) & 1) << 31 | ((imm >> 5) & 0x3f) << 25 | ((imm >> 1) & 0xf) << 8 | ((imm >> 11) & 1) << 7));;
      U) [ $imm -ge -524288 ] && [ $imm -le 1048575 ] || die "imm out of range for U (20 bits)"
         w=$((w | (imm & 0xfffff) << 12));;
      J) [ $imm -ge -1048576 ] && [ $imm -le 1048574 ] && [ $((imm & 1)) -eq 0 ] || die "imm for J must be even, -1048576..1048574"
         w=$((w | ((imm >> 20) & 1) << 31 | ((imm >> 1) & 0x3ff) << 21 | ((imm >> 11) & 1) << 20 | ((imm >> 12) & 0xff) << 12));;
    esac
  fi
  printf '0x%08x\n' $((w & 0xffffffff))
}

# ── backend: insn (.insn header + GAS macro; works on any stock toolchain)

order_index() {  # position of operand $1 among asm operands (rd first, then rest)
  local o i=0
  for o in $ORDER; do [ "$o" = "$1" ] && { echo $i; return; }; i=$((i + 1)); done
}
ref_asm() {
  if has_op "$1"; then echo "%$(order_index "$1")"
  elif [ "$1" = imm ]; then echo 0
  else echo x0; fi
}
ref_mac() {
  if has_op "$1"; then echo "\\$1"
  elif [ "$1" = imm ]; then echo 0
  else echo x0; fi
}
insn_line() {  # $1 = ref function
  local r=$1 o
  o=$(printf '0x%02x' "$OPC")
  case $FMT in
    R)  echo ".insn r $o, $F3, $F7, $($r rd), $($r rs1), $($r rs2)";;
    R4) echo ".insn r4 $o, $F3, $F2, $($r rd), $($r rs1), $($r rs2), $($r rs3)";;
    I)  echo ".insn i $o, $F3, $($r rd), $($r rs1), $($r imm)";;
    S)  echo ".insn s $o, $F3, $($r rs2), $($r imm)($($r rs1))";;
    B)  echo ".insn b $o, $F3, $($r rs1), $($r rs2), $($r imm)";;
    U)  echo ".insn u $o, $($r rd), $($r imm)";;
    J)  echo ".insn j $o, $($r rd), $($r imm)";;
  esac
}

be_insn() {
  BE=insn
  local sym U o asm macro head
  sym=$(symof "$MN"); U=$(upper "$sym")
  ORDER=""
  has_op rd && ORDER="rd"
  for o in $OPS; do [ "$o" = rd ] || ORDER="$ORDER${ORDER:+ }$o"; done
  asm=$(insn_line ref_asm)
  macro=$(insn_line ref_mac)
  head="/* $MN: $FMT-type, MATCH $(hex8 "$MATCH") MASK $(hex8 "$MASK") */"

  # C wrapper (macro so that 'i' operands work at every -O level; GNU C).
  local mlist="" clist="" out_c="" pre="" post="" clob=""
  for o in $OPS; do
    [ "$o" = rd ] && continue
    mlist="$mlist${mlist:+, }$o"
    if [ "$o" = imm ]; then clist="$clist${clist:+, }\"i\"(imm)"
    else clist="$clist${clist:+, }\"r\"($o)"; fi
  done
  case $FMT in S) clob=' : "memory"';; esac
  if has_op rd; then
    out_c='"=r"(_rd)'
    pre='({ long _rd; '; post=' _rd; })'
  fi
  {
    echo "$head"
    echo "#ifndef INJECT_${U}_H"
    echo "#define INJECT_${U}_H"
    echo
    case $FMT in
      B|J) echo "/* $FMT-type has a label operand: use the GAS macro in $MN.inc.S */";;
      *)
        printf '#define %s(%s) %s__asm__ volatile ("%s" : %s : %s%s);%s\n' \
          "$sym" "$mlist" "$pre" "$asm" "$out_c" "$clist" "$clob" "$post";;
    esac
    echo
    echo "#endif"
  } | emit "$MN.h"
  {
    echo "/* GAS macro for $MN; use as: $MN $(echo $OPS | tr ' ' ',') */"
    echo ".macro $sym $(echo $OPS | tr ' ' ',')"
    echo "  $macro"
    echo ".endm"
  } | emit "$MN.inc.S"
}

# ── backend: binutils ──────────────────────────────────────────────

asm_operands() {  # binutils operand string
  local o s=""
  if [ "$FMT" = S ]; then echo "t,q(s)"; return; fi
  for o in $OPS; do
    case $o in
      rd) c=d;; rs1) c=s;; rs2) c=t;; rs3) c=r;;
      imm) case $FMT in I) c=j;; B) c=p;; U) c=u;; J) c=a;; esac;;
    esac
    s="$s${s:+,}$c"
  done
  echo "$s"
}

patch_add() {  # patch_add ID FILE KIND TEXT POS  (block on stdin)
  cat > "$PDIR/$1.block"
  printf '%s|%s|%s|%s|%s\n' "$1" "$2" "$3" "$4" "$5" >> "$PDIR/patches.list"
}

be_binutils() {
  BE=binutils
  local U sym
  sym=$(symof "$MN"); U=$(upper "$sym")
  PDIR="$OUT/$MN/binutils"; mkdir -p "$PDIR"; : > "$PDIR/patches.list"
  printf '#define MATCH_%s %s\n#define MASK_%s %s\n' "$U" "$(hex8 "$MATCH")" "$U" "$(hex8 "$MASK")" |
    patch_add b1 binutils/include/opcode/riscv-opc.h startswith '#define MATCH_ADD ' above
  printf 'DECLARE_INSN(%s, MATCH_%s, MASK_%s)\n' "$sym" "$U" "$U" |
    patch_add b2 binutils/include/opcode/riscv-opc.h startswith 'DECLARE_INSN(add,' above
  printf '{"%s", 0, INSN_CLASS_I, "%s", MATCH_%s, MASK_%s, match_opcode, 0 },\n' \
    "$MN" "$(asm_operands)" "$U" "$U" |
    patch_add b3 binutils/opcodes/riscv-opc.c contains '{"unimp"' above
  say "$BE: $PDIR/patches.list (3 edits, applied with --apply)"
}

# ── backend: gcc (builtin path, same shape as the shipped attn) ────

be_gcc() {
  BE=gcc
  case $FMT in R|R4|I) ;; *) skip "format $FMT has no register-result builtin"; return;; esac
  case $MN in *[!a-z0-9_]*) skip "mnemonic must be [a-z0-9_] for a -m flag"; return;; esac
  has_op rd || { skip "needs rd"; return; }
  local U sym ins="" nin=0 o expect="rd" asmargs="%0" opnds decl="" unspec="" i=0 ftype ftypedef="" atypes="" args=""
  sym=$MN; U=$(upper "$sym")
  for o in $OPS; do [ "$o" = rd ] || ins="$ins $o"; done
  case " $OPS" in " rd "*) ;; *) skip "rd must be the first operand"; return;; esac
  # operand numbers: rd = 0, the rest 1..n in listed order
  for o in $ins; do
    i=$((i + 1))
    asmargs="$asmargs,%$i"
    if [ "$o" = imm ]; then
      unspec="$unspec${unspec:+
                    }(match_operand:SI $i \"const_int_operand\" \"n\")"
      atypes="$atypes, SI"
    else
      unspec="$unspec${unspec:+
                    }(match_operand:DI $i \"register_operand\" \"r\")"
      atypes="$atypes, UDI"
    fi
  done
  nin=$i
  [ $nin -ge 1 ] || { skip "needs at least one input operand"; return; }
  ftype="UDI_FTYPE$(printf '%s' "$atypes" | tr -d ' ' | tr ',' '_')"
  ftypedef="DEF_RISCV_FTYPE ($nin, (UDI$atypes))"
  PDIR="$OUT/$MN/gcc"; mkdir -p "$PDIR"; : > "$PDIR/patches.list"

  printf '\nm%s\nTarget Var(TARGET_%s) Init(0)\nEnable the custom %s instruction and __builtin_riscv_%s.\n' \
    "$sym" "$U" "$MN" "$sym" | patch_add g1 gcc/gcc/config/riscv/riscv.opt eof '' below
  printf '  UNSPEC_RISCV_%s\n' "$U" |
    patch_add g2 gcc/gcc/config/riscv/riscv.md contains 'define_c_enum "unspec"' below
  cat <<EOF | patch_add g3 gcc/gcc/config/riscv/riscv.md startswith '(define_insn "nop"' above
(define_insn "riscv_$sym"
  [(set (match_operand:DI 0 "register_operand" "=r")
        (unspec:DI [$unspec]
                   UNSPEC_RISCV_$U))]
  "TARGET_$U"
  "$MN\\t$asmargs"
  [(set_attr "type" "unknown")
   (set_attr "mode" "DI")])

EOF
  printf 'AVAIL (x_%s, TARGET_%s && TARGET_64BIT)\n' "$sym" "$U" |
    patch_add g4 gcc/gcc/config/riscv/riscv-builtins.cc startswith 'AVAIL (hint_pause' below
  printf '  DIRECT_BUILTIN (%s, RISCV_%s, x_%s),\n' "$sym" "$ftype" "$sym" |
    patch_add g5 gcc/gcc/config/riscv/riscv-builtins.cc contains 'DIRECT_BUILTIN (frflags' above
  printf '%s\n' "$ftypedef" |
    patch_add g6 gcc/gcc/config/riscv/riscv-ftypes.def startswith 'DEF_RISCV_FTYPE (0, (VOID))' above

  # generated smoke test: compile and look for the mnemonic
  for o in $ins; do args="$args${args:+, }$o"; done
  {
    echo "/* Compile: riscv64-unknown-elf-gcc -m$sym -O2 -S $sym.c && grep -w $MN $sym.s */"
    printf 'long test_%s(' "$sym"
    local a="" first=1
    for o in $ins; do
      [ "$o" = imm ] && continue
      [ $first -eq 1 ] || printf ', '
      printf 'long %s' "$o"; first=0
    done
    [ $first -eq 1 ] && printf 'void'
    printf ')\n{\n  return __builtin_riscv_%s(' "$sym"
    first=1
    for o in $ins; do
      [ $first -eq 1 ] || printf ', '
      if [ "$o" = imm ]; then printf '5'; else printf '%s' "$o"; fi
      first=0
    done
    printf ');\n}\n'
  } | emit "$sym.c"
  say "$BE: $PDIR/patches.list (6 edits, applied with --apply); use __builtin_riscv_$sym with -m$sym"
}

# ── backend: llvm (TableGen) ───────────────────────────────────────

be_llvm() {
  BE=llvm
  case $FMT in R|R4|I) ;; *) skip "format $FMT"; return;; esac
  local slot="" s sym U cls outs="(outs)" ins="" argstr="" o zero="" n=0 tys="" pat=""
  for s in 0 1 2 3; do [ "$(slot_value custom-$s)" -eq "$OPC" ] && slot=$s; done
  [ -n "$slot" ] || { skip "opcode is not custom-0..3"; return; }
  sym=$(symof "$MN"); U=$(upper "$sym")
  for o in $OPS; do
    argstr="$argstr${argstr:+, }\$$o"
    case $o in
      rd) outs='(outs GPR:$rd)';;
      imm) ins="$ins${ins:+, }simm12:\$imm";;
      *) ins="$ins${ins:+, }GPR:\$$o"; n=$((n + 1))
         tys="$tys${tys:+, }llvm_anyint_ty"; pat="$pat${pat:+, }GPR:\$$o";;
    esac
  done
  for o in $(fmt_regs "$FMT"); do has_op "$o" || zero="$zero${zero:+, }$o = 0"; done
  case $FMT in
    R)  cls="RVInstR<0b$(bin 7 "$F7"), 0b$(bin 3 "$F3"), OPC_CUSTOM_$slot, $outs, (ins $ins), \"$MN\", \"$argstr\">";;
    R4) cls="RVInstR4<0b$(bin 2 "$F2"), 0b$(bin 3 "$F3"), OPC_CUSTOM_$slot, $outs, (ins $ins), \"$MN\", \"$argstr\">";;
    I)  cls="RVInstI<0b$(bin 3 "$F3"), OPC_CUSTOM_$slot, $outs, (ins $ins), \"$MN\", \"$argstr\">";;
  esac
  {
    echo "// $MN: include from RISCVInstrInfo.td. Written against the LLVM 17+ RISCVInstrFormats.td"
    echo "// class signatures; add a subtarget feature predicate (RISCVFeatures.td, RISCV.td) by hand."
    echo "let hasSideEffects = 0, mayLoad = 0, mayStore = 0${zero:+, $zero} in"
    echo "def $U : $cls, Sched<[]>;"
    echo
    echo "// IntrinsicsRISCV.td:"
    echo "//   def int_riscv_$sym : Intrinsic<[llvm_anyint_ty], [$tys], [IntrNoMem]>;"
    echo "// RISCVInstrInfo.td pattern:"
    echo "//   def : Pat<(int_riscv_$sym $pat), ($U $(printf '%s' "$pat" | sed 's/,  */, /g'))>;"
  } | emit "RISCVInstr$U.td"
}

bin() {  # bin WIDTH VALUE -> zero-padded binary
  local w=$1 v=$(($2)) out="" i
  for ((i = w - 1; i >= 0; i--)); do out="$out$(((v >> i) & 1))"; done
  echo "$out"
}

# ── backend: spike (extension plugin, no simulator rebuild) ────────

be_spike() {
  BE=spike
  [ "$FMT" = S ] && { skip "S-type needs MMU access; write it by hand"; return; }
  local sym o immx="0" body args="" fn fns=""
  sym=$(symof "$MN"); fn="custom_$sym"
  case $FMT in I) immx="insn.i_imm()";; U) immx="insn.u_imm()";; B) immx="insn.sb_imm()";; J) immx="insn.uj_imm()";; esac
  body="  reg_t rs1 = RS1, rs2 = RS2, rs3 = RS3; sreg_t imm = $immx;
  (void) rs1; (void) rs2; (void) rs3; (void) imm;"
  case $FMT in
    B) body="$body
  if ($SEM) return pc + imm;
  return pc + 4;";;
    J) body="$body
  WRITE_RD(pc + 4);
  return pc + imm;";;
    *) if has_op rd; then body="$body
  WRITE_RD($SEM);"; fi
       body="$body
  return pc + 4;";;
  esac
  for o in $OPS; do
    case $o in
      rd) o=ax_rd;; rs1) o=ax_rs1;; rs2) o=ax_rs2;; rs3) o=ax_rs3;; imm) o=ax_imm;;
    esac
    args="$args${args:+, }&$o"
  done
  local k; fns="$fn"; for k in 2 3 4 5 6 7 8; do fns="$fns, $fn"; done
  RKEYS="MN SYM FN FNS MATCH MASK IMMX BODY ARGS"
  V_MN=$MN V_SYM=$sym V_FN=$fn V_FNS=$fns V_MATCH=$(hex8 "$MATCH") V_MASK=$(hex8 "$MASK")
  V_IMMX=$immx V_BODY=$body V_ARGS=$args
  export RKEYS V_MN V_SYM V_FN V_FNS V_MATCH V_MASK V_IMMX V_BODY V_ARGS
  render <<'EOF' | emit "${sym}_ext.cc"
// Spike extension for `@MN@`.
// build: g++ -shared -fPIC -std=c++17 -I<spike>/riscv -I<spike>/softfloat -I<spike> -o lib@SYM@.so @SYM@_ext.cc
// run:   spike --extlib=./lib@SYM@.so --extension=@SYM@ prog.elf
// (insn_desc_t carries 8 handlers in this spike version; adjust the count if yours differs.)
#include "insn_macros.h"
#include "extension.h"
#include "decode_macros.h"

#define ARG(NAME, EXPR) static struct : public arg_t { \
  std::string to_string(insn_t insn) const { return EXPR; } } NAME
ARG(ax_rd, xpr_name[insn.rd()]);
ARG(ax_rs1, xpr_name[insn.rs1()]);
ARG(ax_rs2, xpr_name[insn.rs2()]);
ARG(ax_rs3, xpr_name[insn.rs3()]);
ARG(ax_imm, std::to_string((long) (@IMMX@)));

static reg_t @FN@(processor_t* p, insn_t insn, reg_t pc)
{
@BODY@
}

class @SYM@_t : public extension_t
{
 public:
  const char* name() const override { return "@SYM@"; }
  std::vector<insn_desc_t> get_instructions(const processor_t &) override {
    return {{@MATCH@, @MASK@, @FNS@}};
  }
  std::vector<disasm_insn_t *> get_disasms(const processor_t *) override {
    return {new disasm_insn_t("@MN@", @MATCH@, @MASK@, {@ARGS@})};
  }
};

REGISTER_EXTENSION(@SYM@, []() { static @SYM@_t ext; return &ext; })
EOF
}

# ── backend: qemu ──────────────────────────────────────────────────

be_qemu() {
  BE=qemu
  case $FMT in R|R4|I|U) ;; *) skip "format $FMT"; return;; esac
  local sym pat="" b fields="" defs="" o ins="" extra="" get="" call="" sig="" nargs=0 g bitstr
  sym=$(symof "$MN")
  for ((b = 31; b >= 0; b--)); do
    if [ $(((MASK >> b) & 1)) -eq 1 ]; then pat="$pat$(((MATCH >> b) & 1))"; else pat="$pat."; fi
  done
  bitstr="${pat:0:7} ${pat:7:5} ${pat:12:5} ${pat:17:3} ${pat:20:5} ${pat:25:7}"
  for o in $OPS; do
    case $o in
      imm) case $FMT in I) fields="$fields imm=%imm_i";; U) fields="$fields imm=%imm_u";; esac;;
      *) fields="$fields %$o";;
    esac
    defs="$defs $o"
  done
  for o in $OPS; do
    [ "$o" = rd ] && continue
    if [ "$o" = imm ]; then extra="$extra, tcg_constant_tl(a->imm)"; sig="$sig, target_ulong imm"
    else
      get="$get    TCGv $o = get_gpr(ctx, a->$o, EXT_NONE);
"
      extra="$extra, $o"; sig="$sig, target_ulong $o"
    fi
    nargs=$((nargs + 1))
  done
  {
    echo "# append to target/riscv/insn32.decode"
    echo "&$sym$defs"
    echo "@$sym ....... ..... ..... ... ..... ....... &$sym$fields"
    echo "$sym $bitstr @$sym"
  } | emit "insn32.decode.add"
  RKEYS="SYM GET EXTRA"
  V_SYM=$sym V_GET=$get V_EXTRA=$extra
  export RKEYS V_SYM V_GET V_EXTRA
  render <<'EOF' | emit "trans_${sym}.c.inc"
/* include from target/riscv/translate.c; tcg_env is cpu_env before QEMU 9.0 */
static bool trans_@SYM@(DisasContext *ctx, arg_@SYM@ *a)
{
    TCGv dest = dest_gpr(ctx, a->rd);
@GET@    gen_helper_@SYM@(dest, tcg_env@EXTRA@);
    gen_set_gpr(ctx, a->rd, dest);
    return true;
}
EOF
  printf 'DEF_HELPER_%d(%s, tl, env%s)\n' $((nargs + 1)) "$sym" "$(for ((g = 0; g < nargs; g++)); do printf ', tl'; done)" |
    emit "helper.h.add"
  printf 'target_ulong helper_%s(CPURISCVState *env%s)\n{\n    return %s;\n}\n' "$sym" "$sig" "$SEM" |
    emit "op_helper.c.add"
}

# ── backend: customasm ─────────────────────────────────────────────

be_customasm() {
  BE=customasm
  case $FMT in R|R4|I|U) ;; *) skip "format $FMT"; return;; esac
  local sym enc args="" o i
  sym=$(symof "$MN")
  z() { has_op "$1" && echo "$1" || echo "0b00000"; }
  case $FMT in
    R)  enc="0b$(bin 7 "$F7") @ $(z rs2) @ $(z rs1) @ 0b$(bin 3 "$F3") @ $(z rd)";;
    R4) enc="$(z rs3) @ 0b$(bin 2 "$F2") @ $(z rs2) @ $(z rs1) @ 0b$(bin 3 "$F3") @ $(z rd)";;
    I)  has_op imm && im="imm[11:0]" || im="0b000000000000"
        enc="$im @ $(z rs1) @ 0b$(bin 3 "$F3") @ $(z rd)";;
    U)  has_op imm && im="imm[19:0]" || im="0b00000000000000000000"
        enc="$im @ $(z rd)";;
  esac
  enc="$enc @ 0b$(bin 7 "$OPC")"
  for o in $OPS; do
    case $o in
      imm) t=$([ "$FMT" = I ] && echo i12 || echo u20); args="$args${args:+, }{imm: $t}";;
      *) args="$args${args:+, }{$o: reg}";;
    esac
  done
  {
    echo "#subruledef reg"
    echo "{"
    for ((i = 0; i < 32; i++)); do echo "    x$i => $i\`5"; done
    echo "}"
    echo
    echo "#ruledef"
    echo "{"
    echo "    $MN $args => $enc"
    echo "}"
  } | emit "$sym.asm"
}

# ── backend: riscv-opcodes ─────────────────────────────────────────

be_opcodes() {
  BE=opcodes
  local tail="" o fixed
  for o in $OPS; do
    case $o in
      imm) case $FMT in I) tail="$tail imm12";; S) tail="$tail imm12hi imm12lo";; B) tail="$tail bimm12hi bimm12lo";;
                        U) tail="$tail imm20";; J) tail="$tail jimm20";; esac;;
      *) tail="$tail $o";;
    esac
  done
  fixed="6..2=$(printf '0x%02x' $((OPC >> 2))) 1..0=3"
  case $FMT in
    R)  fixed="$fixed 14..12=$F3 31..25=$F7";;
    R4) fixed="$fixed 14..12=$F3 26..25=$F2";;
    I|S|B) fixed="$fixed 14..12=$F3";;
  esac
  printf '%s%s %s\n' "$MN" "$tail" "$fixed" | emit "rv_custom"
}

# ── patch engine (awk anchors, no sed -i, idempotent, .bak once) ───

PATCH_FAILS=0

patch_find() {  # patch_find FILE KIND TEXT POS -> "INSERT_AFTER_LINE ANCHOR_LINE"
  A_KIND=$2 A_TEXT=$3 A_POS=$4 awk '
    { sub(/\r$/, ""); L[NR] = $0 }
    function ltrim(s) { sub(/^[ \t]+/, "", s); return s }
    END {
      kind = ENVIRON["A_KIND"]; text = ENVIRON["A_TEXT"]; pos = ENVIRON["A_POS"]
      hit = 0
      if (kind == "eof") hit = NR
      else for (i = 1; i <= NR; i++) {
        ok = (kind == "startswith") ? (index(ltrim(L[i]), text) == 1) : (index(L[i], text) > 0)
        if (ok) { hit = i; break }
      }
      if (!hit) exit 1
      print (pos == "above" ? hit - 1 : hit), hit
    }' "$1"
}

patch_present() {  # every real line of the block already in the file?
  awk 'function trim(s) { sub(/\r$/, "", s); sub(/^[ \t]+/, "", s); sub(/[ \t]+$/, "", s); return s }
       NR == FNR { s = trim($0); if (length(s) >= 12) need[s] = 1; next }
       { have[trim($0)] = 1 }
       END { c = 0; for (k in need) { c++; if (!(k in have)) exit 1 } exit (c ? 0 : 1) }' "$2" "$1"
}

patch_apply() {  # tree id file kind text pos
  local tree=$1 id=$2 file=$3 kind=$4 text=$5 pos=$6 target res idx at tmp blk lo hi
  target="$tree/$file"; blk="$PDIR/$id.block"
  if [ ! -f "$target" ]; then echo "  FAIL  $id  missing $target"; PATCH_FAILS=$((PATCH_FAILS + 1)); return; fi
  if patch_present "$target" "$blk"; then echo "  SKIP  $id  already applied in $file"; return; fi
  if ! res=$(patch_find "$target" "$kind" "$text" "$pos"); then
    echo "  FAIL  $id  anchor '$text' not found in $file"; PATCH_FAILS=$((PATCH_FAILS + 1)); return
  fi
  idx=${res% *}; at=${res#* }
  lo=$((at > 2 ? at - 2 : 1)); hi=$((at + 2))
  echo "  --- $id  $file:$at ($pos)"
  sed -n "${lo},${hi}p" "$target" | sed 's/^/      | /'
  sed 's/^/      + /' "$blk"
  if [ "$DRYRUN" -eq 1 ]; then echo "  DRY   $id"; return; fi
  if [ "$YES" -ne 1 ]; then
    printf '  apply? [y/N] '
    { read -r ans </dev/tty; } 2>/dev/null || ans=n
    case $ans in y|Y) ;; *) echo "  SKIP  $id  declined"; return;; esac
  fi
  [ -f "$target.bak" ] || cp -p "$target" "$target.bak"
  tmp=$(mktemp)
  [ -n "$(tail -c1 "$target")" ] && printf '\n' >> "$target"
  {
    head -n "$idx" "$target"
    if head -n1 "$target" | od -An -c | grep -q '\\r'; then awk '{ printf "%s\r\n", $0 }' "$blk"; else cat "$blk"; fi
    tail -n +"$((idx + 1))" "$target"
  } > "$tmp"
  cat "$tmp" > "$target"; rm -f "$tmp"
  echo "  OK    $id  inserted at $file:$((idx + 1))"
}

version_gate() {  # version_gate KIND
  local v=""
  case $1 in
    binutils) [ -f "$TREE/binutils/bfd/version.m4" ] &&
                v=$(sed -n 's/.*\[\([0-9][0-9.]*\)\].*/\1/p' "$TREE/binutils/bfd/version.m4" | head -n1)
              say "binutils version: ${v:-?} (validated: 2.46)"
              case $v in 2.46*) ;; *) [ "$FORCE" -eq 1 ] || die "unvalidated binutils version; anchors may not match. Use --force to try anyway.";; esac;;
    gcc)      [ -f "$TREE/gcc/gcc/BASE-VER" ] && v=$(head -n1 "$TREE/gcc/gcc/BASE-VER" | tr -d '\r')
              say "gcc version: ${v:-?} (validated: 15.2)"
              case $v in 15.2*) ;; *) [ "$FORCE" -eq 1 ] || die "unvalidated gcc version; anchors may not match. Use --force to try anyway.";; esac;;
  esac
}

apply_list() {  # apply_list BACKEND
  local id file kind text pos
  PDIR="$OUT/$MN/$1"
  [ -s "$PDIR/patches.list" ] || return 0
  version_gate "$1"
  echo "  applying $1 edits to $TREE"
  while IFS='|' read -r id file kind text pos; do
    patch_apply "$TREE" "$id" "$file" "$kind" "$text" "$pos"
  done < "$PDIR/patches.list"
}

# ── verify against a real installed toolchain ──────────────────────

do_verify() {
  local bin="$1/bin/$TRIPLE" AS OD GCC src obj want got line ops="" o t="" asmline
  AS="$bin-as"; OD="$bin-objdump"; GCC="$bin-gcc"
  [ -x "$AS" ] || [ -x "$AS.exe" ] || die "no assembler at $AS"
  for o in $OPS; do
    case $o in rd) t=a3;; rs1) t=a0;; rs2) t=a1;; rs3) t=a2;; imm) t=8;; esac
    ops="$ops $o=$([ "$o" = imm ] && echo 8 || case $o in rd) echo 13;; rs1) echo 10;; rs2) echo 11;; rs3) echo 12;; esac)"
  done
  want=$(do_encode "$(echo $ops | tr ' ' ',')")
  case $FMT in
    S) asmline="$MN a1, 8(a0)";;
    B|J) asmline="$MN $(for o in $OPS; do case $o in rd) printf 'a3,';; rs1) printf 'a0,';; rs2) printf 'a1,';; imm) printf '.+8,';; esac; done | sed 's/,$//')";;
    *) asmline="$MN $(for o in $OPS; do case $o in rd) printf 'a3,';; rs1) printf 'a0,';; rs2) printf 'a1,';; rs3) printf 'a2,';; imm) printf '8,';; esac; done | sed 's/,$//')";;
  esac
  src=$(mktemp); obj=$(mktemp)
  printf '\t.text\n_start:\n\t%s\n' "$asmline" > "$src"
  if ! "$AS" "$src" -o "$obj" 2>"$src.err"; then
    cat "$src.err"; rm -f "$src" "$src.err" "$obj"
    die "assembler rejected '$asmline' (was binutils rebuilt after --apply?)"
  fi
  got=$("$OD" -d "$obj" | awk '/^ *0:/ { print "0x" $2; exit }')
  rm -f "$src" "$src.err" "$obj"
  say "assembler:  $asmline"
  say "assembled:  $got"
  say "encoded:    $want"
  [ "$got" = "$want" ] || die "MISMATCH between assembler and inject.sh encoding"
  say "PASS  assembler agrees with the generated encoding"
  if [ -f "$OUT/$MN/gcc/$MN.c" ] && { [ -x "$GCC" ] || [ -x "$GCC.exe" ]; }; then
    src=$(mktemp)
    if "$GCC" "-m$MN" -O2 -S "$OUT/$MN/gcc/$MN.c" -o "$src" 2>/dev/null && grep -qw "$MN" "$src"; then
      say "PASS  gcc -m$MN emits '$MN' for __builtin_riscv_$MN"
    else
      rm -f "$src"; die "gcc did not emit '$MN' (was gcc rebuilt after --apply?)"
    fi
    rm -f "$src"
  fi
}

# ── main ───────────────────────────────────────────────────────────

usage() { sed -n '2,/^set -eu/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; }

BACKENDS=all TREE="$(cd "$SELF/.." && pwd)" OUT="$SELF/out" TRIPLE=riscv64-unknown-elf
SPEC_FILE= APPLY=0 YES=0 DRYRUN=0 FORCE=0 ENCODE= VERIFY=

while [ $# -gt 0 ]; do
  case $1 in
    -h|--help) usage; exit 0;;
    --mnemonic|--format|--opcode|--funct3|--funct7|--funct2|--operands|--semantics|--backend|--tree|--out|--encode|--verify|--triple)
      [ $# -ge 2 ] || die "$1 needs a value"
      case $1 in
        --mnemonic) C_MN=$2;; --format) C_FMT=$2;; --opcode) C_OPC=$2;;
        --funct3) C_F3=$2;; --funct7) C_F7=$2;; --funct2) C_F2=$2;;
        --operands) C_OPS=$(printf '%s' "$2" | tr ',' ' ');; --semantics) C_SEM=$2;;
        --backend) BACKENDS=$2;; --tree) TREE=$2;; --out) OUT=$2;;
        --encode) ENCODE=$2;; --verify) VERIFY=$2;; --triple) TRIPLE=$2;;
      esac
      shift 2;;
    --apply) APPLY=1; shift;;
    --yes|-y) YES=1; shift;;
    --dry-run) DRYRUN=1; APPLY=1; shift;;
    --force) FORCE=1; shift;;
    -*) die "unknown option $1 (see --help)";;
    *) [ -z "$SPEC_FILE" ] || die "one spec file only"; SPEC_FILE=$1; shift;;
  esac
done

[ -z "$SPEC_FILE" ] || load_spec_file "$SPEC_FILE"
[ -z "$C_MN" ]  || MN=$C_MN
[ -z "$C_FMT" ] || FMT=$C_FMT
[ -z "$C_OPC" ] || OPC_IN=$C_OPC
[ -z "$C_F3" ]  || F3=$C_F3
[ -z "$C_F7" ]  || F7=$C_F7
[ -z "$C_F2" ]  || F2=$C_F2
[ -z "$C_OPS" ] || OPS=$C_OPS
[ -z "$C_SEM" ] || SEM=$C_SEM
normalise
TREE=$(cd "$TREE" 2>/dev/null && pwd) || die "tree not found: $TREE"
mkdir -p "$OUT"; OUT=$(cd "$OUT" && pwd)

load_existing
allocate
compute_enc

say "$MN  $FMT-type  MATCH=$(hex8 "$MATCH")  MASK=$(hex8 "$MASK")  operands: $OPS"

if [ -n "$ENCODE" ]; then do_encode "$ENCODE"; exit 0; fi

if [ "$BACKENDS" = all ]; then BACKENDS="insn binutils gcc llvm spike qemu customasm opcodes"; fi
BACKENDS=$(printf '%s' "$BACKENDS" | tr ',' ' ')
for b in $BACKENDS; do
  case $b in insn|binutils|gcc|llvm|spike|qemu|customasm|opcodes) ;; *) die "unknown backend '$b'";; esac
done
for b in $BACKENDS; do "be_$b"; done
mkdir -p "$OUT/$MN"
{
  echo "mnemonic=$MN"; echo "format=$FMT"; echo "opcode=$(printf '0x%02x' "$OPC")"
  case $FMT in R|I|S|B) echo "funct3=$F3";; R4) echo "funct3=$F3";; esac
  [ "$FMT" = R ] && echo "funct7=$F7"
  [ "$FMT" = R4 ] && echo "funct2=$F2"
  echo "operands=$(echo $OPS | tr ' ' ',')"; echo "semantics=$SEM"
} > "$OUT/$MN/spec"
say "spec (reusable): $OUT/$MN/spec"

if [ "$APPLY" -eq 1 ]; then
  for b in $BACKENDS; do case $b in binutils|gcc) apply_list "$b";; esac; done
  [ "$PATCH_FAILS" -eq 0 ] || die "$PATCH_FAILS edit(s) failed"
fi
if [ -n "$VERIFY" ]; then do_verify "$VERIFY"; fi
