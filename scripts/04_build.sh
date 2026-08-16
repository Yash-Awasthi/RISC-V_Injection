#!/usr/bin/env bash
# 04_build.sh — Rebuild patched toolchain and verify the new mnemonic.
#
# Usage:
#   ./04_build.sh <mnemonic> [repo_root] [install_prefix]
#
# Example:
#   ./04_build.sh fds  ~/riscv-gnu-toolchain  $HOME/riscv-install
#
# Steps:
#   1. cd to repo_root
#   2. ./configure --prefix=$INSTALL --with-arch=rv64gc --with-abi=lp64d --disable-gdb
#      (skipped if a Makefile already exists)
#   3. Force-regenerate the per-target Makefile so the new tree-ssa-<m>.o is
#      picked up by the build system (see docs/06-extending-toolchain.md §8).
#   4. make -j$(nproc) 2>&1 | tee build.log
#   5. Smoke test: assemble + objdump round-trip on a hand-written .S file.

set -euo pipefail

MNEMONIC="${1:?usage: $0 <mnemonic> [repo_root] [install_prefix]}"

# Default repo root: <scripts>/.. — i.e. one level above this script.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="${2:-$(cd "${SCRIPT_DIR}/.." && pwd)}"
INSTALL="${3:-$HOME/riscv-install}"
NPROC="$(nproc 2>/dev/null || echo 4)"

echo "═══════════════════════════════════════════════════════"
echo "  Build + smoke-test for mnemonic: ${MNEMONIC}"
echo "  Repo root:       ${REPO_ROOT}"
echo "  Install prefix:  ${INSTALL}"
echo "  Parallel jobs:   ${NPROC}"
echo "═══════════════════════════════════════════════════════"

cd "${REPO_ROOT}"

# ── 1. configure (only if Makefile is missing) ─────────────────────
if [[ ! -f Makefile ]]; then
  echo ""
  echo "  [1/4] ./configure ..."
  ./configure \
    --prefix="${INSTALL}" \
    --with-arch=rv64gc \
    --with-abi=lp64d \
    --disable-gdb
else
  echo ""
  echo "  [1/4] Makefile already present — skipping ./configure."
fi

# ── 2. force-regenerate per-target Makefile so new .o is picked up ─
echo ""
echo "  [2/4] Forcing per-target Makefile regeneration ..."
find . -path '*/gcc/Makefile' -delete 2>/dev/null || true

# ── 3. build ───────────────────────────────────────────────────────
echo ""
echo "  [3/4] make -j${NPROC} (output → build.log)"
if ! make -j"${NPROC}" 2>&1 | tee build.log; then
  echo ""
  echo "  ✘ Build FAILED. First 20 errors:"
  grep -n 'error:' build.log | head -20 || true
  exit 1
fi

# ── 4. assembler/disassembler round-trip ───────────────────────────
echo ""
echo "  [4/4] Assembler round-trip on bare ${MNEMONIC} ..."
TMP_S="$(mktemp --suffix=.S)"
TMP_O="${TMP_S%.S}.o"

# Try the most common 4-reg form first, then fall back.
for form in \
    "${MNEMONIC} a3, a0, a1, a2" \
    "${MNEMONIC} a2, a0, a1" \
    "${MNEMONIC} a1, a0" \
    "${MNEMONIC} a0"; do
  cat > "${TMP_S}" <<EOF
        .text
        .globl _start
_start:
        ${form}
EOF
  AS="${INSTALL}/bin/riscv64-unknown-elf-as"
  if "${AS}" "${TMP_S}" -o "${TMP_O}" 2>/dev/null; then
    echo "  used form: ${form}"
    break
  fi
done

OBJDUMP="${INSTALL}/bin/riscv64-unknown-elf-objdump"
DUMP="$("${OBJDUMP}" -d "${TMP_O}")"
echo "${DUMP}"
if echo "${DUMP}" | grep -qw "${MNEMONIC}"; then
  echo "  ✔ ${MNEMONIC} survives assemble + disassemble."
else
  echo "  ✘ ${MNEMONIC} not found in objdump output."
  exit 2
fi

echo ""
echo "  Toolchain ready at ${INSTALL}/bin"
echo "  One build picked up every staged source change at once —"
echo "  run the full checklist now rather than one-off spot checks:"
echo "    ./05_test.sh ${MNEMONIC} ${INSTALL}"
if [[ "${MNEMONIC}" == "attn" ]]; then
  echo "    ../demo/verify_attn.sh ../demo/sdpa_builtin.c"
  echo "    (sweep) for f in ../demo/failures/fail_*.c; do"
  echo "      ${INSTALL}/bin/riscv64-unknown-elf-gcc -mattn -mattn-recognize -O2 -S \"\$f\" -o /tmp/out.s 2>/dev/null"
  echo "      grep -q '\\battn\\b' /tmp/out.s && echo \"FAIL \$f\" || echo \"OK \$f\""
  echo "    done"
fi
