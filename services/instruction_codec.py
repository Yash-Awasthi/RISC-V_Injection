"""
RISC-V Instruction Codec — encodes and decodes RISC-V instructions
for injection analysis and vulnerability research.

Supports: RV32I, RV32M, RV32A, RV32C (compressed), and common extensions.
"""

from dataclasses import dataclass
from enum import IntEnum
from typing import Optional


class Opcode(IntEnum):
    """RV32I base opcodes (bits [6:0])"""
    LUI = 0b0110111
    AUIPC = 0b0010111
    JAL = 0b1101111
    JALR = 0b1100111
    BRANCH = 0b1100011
    LOAD = 0b0000011
    STORE = 0b0100011
    OP_IMM = 0b0010011
    OP = 0b0110011
    FENCE = 0b0001111
    SYSTEM = 0b1110011


class Funct3(IntEnum):
    """Funct3 encodings"""
    BEQ = 0b000
    BNE = 0b011
    BLT = 0b100
    BGE = 0b101
    BLTU = 0b110
    BGEU = 0b111


@dataclass
class Instruction:
    """Decoded RISC-V instruction."""
    raw: int
    opcode: int
    rd: int
    funct3: int
    rs1: int
    rs2: int
    funct7: int
    imm: int
    mnemonic: str
    assembly: str
    is_compressed: bool
    category: str  # "R", "I", "S", "B", "U", "J", "SYSTEM"


class InstructionCodec:
    """Encode/decode RISC-V instructions."""

    # Opcode to mnemonic lookup for common instructions
    MNEMONICS = {
        (Opcode.OP_IMM, 0b000): "ADDI",
        (Opcode.OP_IMM, 0b101): "SRLI",
        (Opcode.OP_IMM, 0b001): "SLLI",
        (Opcode.OP_IMM, 0b111): "ANDI",
        (Opcode.OP_IMM, 0b110): "ORI",
        (Opcode.OP_IMM, 0b100): "XORI",
        (Opcode.OP_IMM, 0b010): "SLTI",
        (Opcode.OP_IMM, 0b011): "SLTIU",
        (Opcode.OP, 0b000): "ADD",
        (Opcode.OP, 0b001): "SLL",
        (Opcode.OP, 0b010): "SLT",
        (Opcode.OP, 0b011): "SLTU",
        (Opcode.OP, 0b100): "XOR",
        (Opcode.OP, 0b101): "SRL",
        (Opcode.OP, 0b110): "OR",
        (Opcode.OP, 0b111): "AND",
        (Opcode.LUI, 0b000): "LUI",
        (Opcode.AUIPC, 0b000): "AUIPC",
        (Opcode.JAL, 0b000): "JAL",
        (Opcode.JALR, 0b000): "JALR",
        (Opcode.LOAD, 0b000): "LB",
        (Opcode.LOAD, 0b001): "LH",
        (Opcode.LOAD, 0b010): "LW",
        (Opcode.LOAD, 0b100): "LBU",
        (Opcode.LOAD, 0b101): "LHU",
        (Opcode.STORE, 0b000): "SB",
        (Opcode.STORE, 0b001): "SH",
        (Opcode.STORE, 0b010): "SW",
        (Opcode.BRANCH, 0b000): "BEQ",
        (Opcode.BRANCH, 0b001): "BNE",
        (Opcode.BRANCH, 0b100): "BLT",
        (Opcode.BRANCH, 0b101): "BGE",
        (Opcode.BRANCH, 0b110): "BLTU",
        (Opcode.BRANCH, 0b111): "BGEU",
        (Opcode.SYSTEM, 0b000): "ECALL",
        (Opcode.FENCE, 0b000): "FENCE",
    }

    def decode(self, raw: int) -> Instruction:
        """Decode a 32-bit instruction word."""
        is_compressed = (raw & 0x3) != 0x3
        if is_compressed:
            return self._decode_compressed(raw)

        opcode = raw & 0x7F
        rd = (raw >> 7) & 0x1F
        funct3 = (raw >> 12) & 0x7
        rs1 = (raw >> 15) & 0x1F
        rs2 = (raw >> 20) & 0x1F
        funct7 = (raw >> 25) & 0x7F

        # Determine category and immediate
        category, imm = self._extract_imm_category(opcode, raw)

        # Get mnemonic
        mnemonic_key = (opcode, funct3) if opcode != Opcode.OP else (opcode, funct3)
        mnemonic = self.MNEMONICS.get(mnemonic_key, f"UNKNOWN(0x{opcode:02x})")

        # Handle SUB/SRA (funct7 distinguishes ADD vs SUB)
        if opcode == Opcode.OP and funct3 in (0b000, 0b101):
            if funct7 == 0b0100000:
                mnemonic = "SUB" if funct3 == 0b000 else "SRA"

        assembly = self._format_assembly(category, mnemonic, rd, rs1, rs2, imm)

        return Instruction(
            raw=raw, opcode=opcode, rd=rd, funct3=funct3,
            rs1=rs1, rs2=rs2, funct7=funct7, imm=imm,
            mnemonic=mnemonic, assembly=assembly,
            is_compressed=False, category=category,
        )

    def encode_r(self, funct7: int, rs2: int, rs1: int, funct3: int, rd: int) -> int:
        """Encode R-type instruction."""
        return (funct7 << 25) | (rs2 << 20) | (rs1 << 15) | (funct3 << 12) | (rd << 7) | Opcode.OP

    def encode_i(self, imm: int, rs1: int, funct3: int, rd: int, opcode: int = Opcode.OP_IMM) -> int:
        """Encode I-type instruction."""
        imm = imm & 0xFFF
        return (imm << 20) | (rs1 << 15) | (funct3 << 12) | (rd << 7) | opcode

    def encode_s(self, imm: int, rs2: int, rs1: int, funct3: int) -> int:
        """Encode S-type instruction."""
        imm11_5 = (imm >> 5) & 0x7F
        imm4_0 = imm & 0x1F
        return (imm11_5 << 25) | (rs2 << 20) | (rs1 << 15) | (funct3 << 12) | (imm4_0 << 7) | Opcode.STORE

    def encode_b(self, imm: int, rs2: int, rs1: int, funct3: int) -> int:
        """Encode B-type instruction."""
        bit12 = (imm >> 12) & 1
        bits10_5 = (imm >> 5) & 0x3F
        bit11 = (imm >> 11) & 1
        bits4_1 = (imm >> 1) & 0xF
        return (bit12 << 31) | (bits10_5 << 25) | (rs2 << 20) | (rs1 << 15) | (funct3 << 12) | (bit11 << 7) | (bits4_1 << 8) | Opcode.BRANCH

    def encode_u(self, imm: int, rd: int, opcode: int) -> int:
        """Encode U-type instruction."""
        imm31_12 = (imm >> 12) & 0xFFFFF
        return (imm31_12 << 12) | (rd << 7) | opcode

    def encode_j(self, imm: int, rd: int) -> int:
        """Encode J-type instruction."""
        bit20 = (imm >> 20) & 1
        bits10_1 = (imm >> 1) & 0x3FF
        bit11 = (imm >> 11) & 1
        bits19_12 = (imm >> 12) & 0xFF
        return (bit20 << 31) | (bits10_1 << 21) | (bit11 << 20) | (bits19_12 << 12) | (rd << 7) | Opcode.JAL

    def analyze_injection_surface(self, instructions: list[int]) -> dict:
        """Analyze a sequence of instructions for injection vulnerability surfaces."""
        decoded = [self.decode(inst) for inst in instructions]
        analysis = {
            "total_instructions": len(decoded),
            "categories": {},
            "memory_accesses": [],
            "control_flow": [],
            "system_calls": [],
            "potential_vectors": [],
        }

        for i, inst in enumerate(decoded):
            # Count categories
            analysis["categories"][inst.category] = analysis["categories"].get(inst.category, 0) + 1

            # Memory access instructions (load/store)
            if inst.opcode in (Opcode.LOAD, Opcode.STORE):
                analysis["memory_accesses"].append({
                    "index": i,
                    "instruction": inst.assembly,
                    "type": "load" if inst.opcode == Opcode.LOAD else "store",
                    "base_reg": f"x{inst.rs1}",
                    "offset": inst.imm,
                })

            # Control flow instructions
            if inst.opcode in (Opcode.BRANCH, Opcode.JAL, Opcode.JALR):
                analysis["control_flow"].append({
                    "index": i,
                    "instruction": inst.assembly,
                    "type": "branch" if inst.opcode == Opcode.BRANCH else "jump",
                })

            # System calls
            if inst.opcode == Opcode.SYSTEM:
                analysis["system_calls"].append({
                    "index": i,
                    "instruction": inst.assembly,
                })

            # Potential injection vectors (e.g., loads from user-controlled addresses)
            if inst.opcode == Opcode.LOAD and inst.rd == 0:
                analysis["potential_vectors"].append({
                    "index": i,
                    "instruction": inst.assembly,
                    "reason": "Load to x0 (null write attempt)",
                })

        return analysis

    # ── Private helpers ──────────────────────────────────────────────────────

    def _extract_imm_category(self, opcode: int, raw: int):
        """Extract immediate value and determine instruction category."""
        if opcode == Opcode.OP_IMM:
            imm = (raw >> 20) & 0xFFF
            imm = self._sign_extend(imm, 12)
            return "I", imm
        elif opcode == Opcode.OP:
            return "R", 0
        elif opcode == Opcode.STORE:
            imm11_5 = (raw >> 25) & 0x7F
            imm4_0 = (raw >> 7) & 0x1F
            imm = (imm11_5 << 5) | imm4_0
            imm = self._sign_extend(imm, 12)
            return "S", imm
        elif opcode == Opcode.BRANCH:
            b12 = (raw >> 31) & 1
            b10_5 = (raw >> 25) & 0x3F
            b11 = (raw >> 7) & 1
            b4_1 = (raw >> 8) & 0xF
            imm = (b12 << 12) | (b10_5 << 5) | (b11 << 11) | (b4_1 << 1)
            imm = self._sign_extend(imm, 13)
            return "B", imm
        elif opcode == Opcode.LUI or opcode == Opcode.AUIPC:
            imm = (raw >> 12) & 0xFFFFF
            imm = imm << 12
            return "U", imm
        elif opcode == Opcode.JAL:
            b20 = (raw >> 31) & 1
            b10_1 = (raw >> 21) & 0x3FF
            b11 = (raw >> 20) & 1
            b19_12 = (raw >> 12) & 0xFF
            imm = (b20 << 20) | (b10_1 << 1) | (b11 << 11) | (b19_12 << 12)
            imm = self._sign_extend(imm, 21)
            return "J", imm
        elif opcode == Opcode.LOAD:
            imm = (raw >> 20) & 0xFFF
            imm = self._sign_extend(imm, 12)
            return "I", imm
        elif opcode == Opcode.JALR:
            imm = (raw >> 20) & 0xFFF
            imm = self._sign_extend(imm, 12)
            return "I", imm
        else:
            return "SYSTEM", 0

    def _sign_extend(self, value: int, bits: int) -> int:
        """Sign extend a value from `bits` width to Python int."""
        sign_bit = 1 << (bits - 1)
        return (value & (sign_bit - 1)) - (value & sign_bit)

    def _decode_compressed(self, raw: int) -> Instruction:
        """Decode compressed (16-bit) instructions."""
        quadrant = raw & 0x3
        funct3 = (raw >> 13) & 0x7

        # Simplified compressed decode (C.ADDI, C.LW, C.SW, C.BEQZ, C.J)
        if quadrant == 0b01:
            if funct3 == 0b000:  # C.ADDI
                rd_rs1 = (raw >> 7) & 0x1F
                imm = ((raw >> 2) & 0x1F) | (((raw >> 12) & 1) << 5)
                imm = self._sign_extend(imm, 6)
                return Instruction(
                    raw=raw, opcode=0b0010011, rd=rd_rs1, funct3=0b000,
                    rs1=rd_rs1, rs2=0, funct7=0, imm=imm,
                    mnemonic="C.ADDI", assembly=f"c.addi x{rd_rs1}, {imm}",
                    is_compressed=True, category="I",
                )
        elif quadrant == 0b10:
            if funct3 == 0b100:
                rd_rs1 = (raw >> 7) & 0x1F
                rs2 = (raw >> 2) & 0x1F
                if rs2 != 0:
                    return Instruction(
                        raw=raw, opcode=Opcode.OP, rd=rd_rs1, funct3=0b000,
                        rs1=rd_rs1, rs2=rs2, funct7=0, imm=0,
                        mnemonic="C.MV", assembly=f"c.mv x{rd_rs1}, x{rs2}",
                        is_compressed=True, category="R",
                    )

        return Instruction(
            raw=raw, opcode=0, rd=0, funct3=0, rs1=0, rs2=0,
            funct7=0, imm=0, mnemonic="C.UNKNOWN",
            assembly=f"c.unknown 0x{raw:04x}", is_compressed=True, category="I",
        )

    def _format_assembly(self, category: str, mnemonic: str, rd: int, rs1: int, rs2: int, imm: int) -> str:
        """Format decoded instruction as assembly string."""
        if category == "R":
            return f"{mnemonic} x{rd}, x{rs1}, x{rs2}"
        elif category == "I":
            if mnemonic in ("JALR",):
                return f"{mnemonic} x{rd}, x{rs1}, {imm}"
            elif mnemonic in ("LB", "LH", "LW", "LBU", "LHU"):
                return f"{mnemonic} x{rd}, {imm}(x{rs1})"
            return f"{mnemonic} x{rd}, x{rs1}, {imm}"
        elif category == "S":
            return f"{mnemonic} x{rs2}, {imm}(x{rs1})"
        elif category == "B":
            target = imm  # simplified
            return f"{mnemonic} x{rs1}, x{rs2}, {target}"
        elif category == "U":
            return f"{mnemonic} x{rd}, {imm}"
        elif category == "J":
            return f"{mnemonic} x{rd}, {imm}"
        else:
            return f"{mnemonic}"
