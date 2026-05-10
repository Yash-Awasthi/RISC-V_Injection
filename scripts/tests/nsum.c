/* tests/nsum.c — verify that the compiler emits the `nsum` instruction.
   The pass should rewrite the loop into IFN_RISCV_NSUM(n) which
   computes n*(n-1)/2 in hardware (closed-form replacement). */

long nsum_demo(long n)
{
    long acc = 0;
    for (long i = 0; i < n; ++i)
        acc += i;
    return acc;
}
