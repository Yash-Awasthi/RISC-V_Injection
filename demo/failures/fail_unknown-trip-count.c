/*
 * fail_unknown-trip-count.c — Failure case: outer loop trip count is not
 * statically analyzable by SCEV.
 *
 * Failure category : analysis — outer-loop SCEV trip count is chrec_dont_know
 * Matcher gate hit : `if (n == chrec_dont_know) return false;`
 *                    ";; attnrec: loop N rejected — trip count unknown".
 *
 * WHY THIS FAILS
 *   After the load/store bases pass, `attn_match` asks SCEV for
 *   `number_of_latch_executions(outer)`.  If the loop's exit condition
 *   depends on a value that SCEV cannot bound at compile time — here, an
 *   external `volatile` length and a data-dependent early break — SCEV
 *   returns `chrec_dont_know` and the matcher bails out.
 *
 *   This is why `sdpa_test.c` uses `#define N 64` and a `for (i=0; i<N; ++i)`
 *   header: it gives the analyser a closed form for the latch count.
 *
 * HOW TO MAKE THIS PASS
 *   Replace the `volatile`/data-dependent exit with a plain
 *   `for (int i = 0; i < N; i++)` whose `N` is a compile-time constant
 *   or a function parameter with no early break.  Once SCEV can prove
 *   the latch count, this check is satisfied.
 */

#include <math.h>

#define D 32

extern volatile int g_n_runtime;     /* hides the value from the compiler */
extern int abort_flag (int i);

void sdpa_unknown_trip (float *Q, float *K, float *V, float *O)
{
    int n = g_n_runtime;             /* opaque at compile time */
    float scale = 1.0f / sqrtf ((float) D);

    /* Outer loop with two non-SCEV-friendly properties:
         1. trip bound is a volatile load (not a loop-invariant constant);
         2. a data-dependent early break makes the exit condition opaque.
       SCEV gives up and returns chrec_dont_know.                       */
    for (int i = 0; i < n; i++)
    {
        if (abort_flag (i)) break;

        float S[64];
        for (int j = 0; j < n; j++)
        {
            float acc = 0.f;
            for (int d = 0; d < D; d++)
                acc += Q[i*D + d] * K[j*D + d];
            S[j] = acc * scale;
        }

        float sum = 0.f;
        for (int j = 0; j < n; j++) { S[j] = expf (S[j]); sum += S[j]; }
        for (int j = 0; j < n; j++) S[j] /= sum;

        for (int d = 0; d < D; d++)
        {
            float acc = 0.f;
            for (int j = 0; j < n; j++)
                acc += S[j] * V[j*D + d];
            O[i*D + d] = acc;
        }
    }
}
