/*
 * fail_loop-missing.c — Failure case: SDPA body written without any loops.
 *
 * Failure category : structural — no loops in the function
 * Matcher gate hit : `if (number_of_loops (fun) <= 1) return 0;`  (execute())
 *                    and `loop_depth (loop) != 1` filter (no top-level loops).
 *
 * WHY THIS FAILS
 *   attnrec is, by design, a *loop* recognizer.  It walks the loop tree of
 *   the function and inspects each top-level loop nest.  If the function
 *   contains zero (non-root) loops — for example because the programmer
 *   hand-unrolled the body, or wrote the whole thing as straight-line code —
 *   the pass exits immediately without touching anything.
 *
 *   Here the attention body is fully unrolled for a tiny N=D=2 to make the
 *   point unambiguously: there is exactly one basic block, no PHIs, no
 *   back-edges.  attnrec has nothing to recognize.
 *
 * HOW TO MAKE THIS PASS
 *   Re-express the body as the canonical fused loop nest with at least one
 *   outer loop over i and a nested inner reduction — see
 *   `../sdpa_test.c` for the shape that satisfies every check.
 */

#include <math.h>

void sdpa_unrolled (float Q[2][2], float K[2][2], float V[2][2], float O[2][2])
{
    float scale = 1.0f / sqrtf (2.0f);

    /* No loops.  Hand-unrolled QK^T, softmax and SV for the 2x2 case.
       attnrec walks the loop tree, finds nothing at depth 1, returns 0. */
    float s00 = (Q[0][0]*K[0][0] + Q[0][1]*K[0][1]) * scale;
    float s01 = (Q[0][0]*K[1][0] + Q[0][1]*K[1][1]) * scale;
    float s10 = (Q[1][0]*K[0][0] + Q[1][1]*K[0][1]) * scale;
    float s11 = (Q[1][0]*K[1][0] + Q[1][1]*K[1][1]) * scale;

    float e00 = expf (s00), e01 = expf (s01);
    float e10 = expf (s10), e11 = expf (s11);
    float r0 = 1.0f / (e00 + e01);
    float r1 = 1.0f / (e10 + e11);
    e00 *= r0; e01 *= r0;
    e10 *= r1; e11 *= r1;

    O[0][0] = e00*V[0][0] + e01*V[1][0];
    O[0][1] = e00*V[0][1] + e01*V[1][1];
    O[1][0] = e10*V[0][0] + e11*V[1][0];
    O[1][1] = e10*V[0][1] + e11*V[1][1];
}
