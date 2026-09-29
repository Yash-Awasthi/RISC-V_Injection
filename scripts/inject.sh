#!/usr/bin/env bash
# inject.sh - one instruction spec in, custom RISC-V support for every tool out.
#
# Pure bash + POSIX tools (awk sed head tail tr od mktemp seq). Runs unchanged on
# Linux, macOS, WSL and Windows Git Bash / MSYS2 / Cygwin. No Python.
#
#   inject.sh [SPEC_FILE] [options]
#   inject.sh --mnemonic mac --format R4 --semantics 'rs1 * rs2 + rs3'
#
# A SPEC_FILE holds key=value lines and '#' comments. Several instructions can
# share one file, separated by a line of '---'; they are allocated together so
# their encodings never overlap. Flags override the file (single spec only).
#
# Spec keys (also flags of the same name, without the 'spec' file):
#   mnemonic   lowercase name, [a-z][a-z0-9_.]*  (gcc backend: no dots)
#   format     R | R4 | I | S | B | U | J                    (default R)
#   operands   comma list from rd,rs1,rs2,rs3,imm in assembly order (default: the
#              whole format). Registers left out are locked to 0 in MATCH/MASK.
#   opcode     custom-0..custom-3 or a 7-bit int ending in 0b11 (default: first
#              slot with room; CORE-V, T-Head and MIPS already crowd custom-0)
#   funct3 funct7 funct2   fixed fields (auto-allocated when absent)
#   semantics  C expression over rs1 rs2 rs3 imm pc. R/R4/I/U: value written to
#              rd. S: value stored (default rs2). B: branch condition. J: unused.
#   semantics_py    same in Python syntax (renode); default: semantics
#   semantics_sail  same in Sail syntax over X(rs1) etc. (sail backend needs it)
#   width      store width in bits, 8|16|32|64 (S only, default 64)
#   memory     yes|no: touches memory in a way the compiler cannot see
#              (default: yes for S, else no)
#
# Backends (--backend LIST|all): insn binutils gcc llvm spike qemu customasm
# opcodes renode sail gem5. Files land in OUT/<mnemonic>/<backend>/. Only binutils
# and gcc edit a source tree, and only with --apply.
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

bin() {  # bin WIDTH VALUE -> zero-padded binary
  local w=$1 v=$(($2)) out="" i
  for ((i = w - 1; i >= 0; i--)); do out="$out$(((v >> i) & 1))"; done
  echo "$out"
}

# @KEY@ substitution done by awk index/substr: values may hold any character.
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

need_sem() {  # backends that execute the instruction need semantics
  if [ -z "$SEM" ]; then skip "needs semantics"; return 1; fi
  return 0
}

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
reg_shift() { case $1 in rd) echo 7;; rs1) echo 15;; rs2) echo 20;; rs3) echo 27;; esac; }
reg_mask()  { echo $((0x1f << $(reg_shift "$1"))); }
slot_value() {
  case $1 in
    custom-0) echo $((0x0b));; custom-1) echo $((0x2b));;
    custom-2) echo $((0x5b));; custom-3) echo $((0x7b));;
    *) is_num "$1" || die "bad opcode '$1'"; echo $(($1));;
  esac
}
imm_bits() {  # width of the immediate field of the format
  case $1 in I|S|B) echo 12;; U|J) echo 20;; esac
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
    if ! has_op "$r"; then MASK=$((MASK | $(reg_mask "$r"))); fi
  done
}

# ── spec loading ───────────────────────────────────────────────────

C_MN= C_FMT= C_OPC= C_F3= C_F7= C_F2= C_OPS= C_SEM= C_SEMPY= C_SEMSAIL= C_WIDTH= C_MEM=
MN= FMT= OPC_IN= F3= F7= F2= OPS= SEM= SEMPY= SEMSAIL= WIDTH= MEMORY= OPC=

reset_spec() { MN= FMT= OPC_IN= F3= F7= F2= OPS= SEM= SEMPY= SEMSAIL= WIDTH= MEMORY= OPC=; }

spec_set() {
  case $1 in
    mnemonic) MN=$2;; format) FMT=$2;; opcode) OPC_IN=$2;;
    funct3) F3=$2;; funct7) F7=$2;; funct2) F2=$2;;
    operands) OPS=$(printf '%s' "$2" | tr ',' ' ');;
    semantics) SEM=$2;; semantics_py) SEMPY=$2;; semantics_sail) SEMSAIL=$2;;
    width) WIDTH=$2;; memory) MEMORY=$2;;
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

apply_cli() {
  if [ -n "$C_MN" ]; then MN=$C_MN; fi
  if [ -n "$C_FMT" ]; then FMT=$C_FMT; fi
  if [ -n "$C_OPC" ]; then OPC_IN=$C_OPC; fi
  if [ -n "$C_F3" ]; then F3=$C_F3; fi
  if [ -n "$C_F7" ]; then F7=$C_F7; fi
  if [ -n "$C_F2" ]; then F2=$C_F2; fi
  if [ -n "$C_OPS" ]; then OPS=$C_OPS; fi
  if [ -n "$C_SEM" ]; then SEM=$C_SEM; fi
  if [ -n "$C_SEMPY" ]; then SEMPY=$C_SEMPY; fi
  if [ -n "$C_SEMSAIL" ]; then SEMSAIL=$C_SEMSAIL; fi
  if [ -n "$C_WIDTH" ]; then WIDTH=$C_WIDTH; fi
  if [ -n "$C_MEM" ]; then MEMORY=$C_MEM; fi
}

normalise() {
  [ -n "$MN" ] || die "need --mnemonic (or a spec file)"
  case $MN in [a-z]*) ;; *) die "mnemonic must start with a-z";; esac
  case $MN in *[!a-z0-9_.]*) die "mnemonic may only use a-z 0-9 _ .";; esac
  [ -n "$FMT" ] || FMT=R
  case $FMT in R|R4|I|S|B|U|J) ;; *) die "format must be R R4 I S B U J";; esac
  [ -n "$OPS" ] || OPS=$(fmt_default_ops "$FMT")
  local allowed o seen=" " v val
  allowed="$(fmt_regs "$FMT")"
  case $FMT in I|S|B|U|J) allowed="$allowed imm";; esac
  for o in $OPS; do
    case " $allowed " in *" $o "*) ;; *) die "operand '$o' not valid for format $FMT (allowed: $allowed)";; esac
    case "$seen" in *" $o "*) die "operand '$o' listed twice";; esac
    seen="$seen$o "
  done
  for v in F3:7 F7:127 F2:3; do
    eval "val=\${${v%%:*}}"
    [ -z "$val" ] && continue
    is_num "$val" || die "${v%%:*} must be a number"
    [ $((val)) -ge 0 ] && [ $((val)) -le ${v##*:} ] || die "${v%%:*} out of range 0..${v##*:}"
  done
  if [ -n "$OPC_IN" ]; then
    OPC=$(slot_value "$OPC_IN")
    [ $((OPC & 3)) -eq 3 ] && [ "$OPC" -le 127 ] || die "opcode must be 7 bits ending in 0b11"
  fi
  [ -n "$WIDTH" ] || WIDTH=64
  case $WIDTH in 8|16|32|64) ;; *) die "width must be 8, 16, 32 or 64";; esac
  if [ -z "$MEMORY" ]; then if [ "$FMT" = S ]; then MEMORY=yes; else MEMORY=no; fi; fi
  case $MEMORY in yes|no) ;; *) die "memory must be yes or no";; esac
  if [ -z "$SEM" ] && [ "$FMT" = S ]; then SEM=rs2; fi
  [ -n "$SEMPY" ] || SEMPY=$SEM
  case "$SEM$SEMPY$SEMSAIL" in *$'\n'*|*$'\r'*) die "semantics must be one line";; esac
}

# ── existing encodings (collision check) ───────────────────────────

find_opc_h() {
  local h
  for h in "$TREE/binutils/include/opcode/riscv-opc.h" "$TREE/include/opcode/riscv-opc.h"; do
    if [ -f "$h" ]; then echo "$h"; return 0; fi
  done
  return 1
}

EN=(); EM=(); EK=()
load_existing() {
  local h n m k
  h=$(find_opc_h) || return 0
  while read -r n m k; do
    EN+=("$n"); EM+=($((m))); EK+=($((k)))
  done < <(awk '{ sub(/\r$/, "") }
       $1 == "#define" && $2 ~ /^MATCH_/ && $3 ~ /^0[xX][0-9a-fA-F]+$/ { m[substr($2, 7)] = $3 }
       $1 == "#define" && $2 ~ /^MASK_/  && $3 ~ /^0[xX][0-9a-fA-F]+$/ { k[substr($2, 6)] = $3 }
       END { for (n in m) if (n in k) print tolower(n), m[n], k[n] }' "$h")
  say "checking against ${#EN[@]} encodings from $h"
}

# FN/FM/FK: existing entries that can overlap the given opcode (cheap prefilter).
# The instruction itself is skipped so that re-running after --apply is stable.
FN=(); FM=(); FK=()
filter_for_opcode() {
  local i own
  own=$(symof "$MN")
  FN=(); FM=(); FK=()
  for ((i = 0; i < ${#EN[@]}; i++)); do
    if [ "${EN[i]}" = "$own" ]; then continue; fi
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
    if [ -n "$OPC_IN" ]; then
      compute_enc
      if collides "$MATCH" "$MASK"; then
        die "encoding $(hex8 "$MATCH")/$(hex8 "$MASK") overlaps existing '$COLLIDE_WITH'"
      fi
    fi
    die "no free encoding left${OPC_IN:+ in opcode $OPC_IN}; try another --opcode, or --operands with fewer register fields"
  fi
  # later instructions in the same batch must not reuse this encoding
  EN+=("$(symof "$MN")"); EM+=("$MATCH"); EK+=("$MASK")
}

# ── encode ─────────────────────────────────────────────────────────

do_encode() {
  local kv k v w val regs=" " imm=0 sh
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
  for k in rd rs1 rs2 rs3; do
    case $regs in *" $k="*)
      val=${regs#*" $k="}; val=${val%% *}
      sh=$(reg_shift $k)
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
  for o in $ORDER; do
    if [ "$o" = "$1" ]; then echo $i; return 0; fi
    i=$((i + 1))
  done
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
  local sym U o asm macro head mlist="" clist="" out_c="" pre="" post="" clob=""
  sym=$(symof "$MN"); U=$(upper "$sym")
  ORDER=""
  if has_op rd; then ORDER="rd"; fi
  for o in $OPS; do [ "$o" = rd ] || ORDER="$ORDER${ORDER:+ }$o"; done
  asm=$(insn_line ref_asm)
  macro=$(insn_line ref_mac)
  head="/* $MN: $FMT-type, MATCH $(hex8 "$MATCH") MASK $(hex8 "$MASK") */"

  # C wrapper is a macro so that 'i' operands work at every -O level (GNU C).
  for o in $OPS; do
    [ "$o" = rd ] && continue
    mlist="$mlist${mlist:+, }$o"
    if [ "$o" = imm ]; then clist="$clist${clist:+, }\"i\"(imm)"
    else clist="$clist${clist:+, }\"r\"($o)"; fi
  done
  if [ "$MEMORY" = yes ]; then clob=' : "memory"'; fi
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
  local o s="" c=""
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
  case $FMT in B|J) skip "control flow cannot be a builtin; use the insn backend"; return;; esac
  case $MN in *[!a-z0-9_]*) skip "mnemonic must be [a-z0-9_] for a -m flag"; return;; esac
  if [ "$FMT" = S ] && ! { has_op rs1 && has_op rs2; }; then skip "S-type needs rs1 and rs2"; return; fi
  local U sym o k setrd=0 vol=0 unspec="" atypes="" ret ftype ftypedef nin uns enum setx body asmargs
  local pad='                    ' inum
  sym=$MN; U=$(upper "$sym")
  if has_op rd; then setrd=1; N_rd=0; fi
  k=$setrd
  for o in $OPS; do
    [ "$o" = rd ] && continue
    eval "N_$o=$k"
    if [ "$o" = imm ]; then
      unspec="$unspec${unspec:+
$pad}(match_operand:SI $k \"const_int_operand\" \"n\")"
      atypes="$atypes, SI"
    else
      unspec="$unspec${unspec:+
$pad}(match_operand:DI $k \"register_operand\" \"r\")"
      atypes="$atypes, UDI"
    fi
    k=$((k + 1))
  done
  nin=$((k - setrd))
  if [ $nin -eq 0 ] && [ $setrd -eq 0 ]; then skip "nothing to pass and nothing to return"; return; fi
  if [ $nin -eq 0 ]; then unspec="(const_int 0)"; fi
  if [ "$MEMORY" = yes ] || [ $setrd -eq 0 ] || [ $nin -eq 0 ]; then vol=1; fi
  if [ $vol -eq 1 ]; then uns=unspec_volatile; enum="UNSPECV_RISCV_$U"; else uns=unspec; enum="UNSPEC_RISCV_$U"; fi
  if [ $setrd -eq 1 ]; then
    setx="(set (match_operand:DI 0 \"register_operand\" \"=r\")
        ($uns:DI [$unspec]
$pad$enum))"
    ret=UDI
  else
    setx="($uns [$unspec]
        $enum)"
    ret=VOID
  fi
  if [ "$MEMORY" = yes ]; then
    body="[$setx
   (clobber (mem:BLK (scratch)))]"
  else
    body="[$setx]"
  fi
  # asm template: operands in listed order; S prints imm(rs1)
  if [ "$FMT" = S ]; then
    inum=0; has_op imm && inum="%$N_imm"
    asmargs="%$N_rs2,$inum(%$N_rs1)"
  else
    asmargs=""
    for o in $OPS; do eval "asmargs=\"\$asmargs\${asmargs:+,}%\$N_$o\""; done
  fi
  ftype="${ret}_FTYPE$(printf '%s' "$atypes" | tr -d ' ' | tr ',' '_')"
  ftypedef="DEF_RISCV_FTYPE ($nin, ($ret$atypes))"
  PDIR="$OUT/$MN/gcc"; mkdir -p "$PDIR"; : > "$PDIR/patches.list"

  printf '\nm%s\nTarget Var(TARGET_%s) Init(0)\nEnable the custom %s instruction and __builtin_riscv_%s.\n' \
    "$sym" "$U" "$MN" "$sym" | patch_add g1 gcc/gcc/config/riscv/riscv.opt eof '' below
  if [ $vol -eq 1 ]; then
    printf '  UNSPECV_RISCV_%s\n' "$U" |
      patch_add g2 gcc/gcc/config/riscv/riscv.md contains 'define_c_enum "unspecv"' below
  else
    printf '  UNSPEC_RISCV_%s\n' "$U" |
      patch_add g2 gcc/gcc/config/riscv/riscv.md contains 'define_c_enum "unspec"' below
  fi
  cat <<EOF | patch_add g3 gcc/gcc/config/riscv/riscv.md startswith '(define_insn "nop"' above
(define_insn "riscv_$sym"
  $body
  "TARGET_$U"
  "$MN\\t$asmargs"
  [(set_attr "type" "unknown")
   (set_attr "mode" "DI")])

EOF
  printf 'AVAIL (x_%s, TARGET_%s && TARGET_64BIT)\n' "$sym" "$U" |
    patch_add g4 gcc/gcc/config/riscv/riscv-builtins.cc startswith 'AVAIL (hint_pause' below
  if [ $setrd -eq 1 ]; then
    printf '  DIRECT_BUILTIN (%s, RISCV_%s, x_%s),\n' "$sym" "$ftype" "$sym"
  else
    printf '  DIRECT_NO_TARGET_BUILTIN (%s, RISCV_%s, x_%s),\n' "$sym" "$ftype" "$sym"
  fi | patch_add g5 gcc/gcc/config/riscv/riscv-builtins.cc contains 'DIRECT_BUILTIN (frflags' above
  printf '%s\n' "$ftypedef" |
    patch_add g6 gcc/gcc/config/riscv/riscv-ftypes.def startswith 'DEF_RISCV_FTYPE (0, (VOID))' above

  # generated smoke test: compile and look for the mnemonic
  {
    local first=1 a=""
    echo "/* Compile: riscv64-unknown-elf-gcc -m$sym -O2 -S $sym.c && grep -w $MN $sym.s */"
    if [ $setrd -eq 1 ]; then printf 'unsigned long test_%s(' "$sym"; else printf 'void test_%s(' "$sym"; fi
    for o in $OPS; do
      [ "$o" = rd ] && continue
      [ "$o" = imm ] && continue
      [ $first -eq 1 ] || printf ', '
      printf 'unsigned long %s' "$o"; first=0
    done
    if [ $first -eq 1 ]; then printf 'void'; fi
    printf ')\n{\n  '
    if [ $setrd -eq 1 ]; then printf 'return '; fi
    printf '__builtin_riscv_%s(' "$sym"
    first=1
    for o in $OPS; do
      [ "$o" = rd ] && continue
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
  local slot="" s sym U cls outs="(outs)" ins="" argstr="" o zero="" tys="" pat="" pargs="" pk="" iname ity
  local props="hasSideEffects = 0, mayLoad = 0, mayStore = 0" k=0 ret="[]" iprops="IntrNoMem" argk=0 attr=""
  for s in 0 1 2 3; do [ "$(slot_value custom-$s)" -eq "$OPC" ] && slot=$s; done
  [ -n "$slot" ] || { skip "opcode is not custom-0..3"; return; }
  sym=$(symof "$MN"); U=$(upper "$sym")
  case $FMT in
    I|S|B) iname=imm12;; U|J) iname=imm20;; *) iname=;;
  esac
  case $FMT in I|S) ity=simm12;; B) ity=simm13_lsb0;; U) ity=uimm20_lui;; J) ity=simm21_lsb0_jal;; esac
  for o in $OPS; do
    case $o in
      rd) outs='(outs GPR:$rd)'; ret="[llvm_anyint_ty]"; argstr="$argstr${argstr:+, }\$rd";;
      imm) ins="$ins${ins:+, }$ity:\$$iname"
           if [ "$FMT" = S ]; then :; else argstr="$argstr${argstr:+, }\$$iname"; fi
           tys="$tys${tys:+, }llvm_i32_ty"; pat="$pat${pat:+, }timm:\$$iname"; pargs="$pargs${pargs:+, }timm:\$$iname"
           attr="$attr${attr:+, }ImmArg<ArgIndex<$argk>>"; argk=$((argk + 1));;
      *) ins="$ins${ins:+, }GPR:\$$o"
         if [ "$FMT" = S ]; then :; else argstr="$argstr${argstr:+, }\$$o"; fi
         tys="$tys${tys:+, }llvm_anyint_ty"; pat="$pat${pat:+, }GPR:\$$o"; pargs="$pargs${pargs:+, }GPR:\$$o"
         argk=$((argk + 1));;
    esac
  done
  if [ "$FMT" = S ]; then
    if has_op imm && has_op rs1 && has_op rs2; then argstr="\$rs2, \${$iname}(\${rs1})"
    else for o in $OPS; do argstr="$argstr${argstr:+, }\$$o"; done; fi
  fi
  for o in $(fmt_regs "$FMT"); do has_op "$o" || zero="$zero, $o = 0"; done
  if [ -n "$iname" ] && ! has_op imm; then zero="$zero, $iname = 0"; fi
  case $FMT in
    R)  cls="RVInstR<0b$(bin 7 "$F7"), 0b$(bin 3 "$F3"), OPC_CUSTOM_$slot, $outs, (ins $ins), \"$MN\", \"$argstr\">";;
    R4) cls="RVInstR4<0b$(bin 2 "$F2"), 0b$(bin 3 "$F3"), OPC_CUSTOM_$slot, $outs, (ins $ins), \"$MN\", \"$argstr\">";;
    I)  cls="RVInstI<0b$(bin 3 "$F3"), OPC_CUSTOM_$slot, $outs, (ins $ins), \"$MN\", \"$argstr\">";;
    S)  cls="RVInstS<0b$(bin 3 "$F3"), OPC_CUSTOM_$slot, $outs, (ins $ins), \"$MN\", \"$argstr\">";;
    B)  cls="RVInstB<0b$(bin 3 "$F3"), OPC_CUSTOM_$slot, $outs, (ins $ins), \"$MN\", \"$argstr\">";;
    U)  cls="RVInstU<OPC_CUSTOM_$slot, $outs, (ins $ins), \"$MN\", \"$argstr\">";;
    J)  cls="RVInstJ<OPC_CUSTOM_$slot, $outs, (ins $ins), \"$MN\", \"$argstr\">";;
  esac
  case $FMT in B|J) props="hasSideEffects = 0, mayLoad = 0, mayStore = 0, isBranch = 1, isTerminator = 1";; esac
  if [ "$MEMORY" = yes ]; then props="hasSideEffects = 1, mayLoad = 1, mayStore = 1"; iprops="IntrHasSideEffects"; fi
  [ -z "$attr" ] || iprops="$iprops, $attr"
  {
    echo "// $MN: include from RISCVInstrInfo.td. Written against the LLVM 17+ RISCVInstrFormats.td"
    echo "// class signatures; add a subtarget feature predicate (RISCVFeatures.td, RISCV.td) by hand."
    echo "let $props$zero in"
    echo "def $U : $cls, Sched<[]>;"
    case $FMT in
      B|J) ;;
      *)
        echo
        echo "// IntrinsicsRISCV.td:"
        echo "//   def int_riscv_$sym : Intrinsic<$ret, [$tys], [$iprops]>;"
        echo "// RISCVInstrInfo.td pattern:"
        if has_op rd; then
          echo "//   def : Pat<(int_riscv_$sym $pat), ($U $pargs)>;"
        else
          echo "//   def : Pat<(int_riscv_$sym $pat), ($U $pargs)>;   // no result: match the void call"
        fi;;
    esac
  } | emit "RISCVInstr$U.td"
}

# ── backend: spike (extension plugin, no simulator rebuild) ────────

be_spike() {
  BE=spike
  case $FMT in J) ;; *) need_sem || return 0;; esac
  local sym o immx="0" body args="" fn fns="" k
  sym=$(symof "$MN"); fn="custom_$sym"
  case $FMT in I) immx="insn.i_imm()";; S) immx="insn.s_imm()";; U) immx="insn.u_imm()";; B) immx="insn.sb_imm()";; J) immx="insn.uj_imm()";; esac
  body="  reg_t rs1 = RS1, rs2 = RS2, rs3 = RS3; sreg_t imm = $immx;
  (void) rs1; (void) rs2; (void) rs3; (void) imm;"
  case $FMT in
    B) body="$body
  if ($SEM) return pc + imm;
  return pc + 4;";;
    J) body="$body
  WRITE_RD(pc + 4);
  return pc + imm;";;
    S) body="$body
  MMU.store<uint${WIDTH}_t>(rs1 + imm, (uint${WIDTH}_t) ($SEM));
  return pc + 4;";;
    *) if has_op rd; then body="$body
  WRITE_RD($SEM);"; else body="$body
  (void) ($SEM);"; fi
       body="$body
  return pc + 4;";;
  esac
  for o in $OPS; do
    case $o in
      rd) o=ax_rd;; rs1) o=ax_rs1;; rs2) o=ax_rs2;; rs3) o=ax_rs3;; imm) o=ax_imm;;
    esac
    args="$args${args:+, }&$o"
  done
  fns="$fn"; for k in 2 3 4 5 6 7 8; do fns="$fns, $fn"; done
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
  case $FMT in J) ;; *) need_sem || return 0;; esac
  local sym pat="" b fields="" defs="" o extra="" get="" sig="" nargs=0 g bitstr immf mo helper=1
  sym=$(symof "$MN")
  for ((b = 31; b >= 0; b--)); do
    if [ $(((MASK >> b) & 1)) -eq 1 ]; then pat="$pat$(((MATCH >> b) & 1))"; else pat="$pat."; fi
  done
  bitstr="${pat:0:7} ${pat:7:5} ${pat:12:5} ${pat:17:3} ${pat:20:5} ${pat:25:7}"
  case $FMT in I) immf=imm_i;; S) immf=imm_s;; B) immf=imm_b;; U) immf=imm_u;; J) immf=imm_j;; esac
  for o in $OPS; do
    case $o in
      imm) fields="$fields imm=%$immf";;
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
  case $WIDTH in 8) mo=MO_UB;; 16) mo=MO_UW;; 32) mo=MO_UL;; 64) mo=MO_UQ;; esac
  {
    echo "# append to target/riscv/insn32.decode"
    echo "&$sym$defs"
    echo "@$sym ....... ..... ..... ... ..... ....... &$sym$fields"
    echo "$sym $bitstr @$sym"
  } | emit "insn32.decode.add"
  RKEYS="SYM GET EXTRA MO"
  V_SYM=$sym V_GET=$get V_EXTRA=$extra V_MO=$mo
  export RKEYS V_SYM V_GET V_EXTRA V_MO
  case $FMT in
    R|R4|I|U)
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
      ;;
    S)
      render <<'EOF' | emit "trans_${sym}.c.inc"
/* include from target/riscv/translate.c; tcg_env is cpu_env before QEMU 9.0 */
static bool trans_@SYM@(DisasContext *ctx, arg_@SYM@ *a)
{
    TCGv addr = get_address(ctx, a->rs1, a->imm);
    TCGv val = tcg_temp_new();
@GET@    gen_helper_@SYM@(val, tcg_env@EXTRA@);
    tcg_gen_qemu_st_tl(val, addr, ctx->mem_idx, MO_TE | @MO@);
    return true;
}
EOF
      ;;
    B)
      render <<'EOF' | emit "trans_${sym}.c.inc"
/* include from target/riscv/translate.c; tcg_env is cpu_env before QEMU 9.0 */
static bool trans_@SYM@(DisasContext *ctx, arg_@SYM@ *a)
{
    TCGLabel *taken = gen_new_label();
    TCGv cond = tcg_temp_new();
@GET@    gen_helper_@SYM@(cond, tcg_env@EXTRA@);
    tcg_gen_brcondi_tl(TCG_COND_NE, cond, 0, taken);
    gen_goto_tb(ctx, 1, ctx->pc_succ_insn);
    gen_set_label(taken);
    gen_goto_tb(ctx, 0, ctx->base.pc_next + a->imm);
    ctx->base.is_jmp = DISAS_NORETURN;
    return true;
}
EOF
      ;;
    J)
      helper=0
      render <<'EOF' | emit "trans_${sym}.c.inc"
/* include from target/riscv/translate.c after trans_rvi.c.inc (uses gen_jal) */
static bool trans_@SYM@(DisasContext *ctx, arg_@SYM@ *a)
{
    return gen_jal(ctx, a->rd, a->imm);
}
EOF
      ;;
  esac
  if [ $helper -eq 1 ]; then
    printf 'DEF_HELPER_%d(%s, tl, env%s)\n' $((nargs + 1)) "$sym" "$(for ((g = 0; g < nargs; g++)); do printf ', tl'; done)" |
      emit "helper.h.add"
    printf 'target_ulong helper_%s(CPURISCVState *env%s)\n{\n    return %s;\n}\n' "$sym" "$sig" "$SEM" |
      emit "op_helper.c.add"
  fi
}

# ── backend: customasm ─────────────────────────────────────────────

be_customasm() {
  BE=customasm
  local sym enc args="" o i t f3b r off="" body im
  sym=$(symof "$MN")
  z() { if has_op "$1"; then echo "$1"; else echo "0b00000"; fi; }
  f3b="0b$(bin 3 "$F3")"
  for o in $OPS; do
    case $o in
      imm) case $FMT in I|S) t=i12;; U) t=u20;; B|J) t=u32;; esac
           case $FMT in B|J) args="$args${args:+, }{target: $t}";; *) args="$args${args:+, }{imm: $t}";; esac;;
      *) args="$args${args:+, }{$o: reg}";;
    esac
  done
  case $FMT in
    R)  enc="0b$(bin 7 "$F7") @ $(z rs2) @ $(z rs1) @ $f3b @ $(z rd)";;
    R4) enc="$(z rs3) @ 0b$(bin 2 "$F2") @ $(z rs2) @ $(z rs1) @ $f3b @ $(z rd)";;
    I)  if has_op imm; then im="imm[11:0]"; else im="0b000000000000"; fi
        enc="$im @ $(z rs1) @ $f3b @ $(z rd)";;
    S)  if has_op imm; then enc="imm[11:5] @ $(z rs2) @ $(z rs1) @ $f3b @ imm[4:0]"
        else enc="0b0000000 @ $(z rs2) @ $(z rs1) @ $f3b @ 0b00000"; fi;;
    B)  off="off = target - pc"
        enc="off[12:12] @ off[10:5] @ $(z rs2) @ $(z rs1) @ $f3b @ off[4:1] @ off[11:11]";;
    U)  if has_op imm; then im="imm[19:0]"; else im="0b00000000000000000000"; fi
        enc="$im @ $(z rd)";;
    J)  off="off = target - pc"
        enc="off[20:20] @ off[10:1] @ off[11:11] @ off[19:12] @ $(z rd)";;
  esac
  enc="$enc @ 0b$(bin 7 "$OPC")"
  {
    echo "#subruledef reg"
    echo "{"
    for ((i = 0; i < 32; i++)); do echo "    x$i => $i\`5"; done
    echo "}"
    echo
    echo "#ruledef"
    echo "{"
    if [ -n "$off" ]; then
      echo "    $MN $args => {"
      echo "        $off"
      echo "        $enc"
      echo "    }"
    else
      echo "    $MN $args => $enc"
    fi
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

# ── backend: renode (custom instruction handler, no rebuild) ───────

be_renode() {
  BE=renode
  case $FMT in R|R4|I|U) ;; *) skip "format $FMT (needs PC/bus access)"; return;; esac
  need_sem || return 0
  local b bits="" ch py="" o
  for ((b = 31; b >= 0; b--)); do
    if [ $(((MASK >> b) & 1)) -eq 1 ]; then bits="$bits$(((MATCH >> b) & 1))"; continue; fi
    ch=x
    case $FMT in R4) if [ $b -ge 27 ]; then ch=c; fi;; esac
    case $FMT in I) if [ $b -ge 20 ]; then ch=i; fi;; U) if [ $b -ge 12 ]; then ch=i; fi;; esac
    if [ $b -ge 20 ] && [ $b -le 24 ] && [ "$FMT" != I ] && [ "$FMT" != U ]; then ch=b; fi
    if [ $b -ge 15 ] && [ $b -le 19 ] && [ "$FMT" != U ]; then ch=a; fi
    if [ $b -ge 7 ] && [ $b -le 11 ]; then ch=d; fi
    bits="$bits$ch"
  done
  for o in rs1 rs2 rs3; do
    if has_op $o; then py="$py$o = cpu.GetRegister((instruction >> $(reg_shift $o)) & 31).RawValue; "; fi
  done
  case $FMT in
    I) if has_op imm; then py="${py}imm = (((instruction >> 20) & 4095) ^ 2048) - 2048; "; fi;;
    U) if has_op imm; then py="${py}imm = (instruction >> 12) & 1048575; "; fi;;
  esac
  if has_op rd; then
    py="${py}cpu.SetRegister((instruction >> 7) & 31, ($SEMPY) & 0xFFFFFFFFFFFFFFFF)"
  else
    py="${py}res = ($SEMPY)"
  fi
  printf '# RV64 hart; add to a .resc after the machine is created\nsysbus.cpu InstallCustomInstructionHandlerFromString "%s" "%s"\n' "$bits" "$py" |
    emit "$(symof "$MN").resc"
}

# ── backend: sail (sail-riscv model) ───────────────────────────────

be_sail() {
  BE=sail
  case $FMT in R|R4|I|U|S) ;; *) skip "format $FMT"; return;; esac
  if [ -z "$SEMSAIL" ]; then skip "needs semantics_sail (Sail expression over X(rs1) ..., e.g. X(rs1) + X(rs2))"; return; fi
  local U types="" names="" enc="" asm="" o first=1 rd0="0b00000" ex
  U=$(upper "$(symof "$MN")")
  reg() { if has_op "$1"; then echo "encdec_reg($1)"; else echo "0b00000"; fi; }
  # union tuple: immediate first, then registers from rs3 down to rd
  if has_op imm; then
    case $FMT in
      I) types="bits(12)"; names="imm";;
      U) types="bits(20)"; names="imm";;
      S) types="bits(12)"; names="imm7 @ imm5";;
    esac
  fi
  for o in rs3 rs2 rs1 rd; do
    if has_op $o; then types="$types${types:+, }regidx"; names="$names${names:+, }$o"; fi
  done
  case $FMT in
    R)  enc="0b$(bin 7 "$F7") @ $(reg rs2) @ $(reg rs1) @ 0b$(bin 3 "$F3") @ $(reg rd)";;
    R4) enc="$(reg rs3) @ 0b$(bin 2 "$F2") @ $(reg rs2) @ $(reg rs1) @ 0b$(bin 3 "$F3") @ $(reg rd)";;
    I)  if has_op imm; then enc="imm"; else enc="0x000"; fi
        enc="$enc @ $(reg rs1) @ 0b$(bin 3 "$F3") @ $(reg rd)";;
    U)  if has_op imm; then enc="imm"; else enc="0x00000"; fi
        enc="$enc @ $(reg rd)";;
    S)  enc="imm7 @ $(reg rs2) @ $(reg rs1) @ 0b$(bin 3 "$F3") @ imm5";;
  esac
  enc="$enc @ 0b$(bin 7 "$OPC")"
  for o in $OPS; do
    if [ $first -eq 1 ]; then first=0; else asm="$asm ^ sep()"; fi
    case $o in
      imm) case $FMT in U) asm="$asm ^ hex_bits_20(imm)";; S) asm="$asm ^ hex_bits_signed_12(imm7 @ imm5)";; *) asm="$asm ^ hex_bits_signed_12(imm)";; esac;;
      *) asm="$asm ^ reg_name($o)";;
    esac
  done
  if [ "$FMT" = S ] && has_op imm && has_op rs1; then
    asm=""
    for o in $OPS; do
      case $o in
        imm) ;;
        rs1) ;;
        *) asm="$asm${asm:+ ^ sep() ^ }reg_name($o)";;
      esac
    done
    asm="$asm ^ sep() ^ hex_bits_signed_12(imm7 @ imm5) ^ \"(\" ^ reg_name(rs1) ^ \")\""
  fi
  RKEYS="MN U TYPES NAMES ENC ASM RD SEM"
  V_MN=$MN V_U=$U V_TYPES=$types V_NAMES=$names V_ENC=$enc V_ASM=${asm# ^ } V_RD=X V_SEM=$SEMSAIL
  export RKEYS V_MN V_U V_TYPES V_NAMES V_ENC V_ASM V_RD V_SEM
  if [ "$FMT" = S ]; then
    ex="  let offset : xlenbits = sign_extend(imm7 @ imm5);
  let value : xlenbits = $SEMSAIL;
  let data = value[$((WIDTH - 1)) .. 0];
  match vmem_write(rs1, offset, $((WIDTH / 8)), data, Store(Data), false, false, false) {
    Ok(_) => RETIRE_SUCCESS,
    Err(e) => e,
  }"
    V_SEM=$ex; export V_SEM
    say "$BE: S-type execute follows the STORE clause of the current sail-riscv (vmem_write); check names against your model revision"
  fi
  {
    echo "// $MN: add to a new file, list it in riscv.sail_project after the extension it depends on."
    echo "union clause instruction = @U@_INSN : (@TYPES@)"
    echo
    echo "mapping clause encdec = @U@_INSN(@NAMES@)"
    echo "  <-> @ENC@"
    echo
    echo "mapping clause assembly = @U@_INSN(@NAMES@)"
    echo "  <-> \"@MN@\" ^ spc() ^ @ASM@"
    echo
    echo "function clause execute @U@_INSN(@NAMES@) = {"
    if [ "$FMT" = S ]; then
      echo "@SEM@"
    else
      echo "  @RD@(rd) = @SEM@;"
      echo "  RETIRE_SUCCESS"
    fi
    echo "}"
  } | render | emit "$(symof "$MN").sail"
}

# ── backend: gem5 (ISA decoder entry) ──────────────────────────────

be_gem5() {
  BE=gem5
  case $FMT in R4) skip "R4-type"; return;; J) ;; *) need_sem || return 0;; esac
  local opc5 fmt code pre ea mem
  opc5=$(printf '0x%02x' $((OPC >> 2)))
  pre="uint64_t rs1 = Rs1, rs2 = Rs2; (void) rs1; (void) rs2;"
  case $WIDTH in 8) mem=Mem_ub;; 16) mem=Mem_uh;; 32) mem=Mem_uw;; 64) mem=Mem_ud;; esac
  case $FMT in
    R) code="ROp::$(symof "$MN")({{ $pre Rd = $SEM; }});";;
    I) code="IOp::$(symof "$MN")({{ $pre Rd = $SEM; }});";;
    U) code="UOp::$(symof "$MN")({{ Rd = $SEM; }});";;
    S) code="SOp::$(symof "$MN")({{ $pre $mem = $SEM; }}, {{ EA = Rs1 + imm; }});";;
    B) code="BOp::$(symof "$MN")({{ $pre if ($SEM) NPC = PC + imm; else NPC = NPC; }}, IsDirectControl, IsCondControl);";;
    J) code="JOp::$(symof "$MN")({{ Rd = NPC; NPC = PC + imm; }}, IsDirectControl, IsUncondControl, IsCall);";;
  esac
  {
    echo "// src/arch/riscv/isa/decoder.isa: merge into the existing OPCODE5 $opc5 block if there is one"
    case $FMT in
      U|J) echo "$opc5: $code";;
      R) echo "$opc5: decode FUNCT3 {"
         echo "    $(printf '%s' "$(printf '0x%x' "$F3")"): decode FUNCT7 {"
         echo "        $(printf '0x%02x' "$F7"): $code"
         echo "    }"
         echo "}";;
      *) echo "$opc5: decode FUNCT3 {"
         echo "    $(printf '0x%x' "$F3"): $code"
         echo "}";;
    esac
  } | emit "decoder.isa.add"
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
  local tree=$1 id=$2 file=$3 kind=$4 text=$5 pos=$6 target res idx at tmp blk lo hi ans
  target="$tree/$file"; blk="$PDIR/$id.block"
  if [ ! -f "$target" ]; then echo "  FAIL  $id  missing $target"; PATCH_FAILS=$((PATCH_FAILS + 1)); return 0; fi
  if patch_present "$target" "$blk"; then echo "  SKIP  $id  already applied in $file"; return 0; fi
  if ! res=$(patch_find "$target" "$kind" "$text" "$pos"); then
    echo "  FAIL  $id  anchor '$text' not found in $file"; PATCH_FAILS=$((PATCH_FAILS + 1)); return 0
  fi
  idx=${res% *}; at=${res#* }
  lo=$((at > 2 ? at - 2 : 1)); hi=$((at + 2))
  echo "  --- $id  $file:$at ($pos)"
  sed -n "${lo},${hi}p" "$target" | sed 's/^/      | /'
  sed 's/^/      + /' "$blk"
  if [ "$DRYRUN" -eq 1 ]; then echo "  DRY   $id"; return 0; fi
  if [ "$YES" -ne 1 ]; then
    printf '  apply? [y/N] '
    ans=n
    { read -r ans </dev/tty; } 2>/dev/null || ans=n
    case $ans in y|Y) ;; *) echo "  SKIP  $id  declined"; return 0;; esac
  fi
  [ -f "$target.bak" ] || cp -p "$target" "$target.bak"
  tmp=$(mktemp)
  if [ -n "$(tail -c1 "$target")" ]; then printf '\n' >> "$target"; fi
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
    binutils) if [ -f "$TREE/binutils/bfd/version.m4" ]; then
                v=$(sed -n 's/.*\[\([0-9][0-9.]*\)\].*/\1/p' "$TREE/binutils/bfd/version.m4" | head -n1)
              fi
              say "binutils version: ${v:-?} (validated: 2.46)"
              case $v in 2.46*) ;; *) [ "$FORCE" -eq 1 ] || die "unvalidated binutils version; anchors may not match. Use --force to try anyway.";; esac;;
    gcc)      if [ -f "$TREE/gcc/gcc/BASE-VER" ]; then v=$(head -n1 "$TREE/gcc/gcc/BASE-VER" | tr -d '\r'); fi
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
  local bin="$1/bin/$TRIPLE" AS OD GCC src obj want got o ops="" asmline v
  AS="$bin-as"; OD="$bin-objdump"; GCC="$bin-gcc"
  [ -x "$AS" ] || [ -x "$AS.exe" ] || die "no assembler at $AS"
  for o in $OPS; do
    case $o in rd) v=13;; rs1) v=10;; rs2) v=11;; rs3) v=12;; imm) v=8;; esac
    ops="$ops $o=$v"
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

# ── one instruction, start to finish ───────────────────────────────

ALL_BACKENDS="insn binutils gcc llvm spike qemu customasm opcodes renode sail gem5"

run_one() {
  normalise
  allocate
  compute_enc
  say "$MN  $FMT-type  MATCH=$(hex8 "$MATCH")  MASK=$(hex8 "$MASK")  operands: $OPS"
  if [ -n "$ENCODE" ]; then do_encode "$ENCODE"; return 0; fi

  local b
  for b in $BACKENDS; do "be_$b"; done
  mkdir -p "$OUT/$MN"
  {
    echo "mnemonic=$MN"; echo "format=$FMT"; echo "opcode=$(printf '0x%02x' "$OPC")"
    case $FMT in R|I|S|B|R4) echo "funct3=$F3";; esac
    if [ "$FMT" = R ]; then echo "funct7=$F7"; fi
    if [ "$FMT" = R4 ]; then echo "funct2=$F2"; fi
    echo "operands=$(echo $OPS | tr ' ' ',')"
    if [ -n "$SEM" ]; then echo "semantics=$SEM"; fi
    if [ "$SEMPY" != "$SEM" ]; then echo "semantics_py=$SEMPY"; fi
    if [ -n "$SEMSAIL" ]; then echo "semantics_sail=$SEMSAIL"; fi
    echo "width=$WIDTH"; echo "memory=$MEMORY"
  } > "$OUT/$MN/spec"
  say "spec (reusable): $OUT/$MN/spec"

  if [ "$APPLY" -eq 1 ]; then
    for b in $BACKENDS; do case $b in binutils|gcc) apply_list "$b";; esac; done
    [ "$PATCH_FAILS" -eq 0 ] || die "$PATCH_FAILS edit(s) failed"
  fi
  if [ -n "$VERIFY" ]; then do_verify "$VERIFY"; fi
}

# ── main ───────────────────────────────────────────────────────────

usage() { sed -n '2,/^set -eu/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; }

BACKENDS=all TREE="$(cd "$SELF/.." && pwd)" OUT="$SELF/out" TRIPLE=riscv64-unknown-elf
SPEC_FILE= APPLY=0 YES=0 DRYRUN=0 FORCE=0 ENCODE= VERIFY=

while [ $# -gt 0 ]; do
  case $1 in
    -h|--help) usage; exit 0;;
    --mnemonic|--format|--opcode|--funct3|--funct7|--funct2|--operands|--semantics|--semantics-py|--semantics-sail|--width|--memory|--backend|--tree|--out|--encode|--verify|--triple)
      [ $# -ge 2 ] || die "$1 needs a value"
      case $1 in
        --mnemonic) C_MN=$2;; --format) C_FMT=$2;; --opcode) C_OPC=$2;;
        --funct3) C_F3=$2;; --funct7) C_F7=$2;; --funct2) C_F2=$2;;
        --operands) C_OPS=$(printf '%s' "$2" | tr ',' ' ');; --semantics) C_SEM=$2;;
        --semantics-py) C_SEMPY=$2;; --semantics-sail) C_SEMSAIL=$2;;
        --width) C_WIDTH=$2;; --memory) C_MEM=$2;;
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

TREE=$(cd "$TREE" 2>/dev/null && pwd) || die "tree not found: $TREE"
mkdir -p "$OUT"; OUT=$(cd "$OUT" && pwd)

if [ "$BACKENDS" = all ]; then BACKENDS=$ALL_BACKENDS; fi
BACKENDS=$(printf '%s' "$BACKENDS" | tr ',' ' ')
for b in $BACKENDS; do
  case " $ALL_BACKENDS " in *" $b "*) ;; *) die "unknown backend '$b' (choose from: $ALL_BACKENDS)";; esac
done

load_existing

if [ -n "$SPEC_FILE" ] && [ -f "$SPEC_FILE" ] && grep -q '^---[[:space:]]*$' "$SPEC_FILE"; then
  [ -z "$C_MN" ] || die "--mnemonic cannot be combined with a multi-instruction spec"
  [ -z "$ENCODE" ] || die "--encode needs a single instruction"
  CHUNKS=$(mktemp -d)
  awk -v d="$CHUNKS" 'BEGIN { n = 1 } /^---[ \t\r]*$/ { n++; next } { print > (d "/spec." n) }' "$SPEC_FILE"
  for f in "$CHUNKS"/spec.*; do
    [ -s "$f" ] || continue
    reset_spec
    load_spec_file "$f"
    apply_cli
    run_one
    echo
  done
  rm -rf "$CHUNKS"
else
  reset_spec
  if [ -n "$SPEC_FILE" ]; then load_spec_file "$SPEC_FILE"; fi
  apply_cli
  run_one
fi
