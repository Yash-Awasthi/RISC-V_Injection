/*
 * fail_main-not-nested.c — Failure case: the SDPA body is written
 * directly inside `main()` as a single flat loop, with no outer +
 * inner nest structure.
 *
 * Failure category : structural — outer loop has no inner loop
 * Matcher gate hit : `if (!outer->inner) return false;` in `attn_match`
 *                    ";; attnrec: loop N rejected — no inner loop".
 *
 * WHY THIS FAILS
 *   attnrec's per-loop entry point only considers loops at
 *   `loop_depth (loop) == 1` *and* requires `loop->inner` to be
 *   non-NULL.  Attention is intrinsically a nested computation —
 *   the outer-i loop must contain (transitively) the inner madd
 *   reduction in d.
 *
 *   In this file the programmer has linearised the three matrices so
 *   that the whole thing fits in a single flat loop over a precomputed
 *   index space.  There is exactly one loop, no inner — perfect for
 *   readability, fatal for the recogniser.
 *
 *   Note also that calling this from `main()` is *not* what causes
 *   the failure — the matcher is function-agnostic.  Putting the code
 *   in `main()` here is deliberate: it documents that "writing in main"
 *   is not by itself sufficient to disable the pass; the *shape* of
 *   the loop is what matters.  Try moving the body into a helper
 *   function with a proper nest and the pass fires immediately.
 *
 * HOW TO MAKE THIS PASS
 *   Re-introduce the outer i-loop and the inner d/j-loops as separate
 *   nested `for`s, exactly as `../sdpa_test.c` does.  A single flat
 *   loop, even if it computes the right values, will never trigger
 *   attnrec.
 */

#include <math.h>
#include <stdio.h>

#define N 8
#define D 4

static float Q[N*D], K[N*D], V[N*D], O[N*D], S[N];

int main (void)
{
    float scale = 1.0f / sqrtf ((float) D);

    /* One flat loop covering all i*j*d work — no nesting at all.
       attn_match sees outer->inner == NULL and rejects.            */
    int total = N * N;
    for (int t = 0; t < total; t++)
    {
        int i = t / N;
        int j = t % N;
        float acc = 0.f;
        for (int d = 0; d < D; d++) acc += Q[i*D + d] * K[j*D + d];
        S[j] = expf (acc * scale);
        if (j == N - 1)
        {
            float sum = 0.f;
            for (int k = 0; k < N; k++) sum += S[k];
            for (int k = 0; k < N; k++) S[k] /= sum;
            for (int d = 0; d < D; d++)
            {
                float a = 0.f;
                for (int k = 0; k < N; k++) a += S[k] * V[k*D + d];
                O[i*D + d] = a;
            }
        }
    }

    printf ("%f\n", (double) O[0]);
    return 0;
}
