/* tests/fds.c — verify that the compiler emits the `fds` instruction.
   The pass should rewrite (a / b) - c   into   IFN_RISCV_FDS(a, b, c). */

long fds_demo(long a, long b, long c)
{
    /* Use long (DI) so it matches the (define_insn ... DI ... ) we generate. */
    return (a / b) - c;
}
