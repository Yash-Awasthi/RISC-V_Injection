/* tests/sinint.c — marker-based pattern test.
   The matcher rewrites calls to __custom_sinint() into IFN_RISCV_SININT,
   which the RISC-V backend lowers to a single `sinint` instruction. */

extern long __custom_sinint(long a1, long a2);

long sinint_demo(long a1, long a2)
{
    return __custom_sinint(a1, a2);
}
