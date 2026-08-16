/*
 * attn.h — C-side ABI for the RISC-V `attn` scaled dot-product
 *          attention instruction.
 *
 *     attn  rd, rs1, rs2, rs3
 *       rd  = O_ptr                     output matrix, written by hardware
 *       rs1 = &{ Q_ptr, K_ptr, V_ptr }   attn_ptrs block
 *       rs2 = &{ N, D, H }               attn_dims block
 *       rs3 = &{ scale_bits, flags }     attn_cfg  block
 *
 * The three register blocks are plain C structs. attn_sdpa() below
 * fills them on the stack and hands their addresses to the builtin —
 * no compiler pass builds or knows about this layout. This header
 * IS the ABI; the model in tools/attn_model.py is checked against it
 * field-for-field by scripts/tests/test_attn_contract.py.
 *
 * Q, K, V, O are row-major float32 matrices, one head at a time
 * concatenated: matrix[head][token][dim], N*D floats per head.
 */

#ifndef RISCV_INJECTION_ATTN_H
#define RISCV_INJECTION_ATTN_H

#include <stdint.h>
#include <string.h>

#if !defined(__riscv)
#include <math.h>
#endif

/* rs1 -> &{ Q_ptr, K_ptr, V_ptr } */
struct attn_ptrs
{
  const void *q;
  const void *k;
  const void *v;
};

/* rs2 -> &{ N, D, H } */
struct attn_dims
{
  uint64_t n;
  uint64_t d;
  uint64_t h;
};

/* rs3 -> &{ scale_bits, flags }  scale is 1/sqrt(D) as raw float32 bits */
struct attn_cfg
{
  uint32_t scale_bits;
  uint32_t flags;
};

#if defined(__riscv)

static inline void
attn_sdpa (const float *q, const float *k, const float *v, float *o,
           uint64_t n, uint64_t d, uint64_t h, float scale)
{
  struct attn_ptrs ptrs = { q, k, v };
  struct attn_dims dims = { n, d, h };
  struct attn_cfg cfg;

  memcpy (&cfg.scale_bits, &scale, sizeof (cfg.scale_bits));
  cfg.flags = 0;

  __builtin_riscv_attn (o, &ptrs, &dims, &cfg);
}

#else /* !__riscv — no hardware, no builtin: portable reference body */

static inline void
attn_sdpa (const float *q, const float *k, const float *v, float *o,
           uint64_t n, uint64_t d, uint64_t h, float scale)
{
  uint64_t head_stride = n * d;

  for (uint64_t hh = 0; hh < h; hh++)
    {
      const float *qh = q + hh * head_stride;
      const float *kh = k + hh * head_stride;
      const float *vh = v + hh * head_stride;
      float *oh = o + hh * head_stride;

      for (uint64_t i = 0; i < n; i++)
        {
          float s[n];
          float sum = 0.f;

          for (uint64_t j = 0; j < n; j++)
            {
              float acc = 0.f;
              for (uint64_t dd = 0; dd < d; dd++)
                acc += qh[i * d + dd] * kh[j * d + dd];
              s[j] = acc * scale;
            }

          for (uint64_t j = 0; j < n; j++)
            { s[j] = expf (s[j]); sum += s[j]; }
          for (uint64_t j = 0; j < n; j++)
            s[j] /= sum;

          for (uint64_t dd = 0; dd < d; dd++)
            {
              float acc = 0.f;
              for (uint64_t j = 0; j < n; j++)
                acc += s[j] * vh[j * d + dd];
              oh[i * d + dd] = acc;
            }
        }
    }
}

#endif /* __riscv */

#endif /* RISCV_INJECTION_ATTN_H */
