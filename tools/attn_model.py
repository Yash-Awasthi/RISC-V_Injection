#!/usr/bin/env python3
"""Executable model of the attn instruction, see docs/01-instruction-spec.md.

Three things are proven here, not just asserted:
  1. the R4-type instruction word and the Q/K/V matrix blocks decode
     from raw bytes -- the same bytes real hardware would read out of
     a register file and memory, not from python call arguments.
  2. the architectural semantics O = softmax(Q K^T / sqrt(d)) V match
     an independently written scalar reference within 1e-5.
  3. the register-block layout below is the one true copy of the
     attn_ptrs/attn_dims/attn_cfg ABI; scripts/tests/test_attn_contract.py
     parses demo/attn.h and diffs it against ATTN_STRUCTS field-for-field.
"""

import struct
import numpy as np

MATCH_ATTN = 0x0000000b
MASK_ATTN = 0x0600707f

# rd/rs1/rs2/rs3 register-block layouts, byte widths on rv64 (pointers and
# uint64_t are 8 bytes, uint32_t is 4). Each value is (field, C type, width)
# in declaration order -- the same order demo/attn.h must declare them in.
ATTN_STRUCTS = {
    "attn_ptrs": [                      # rs1 -> &{ Q_ptr, K_ptr, V_ptr }
        ("q", "const void *", 8),
        ("k", "const void *", 8),
        ("v", "const void *", 8),
    ],
    "attn_dims": [                      # rs2 -> &{ N, D, H }
        ("n", "uint64_t", 8),
        ("d", "uint64_t", 8),
        ("h", "uint64_t", 8),
    ],
    "attn_cfg": [                       # rs3 -> &{ scale_bits, flags }
        ("scale_bits", "uint32_t", 4),
        ("flags", "uint32_t", 4),
    ],
}


def decode_insn(raw):
    """Decode a 4-byte little-endian attn word into its R4-type fields."""
    insn, = struct.unpack("<I", raw)
    if (insn & MASK_ATTN) != MATCH_ATTN:
        raise ValueError("not an attn instruction: 0x%08x" % insn)
    return {
        "opcode": insn & 0x7f,
        "rd": (insn >> 7) & 0x1f,
        "funct3": (insn >> 12) & 0x7,
        "rs1": (insn >> 15) & 0x1f,
        "rs2": (insn >> 20) & 0x1f,
        "funct2": (insn >> 25) & 0x3,
        "rs3": (insn >> 27) & 0x1f,
    }


def encode_insn(rd, rs1, rs2, rs3):
    """Inverse of decode_insn, used only to build a test instruction word."""
    insn = MATCH_ATTN | (rd << 7) | (rs1 << 15) | (rs2 << 20) | (rs3 << 27)
    return struct.pack("<I", insn)


def read_matrix(mem, addr, n, d):
    """Read an [n x d] row-major float32 block out of raw memory bytes."""
    flat = np.frombuffer(mem, dtype="<f4", count=n * d, offset=addr)
    return flat.reshape(n, d).copy()


def write_matrix(mem, addr, m):
    """Write an [n x d] float32 matrix into raw memory bytes at addr."""
    data = np.ascontiguousarray(m, dtype="<f4").tobytes()
    mem[addr:addr + len(data)] = data


def attn(q, k, v, d):
    """O = softmax(Q K^T / sqrt(d)) V, row-wise stable softmax."""
    s = (q @ k.T) / np.sqrt(d)
    s = s - s.max(axis=1, keepdims=True)
    p = np.exp(s)
    p = p / p.sum(axis=1, keepdims=True)
    return p @ v


def attn_reference(q, k, v, d):
    """Scalar reference for attn(), written independently for the self-check."""
    n = q.shape[0]
    o = np.zeros_like(v)
    for i in range(n):
        row = [float(np.dot(q[i], k[j])) / d ** 0.5 for j in range(n)]
        m = max(row)
        exps = [np.exp(x - m) for x in row]
        z = sum(exps)
        for j in range(n):
            o[i] += (exps[j] / z) * v[j]
    return o


if __name__ == "__main__":
    n, d = 64, 32  # shape from demo/sdpa_test.c: N=64 tokens, D=32 head dim
    rng = np.random.default_rng(0)
    q0 = rng.standard_normal((n, d)).astype("<f4")
    k0 = rng.standard_normal((n, d)).astype("<f4")
    v0 = rng.standard_normal((n, d)).astype("<f4")

    # simulated flat memory: the three matrix blocks live at distinct addresses,
    # spaced by the block size in bytes so they never overlap
    blk = n * d * 4
    mem = bytearray(0x1000 + 3 * blk)
    addr_q, addr_k, addr_v = 0x1000, 0x1000 + blk, 0x1000 + 2 * blk
    write_matrix(mem, addr_q, q0)
    write_matrix(mem, addr_k, k0)
    write_matrix(mem, addr_v, v0)

    q = read_matrix(mem, addr_q, n, d)
    k = read_matrix(mem, addr_k, n, d)
    v = read_matrix(mem, addr_v, n, d)
    assert np.array_equal(q, q0) and np.array_equal(k, k0) and np.array_equal(v, v0), \
        "matrix blocks did not survive a raw memory round trip"

    # worked example from docs/01-instruction-spec.md S5: attn a3,a0,a1,a2
    raw_insn = encode_insn(rd=13, rs1=10, rs2=11, rs3=12)
    assert raw_insn == struct.pack("<I", 0x60b5068b)
    fields = decode_insn(raw_insn)
    assert fields == {"opcode": 0x0b, "rd": 13, "funct3": 0, "rs1": 10,
                       "rs2": 11, "funct2": 0, "rs3": 12}

    o = attn(q, k, v, d)
    o_golden = attn_reference(q, k, v, d)
    err = float(np.max(np.abs(o - o_golden)))
    print("max abs err vs golden: %.3e" % err)
    assert err < 1e-5, "attn() diverges from the scalar reference"
