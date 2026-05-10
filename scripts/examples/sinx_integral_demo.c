/* sinx_integral_demo.c — Definite integral of sin(x) on [a,b] as one
 * custom RISC-V instruction.
 *
 * Mathematically:    ∫_a^b sin(x) dx = cos(a) - cos(b)
 *
 * We do NOT ask the compiler to discover that closed form.  We use the
 * explicit-marker pattern: a call to __custom_sinint(a_bits, b_bits)
 * (operands passed as IEEE-754 bits in long registers) is rewritten by
 * the generated GIMPLE pass into IFN_RISCV_SININT, which the RISC-V
 * backend lowers to a single `sinint` machine instruction.
 *
 * Build/integrate with:
 *   python3 customrv.py from-c examples/sinx_integral_demo.c \
 *       --mnemonic sinint --apply --build
 */

extern long __custom_sinint(long a_bits, long b_bits);

long sinint_demo(long a_bits, long b_bits)
{
    return __custom_sinint(a_bits, b_bits);
}
