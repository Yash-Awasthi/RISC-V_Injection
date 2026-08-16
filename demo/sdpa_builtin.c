/*
 * sdpa_builtin.c — scaled dot-product attention via the explicit
 *                  attn_sdpa() wrapper, no pattern matcher involved.
 *
 * Compare with sdpa_test.c, which relies on the attnrec pass to find
 * attention in a hand-fused loop nest. This file states the operation
 * directly, so there is no shape for a matcher to get wrong.
 *
 * Compile command:
 *   riscv64-unknown-elf-gcc -mattn -O2 -S sdpa_builtin.c -o sdpa_builtin.s
 */

#include <math.h>
#include "attn.h"

#define N 64    /* sequence length — number of tokens         */
#define D 32    /* head dimension  — embedding size per head  */
#define H 1     /* single head                                 */

static float q[N * D], k[N * D], v[N * D], o[N * D];

int
main (void)
{
  float scale = 1.0f / sqrtf ((float) D);

  attn_sdpa (q, k, v, o, N, D, H, scale);
  return 0;
}
