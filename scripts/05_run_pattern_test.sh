#!/usr/bin/env bash
# 05_run_pattern_test.sh — verify the compiler pass emits <mnemonic>
# Group 9 | RISC-V GNU Toolchain
#
# Usage:
#   ./05_run_pattern_test.sh <mnemonic> [install_prefix] [test_c_file]
# Example:
#   ./05_run_pattern_test.sh fds   $HOME/riscv-install   tests/fds.c
#   ./05_run_pattern_test.sh nsum  $HOME/riscv-install   tests/nsum.c
#
# Compiles the C file with -m<flag> -O2 -S and greps for the mnemonic.

set -euo pipefail

MNEMONIC="${1:?usage: $0 <mnemonic> [install_prefix] [test_c_file]}"
INSTALL="${2:-$HOME/riscv-install}"
TEST_C="${3:-$(dirname "$0")/tests/${MNEMONIC}.c}"

if [[ ! -f "${TEST_C}" ]]; then
  echo "  ✘ Test source not found: ${TEST_C}"
  exit 1
fi

GCC="${INSTALL}/bin/riscv64-unknown-elf-gcc"
OUT_S="$(mktemp --suffix=.s)"

echo "═══════════════════════════════════════════════════════"
echo "  Pattern test: ${MNEMONIC}"
echo "  Source:       ${TEST_C}"
echo "  Compiler:     ${GCC}"
echo "═══════════════════════════════════════════════════════"

# The -fno-schedule-insns* flags prevent the scheduler from re-ordering
# instructions across our IFN call (matches docs/03-build-and-run.md).
"${GCC}" \
  "-m${MNEMONIC}" -O2 \
  -fno-schedule-insns -fno-schedule-insns2 \
  -S "${TEST_C}" -o "${OUT_S}"

echo ""
echo "  Generated assembly: ${OUT_S}"
echo ""

if grep -nE "^\s*${MNEMONIC}\b" "${OUT_S}"; then
  echo ""
  echo "  ✔ PASS — '${MNEMONIC}' instruction emitted."
  exit 0
else
  echo ""
  echo "  ✘ FAIL — '${MNEMONIC}' instruction NOT emitted."
  echo "  Hint: dump the GIMPLE pass output with"
  echo "    ${GCC} -m${MNEMONIC} -O2 -fdump-tree-${MNEMONIC}rec-details \\"
  echo "      -S ${TEST_C} -o /tmp/out.s"
  exit 1
fi
