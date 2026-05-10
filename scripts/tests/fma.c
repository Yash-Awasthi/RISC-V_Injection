/* tests/fma.c — marker-based pattern test.
   The matcher rewrites calls to __custom_fma() into IFN_RISCV_FMA,
   which the RISC-V backend lowers to a single `fma` instruction. */

extern long __custom_fma(long a1, long a2, long a3);

long fma_demo(long a1, long a2, long a3)
{
    return __custom_fma(a1, a2, a3);
}
