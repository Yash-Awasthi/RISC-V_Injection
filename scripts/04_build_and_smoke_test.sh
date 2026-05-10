#!/usr/bin/env bash
# 04_build_and_smoke_test.sh — rebuild patched toolchain and verify mnemonic
# Group 9 | RISC-V GNU Toolchain
#
# Usage:
#   ./04_build_and_smoke_test.sh <mnemonic> [repo_root] [install_prefix]
# Example:
#   ./04_build_and_smoke_test.sh fds  ~/riscv-gnu-toolchain  $HOME/riscv-install
#
# Steps:
#   1. cd to repo_root
#   2. ./configure --prefix=$INSTALL --with-arch=rv64gc --with-abi=lp64d --disable-gdb
#   3. make -j$(nproc) 2>&1 | tee build.log
#   4. Smoke test: as + objdump round-trip on a hand-written .S
#   5. Smoke test: $INSTALL/bin/riscv64-unknown-elf-objdump --help works

set -euo pipefail

MNEMONIC="${1:?usage: $0 <mnemonic> [repo_root] [install_prefix]}"
REPO_ROOT="${2:-$(cd "$(dirname "$0")/../.." && pwd)}"
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

# ── 2. build ───────────────────────────────────────────────────────
echo ""
echo "  [2/4] make -j${NPROC} (output → build.log)"
if ! make -j"${NPROC}" 2>&1 | tee build.log; then
  echo ""
  echo "  ✘ Build FAILED. First 20 errors:"
  grep -n 'error:' build.log | head -20 || true
  exit 1
fi

# ── 3. assembler/disassembler round-trip ───────────────────────────
echo ""
echo "  [3/4] Assembler round-trip on bare ${MNEMONIC} ..."
TMP_S="$(mktemp --suffix=.S)"
TMP_O="${TMP_S%.S}.o"

# Operand string depends on num_inputs, but for a smoke test we just
# write the most common 4-reg form and fall back to 2-reg if it fails.
cat > "${TMP_S}" <<EOF
        .text
        .globl _start
_start:
        ${MNEMONIC} a3, a0, a1, a2
EOF

AS="${INSTALL}/bin/riscv64-unknown-elf-as"
OBJDUMP="${INSTALL}/bin/riscv64-unknown-elf-objdump"

if ! "${AS}" "${TMP_S}" -o "${TMP_O}" 2>/dev/null; then
  # Retry with 2-reg form (R-type, 1 source).
  cat > "${TMP_S}" <<EOF
        .text
        .globl _start
_start:
        ${MNEMONIC} a1, a0
EOF
  "${AS}" "${TMP_S}" -o "${TMP_O}"
fi

DUMP="$("${OBJDUMP}" -d "${TMP_O}")"
echo "${DUMP}"
if echo "${DUMP}" | grep -qw "${MNEMONIC}"; then
  echo "  ✔ ${MNEMONIC} survives assemble + disassemble."
else
  echo "  ✘ ${MNEMONIC} not found in objdump output."
  exit 2
fi

# ── 4. final ack ───────────────────────────────────────────────────
echo ""
echo "  [4/4] Toolchain ready at ${INSTALL}/bin"
echo ""
echo "  Next: ./05_run_pattern_test.sh ${MNEMONIC} [install_prefix]"
