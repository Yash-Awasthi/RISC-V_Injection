"""
RISC-V Instruction Encoder — Instruction encoding/decoding and register allocation
Inspired by riscv-gnu-toolchain, riscv-isa-sim
"""

from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from enum import Enum


class RegisterType(Enum):
    INTEGER = "integer"
    FLOAT = "float"
    VECTOR = "vector"


@dataclass
class Register:
    number: int
    name: str
    reg_type: RegisterType
    ABI_NAME: str = ""
    callee_saved: bool = False


@dataclass
class Instruction:
    mnemonic: str
    rd: Optional[int] = None
    rs1: Optional[int] = None
    rs2: Optional[int] = None
    rs3: Optional[int] = None
    imm: Optional[int] = None
    funct3: int = 0
    funct7: int = 0
    opcode: int = 0


# RISC-V base integer registers
REGISTERS = [
    Register(0, "x0", RegisterType.INTEGER, "zero", False),
    Register(1, "x1", RegisterType.INTEGER, "ra", False),
    Register(2, "x2", RegisterType.INTEGER, "sp", False),
    Register(3, "x3", RegisterType.INTEGER, "gp", False),
    Register(4, "x4", RegisterType.INTEGER, "tp", False),
    Register(5, "x5", RegisterType.INTEGER, "t0", False),
    Register(6, "x6", RegisterType.INTEGER, "t1", False),
    Register(7, "x7", RegisterType.INTEGER, "t2", False),
    Register(8, "x8", RegisterType.INTEGER, "s0", True),
    Register(9, "x9", RegisterType.INTEGER, "s1", True),
    Register(10, "x10", RegisterType.INTEGER, "a0", False),
    Register(11, "x11", RegisterType.INTEGER, "a1", False),
    Register(12, "x12", RegisterType.INTEGER, "a2", False),
    Register(13, "x13", RegisterType.INTEGER, "a3", False),
    Register(14, "x14", RegisterType.INTEGER, "a4", False),
    Register(15, "x15", RegisterType.INTEGER, "a5", False),
    Register(16, "x16", RegisterType.INTEGER, "a6", False),
    Register(17, "x17", RegisterType.INTEGER, "a7", False),
    Register(18, "x18", RegisterType.INTEGER, "s2", True),
    Register(19, "x19", RegisterType.INTEGER, "s3", True),
    Register(20, "x20", RegisterType.INTEGER, "s4", True),
    Register(21, "x21", RegisterType.INTEGER, "s5", True),
    Register(22, "x22", RegisterType.INTEGER, "s6", True),
    Register(23, "x23", RegisterType.INTEGER, "s7", True),
    Register(24, "x24", RegisterType.INTEGER, "s8", True),
    Register(25, "x25", RegisterType.INTEGER, "s9", True),
    Register(26, "x26", RegisterType.INTEGER, "s10", True),
    Register(27, "x27", RegisterType.INTEGER, "s11", True),
    Register(28, "x28", RegisterType.INTEGER, "t3", False),
    Register(29, "x29", RegisterType.INTEGER, "t4", False),
    Register(30, "x30", RegisterType.INTEGER, "t5", False),
    Register(31, "x31", RegisterType.INTEGER, "t6", False),
]


class InstructionEncoder:
    """RISC-V instruction encoding and decoding."""

    # Opcodes
    OP_R_TYPE = 0b0110011
    OP_I_TYPE = 0b0010011
    OP_LOAD = 0b0000011
    OP_STORE = 0b0100011
    OP_BRANCH = 0b1100011
    OP_JAL = 0b1101111
    OP_JALR = 0b1100111
    OP_LUI = 0b0110111
    OP_AUIPC = 0b0010111

    @staticmethod
    def encode_r_type(funct7: int, rs2: int, rs1: int, funct3: int, rd: int, opcode: int) -> int:
        return ((funct7 & 0x7F) << 25 | (rs2 & 0x1F) << 20 | (rs1 & 0x1F) << 15 |
                (funct3 & 0x7) << 12 | (rd & 0x1F) << 7 | (opcode & 0x7F))

    @staticmethod
    def encode_i_type(imm: int, rs1: int, funct3: int, rd: int, opcode: int) -> int:
        return ((imm & 0xFFF) << 20 | (rs1 & 0x1F) << 15 |
                (funct3 & 0x7) << 12 | (rd & 0x1F) << 7 | (opcode & 0x7F))

    @staticmethod
    def encode_s_type(imm: int, rs2: int, rs1: int, funct3: int, opcode: int) -> int:
        imm11_5 = (imm >> 5) & 0x7F
        imm4_0 = imm & 0x1F
        return ((imm11_5) << 25 | (rs2 & 0x1F) << 20 | (rs1 & 0x1F) << 15 |
                (funct3 & 0x7) << 12 | (imm4_0) << 7 | (opcode & 0x7F))

    @staticmethod
    def encode_b_type(imm: int, rs2: int, rs1: int, funct3: int, opcode: int) -> int:
        b12 = (imm >> 12) & 1
        b11 = (imm >> 11) & 1
        b10_5 = (imm >> 5) & 0x3F
        b4_1 = (imm >> 1) & 0xF
        return ((b12) << 31 | (b10_5) << 25 | (rs2 & 0x1F) << 20 | (rs1 & 0x1F) << 15 |
                (funct3 & 0x7) << 12 | (b4_1) << 8 | (b11) << 7 | (opcode & 0x7F))

    @staticmethod
    def encode_u_type(imm: int, rd: int, opcode: int) -> int:
        return ((imm & 0xFFFFF) << 12 | (rd & 0x1F) << 7 | (opcode & 0x7F))

    @staticmethod
    def encode_j_type(imm: int, rd: int, opcode: int) -> int:
        b20 = (imm >> 20) & 1
        b10_1 = (imm >> 1) & 0x3FF
        b11 = (imm >> 11) & 1
        b19_12 = (imm >> 12) & 0xFF
        return ((b20) << 31 | (b10_1) << 21 | (b11) << 20 | (b19_12) << 12 |
                (rd & 0x1F) << 7 | (opcode & 0x7F))

    @classmethod
    def encode_add(cls, rd: int, rs1: int, rs2: int) -> int:
        return cls.encode_r_type(0x00, rs2, rs1, 0x0, rd, cls.OP_R_TYPE)

    @classmethod
    def encode_addi(cls, rd: int, rs1: int, imm: int) -> int:
        return cls.encode_i_type(imm, rs1, 0x0, rd, cls.OP_I_TYPE)

    @classmethod
    def encode_lw(cls, rd: int, rs1: int, imm: int) -> int:
        return cls.encode_i_type(imm, rs1, 0x2, rd, cls.OP_LOAD)

    @classmethod
    def encode_sw(cls, rs2: int, rs1: int, imm: int) -> int:
        return cls.encode_s_type(imm, rs2, rs1, 0x2, cls.OP_STORE)

    @classmethod
    def encode_beq(cls, rs1: int, rs2: int, imm: int) -> int:
        return cls.encode_b_type(imm, rs2, rs1, 0x0, cls.OP_BRANCH)

    @classmethod
    def encode_jal(cls, rd: int, imm: int) -> int:
        return cls.encode_j_type(imm, rd, cls.OP_JAL)

    @classmethod
    def encode_lui(cls, rd: int, imm: int) -> int:
        return cls.encode_u_type(imm >> 12, rd, cls.OP_LUI)


class RegisterAllocator:
    """Simple register allocator for RISC-V."""

    def __init__(self):
        self.available_temp = list(range(5, 8)) + list(range(28, 32))
        self.available_args = list(range(10, 18))
        self.available_saved = list(range(8, 10)) + list(range(18, 28))
        self.allocated = {}

    def allocate_temp(self) -> int:
        if not self.available_temp:
            raise RuntimeError("No temporary registers available")
        reg = self.available_temp.pop(0)
        self.allocated[reg] = "temp"
        return reg

    def allocate_arg(self) -> int:
        if not self.available_args:
            raise RuntimeError("No argument registers available")
        reg = self.available_args.pop(0)
        self.allocated[reg] = "arg"
        return reg

    def allocate_saved(self) -> int:
        if not self.available_saved:
            raise RuntimeError("No saved registers available")
        reg = self.available_saved.pop(0)
        self.allocated[reg] = "saved"
        return reg

    def free(self, reg: int):
        if reg in self.allocated:
            del self.allocated[reg]
            if 5 <= reg <= 7 or 28 <= reg <= 31:
                self.available_temp.append(reg)
            elif 10 <= reg <= 17:
                self.available_args.append(reg)
            elif 8 <= reg <= 9 or 18 <= reg <= 27:
                self.available_saved.append(reg)

    def get_callee_saved_regs(self) -> List[int]:
        return [reg for reg, _ in self.allocated.items()
                if REGISTERS[reg].callee_saved]

    def get_usage_summary(self) -> Dict:
        return {
            "allocated": len(self.allocated),
            "available_temp": len(self.available_temp),
            "available_args": len(self.available_args),
            "available_saved": len(self.available_saved),
            "by_type": {
                "temp": sum(1 for v in self.allocated.values() if v == "temp"),
                "arg": sum(1 for v in self.allocated.values() if v == "arg"),
                "saved": sum(1 for v in self.allocated.values() if v == "saved"),
            }
        }
