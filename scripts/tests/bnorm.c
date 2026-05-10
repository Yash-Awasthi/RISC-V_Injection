/* tests/bnorm.c — marker-based pattern test.
   The matcher rewrites calls to __custom_bnorm() into IFN_RISCV_BNORM,
   which the RISC-V backend lowers to a single `bnorm` instruction. */

extern long __custom_bnorm(long a1, long a2, long a3);

long bnorm_demo(long a1, long a2, long a3)
{
    return __custom_bnorm(a1, a2, a3);
}
