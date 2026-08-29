"""
RISC-V Compiler Toolchain Patterns
Inspired by low-level-dev-skills - instruction encoding, custom ISA extensions, cross-compilation

Pure functions for:
- RISC-V instruction encoding/decoding
- Custom ISA extension design
- Cross-compilation setup
- Binary analysis
"""

from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple
from enum import Enum
import struct


class InstructionFormat(Enum):
    """RISC-V instruction formats"""
    R = "R"  # Register-register
    I = "I"  # Immediate
    S = "S"  # Store
    B = "B"  # Branch
    U = "U"  # Upper immediate
    J = "J"  # Jump


@dataclass
class Instruction:
    """RISC-V instruction representation"""
    mnemonic: str
    format: InstructionFormat
    opcode: int
    funct3: Optional[int]
    funct7: Optional[int]
    rd: Optional[int]  # Destination register
    rs1: Optional[int]  # Source register 1
    rs2: Optional[int]  # Source register 2
    imm: Optional[int]  # Immediate value
    description: str


@dataclass
class ISAExtension:
    """Custom ISA extension definition"""
    name: str
    version: str
    description: str
    instructions: List[Instruction]
    registers: List[str]
    CSRs: List[str]  # Control and Status Registers


@dataclass
class CompilationTarget:
    """Cross-compilation target configuration"""
    architecture: str  # e.g., "riscv32", "riscv64"
    abi: str  # e.g., "ilp32", "lp64"
    extensions: List[str]  # e.g., ["rv32im", "rv64gc"]
    march: str  # e.g., "rv32imac"
    mabi: str  # e.g., "ilp32d"


class RISCVEncoder:
    """RISC-V instruction encoder"""
    
    # Base opcodes
    OPCODES = {
        'LUI': 0b0110111,
        'AUIPC': 0b0010111,
        'JAL': 0b1101111,
        'JALR': 0b1100111,
        'BRANCH': 0b1100011,
        'LOAD': 0b0000011,
        'STORE': 0b0100011,
        'OP_IMM': 0b0010011,
        'OP': 0b0110011,
        'FENCE': 0b0001111,
        'SYSTEM': 0b1110011,
    }
    
    # Function codes
    FUNCT3 = {
        'ADD': 0b000,
        'SUB': 0b000,
        'SLL': 0b001,
        'SLT': 0b010,
        'SLTU': 0b011,
        'XOR': 0b100,
        'SRL': 0b101,
        'SRA': 0b101,
        'OR': 0b110,
        'AND': 0b111,
    }
    
    FUNCT7 = {
        'ADD': 0b0000000,
        'SUB': 0b0100000,
        'SRA': 0b0100000,
    }
    
    @staticmethod
    def encode_r_type(mnemonic: str, rd: int, rs1: int, rs2: int) -> int:
        """
        Encode R-type instruction
        
        Format: funct7[31:25] | rs2[24:20] | rs1[19:15] | funct3[14:12] | rd[11:7] | opcode[6:0]
        """
        opcode = RISCVEncoder.OPCODES['OP']
        funct3 = RISCVEncoder.FUNCT3.get(mnemonic, 0)
        funct7 = RISCVEncoder.FUNCT7.get(mnemonic, 0)
        
        instruction = (funct7 << 25) | (rs2 << 20) | (rs1 << 15) | (funct3 << 12) | (rd << 7) | opcode
        return instruction
    
    @staticmethod
    def encode_i_type(mnemonic: str, rd: int, rs1: int, imm: int) -> int:
        """
        Encode I-type instruction
        
        Format: imm[31:20] | rs1[19:15] | funct3[14:12] | rd[11:7] | opcode[6:0]
        """
        opcode = RISCVEncoder.OPCODES['OP_IMM']
        funct3 = RISCVEncoder.FUNCT3.get(mnemonic, 0)
        
        # Sign extend immediate to 12 bits
        imm = imm & 0xFFF
        
        instruction = (imm << 20) | (rs1 << 15) | (funct3 << 12) | (rd << 7) | opcode
        return instruction
    
    @staticmethod
    def encode_s_type(mnemonic: str, rs1: int, rs2: int, imm: int) -> int:
        """
        Encode S-type instruction
        
        Format: imm[31:25] | rs2[24:20] | rs1[19:15] | funct3[14:12] | imm[11:7] | opcode[6:0]
        """
        opcode = RISCVEncoder.OPCODES['STORE']
        funct3 = RISCVEncoder.FUNCT3.get(mnemonic, 0)
        
        # Split immediate
        imm_11_5 = (imm >> 5) & 0x7F
        imm_4_0 = imm & 0x1F
        
        instruction = (imm_11_5 << 25) | (rs2 << 20) | (rs1 << 15) | (funct3 << 12) | (imm_4_0 << 7) | opcode
        return instruction
    
    @staticmethod
    def encode_b_type(mnemonic: str, rs1: int, rs2: int, imm: int) -> int:
        """
        Encode B-type instruction
        
        Format: imm[31|30:25] | rs2[24:20] | rs1[19:15] | funct3[14:12] | imm[11|8:7] | imm[4:1|11] | opcode[6:0]
        """
        opcode = RISCVEncoder.OPCODES['BRANCH']
        funct3 = RISCVEncoder.FUNCT3.get(mnemonic, 0)
        
        # Split immediate according to B-type format
        imm_12 = (imm >> 12) & 1
        imm_10_5 = (imm >> 5) & 0x3F
        imm_4_1 = (imm >> 1) & 0xF
        imm_11 = (imm >> 11) & 1
        
        instruction = (imm_12 << 31) | (imm_10_5 << 25) | (rs2 << 20) | (rs1 << 15) | (funct3 << 12) | (imm_11 << 7) | (imm_4_1 << 1) | opcode
        return instruction
    
    @staticmethod
    def encode_u_type(mnemonic: str, rd: int, imm: int) -> int:
        """
        Encode U-type instruction
        
        Format: imm[31:12] | rd[11:7] | opcode[6:0]
        """
        opcode = RISCVEncoder.OPCODES.get(mnemonic, 0)
        
        # Upper 20 bits of immediate
        imm_upper = (imm >> 12) & 0xFFFFF
        
        instruction = (imm_upper << 12) | (rd << 7) | opcode
        return instruction
    
    @staticmethod
    def encode_j_type(mnemonic: str, rd: int, imm: int) -> int:
        """
        Encode J-type instruction
        
        Format: imm[31|19:12] | rd[11:7] | imm[20|10:1|11] | opcode[6:0]
        """
        opcode = RISCVEncoder.OPCODES['JAL']
        
        # Split immediate according to J-type format
        imm_20 = (imm >> 20) & 1
        imm_10_1 = (imm >> 1) & 0x3FF
        imm_11 = (imm >> 11) & 1
        imm_19_12 = (imm >> 12) & 0xFF
        
        instruction = (imm_20 << 31) | (imm_19_12 << 12) | (rd << 7) | (imm_11 << 20) | (imm_10_1 << 21) | opcode
        return instruction


class RISCVDecoder:
    """RISC-V instruction decoder"""
    
    @staticmethod
    def decode(instruction: int) -> Instruction:
        """
        Decode a 32-bit RISC-V instruction
        
        Args:
            instruction: 32-bit instruction word
        
        Returns:
            Decoded Instruction object
        """
        opcode = instruction & 0x7F
        
        # Determine format and decode
        if opcode == RISCVEncoder.OPCODES['OP']:
            return RISCVDecoder._decode_r_type(instruction)
        elif opcode == RISCVEncoder.OPCODES['OP_IMM']:
            return RISCVDecoder._decode_i_type(instruction)
        elif opcode == RISCVEncoder.OPCODES['LOAD']:
            return RISCVDecoder._decode_i_type(instruction)
        elif opcode == RISCVEncoder.OPCODES['STORE']:
            return RISCVDecoder._decode_s_type(instruction)
        elif opcode == RISCVEncoder.OPCODES['BRANCH']:
            return RISCVDecoder._decode_b_type(instruction)
        elif opcode == RISCVEncoder.OPCODES['LUI'] or opcode == RISCVEncoder.OPCODES['AUIPC']:
            return RISCVDecoder._decode_u_type(instruction)
        elif opcode == RISCVEncoder.OPCODES['JAL']:
            return RISCVDecoder._decode_j_type(instruction)
        else:
            return Instruction(
                mnemonic="UNKNOWN",
                format=InstructionFormat.R,
                opcode=opcode,
                funct3=None,
                funct7=None,
                rd=None,
                rs1=None,
                rs2=None,
                imm=None,
                description=f"Unknown instruction with opcode {opcode:#x}"
            )
    
    @staticmethod
    def _decode_r_type(instruction: int) -> Instruction:
        """Decode R-type instruction"""
        funct7 = (instruction >> 25) & 0x7F
        rs2 = (instruction >> 20) & 0x1F
        rs1 = (instruction >> 15) & 0x1F
        funct3 = (instruction >> 12) & 0x7
        rd = (instruction >> 7) & 0x1F
        opcode = instruction & 0x7F
        
        # Determine mnemonic
        mnemonic = "UNKNOWN"
        if funct7 == 0:
            if funct3 == 0:
                mnemonic = "ADD"
            elif funct3 == 1:
                mnemonic = "SLL"
            elif funct3 == 2:
                mnemonic = "SLT"
            elif funct3 == 3:
                mnemonic = "SLTU"
            elif funct3 == 4:
                mnemonic = "XOR"
            elif funct3 == 5:
                mnemonic = "SRL"
            elif funct3 == 6:
                mnemonic = "OR"
            elif funct3 == 7:
                mnemonic = "AND"
        elif funct7 == 0x20:
            if funct3 == 0:
                mnemonic = "SUB"
            elif funct3 == 5:
                mnemonic = "SRA"
        
        return Instruction(
            mnemonic=mnemonic,
            format=InstructionFormat.R,
            opcode=opcode,
            funct3=funct3,
            funct7=funct7,
            rd=rd,
            rs1=rs1,
            rs2=rs2,
            imm=None,
            description=f"{mnemonic} x{rd}, x{rs1}, x{rs2}"
        )
    
    @staticmethod
    def _decode_i_type(instruction: int) -> Instruction:
        """Decode I-type instruction"""
        imm = (instruction >> 20) & 0xFFF
        rs1 = (instruction >> 15) & 0x1F
        funct3 = (instruction >> 12) & 0x7
        rd = (instruction >> 7) & 0x1F
        opcode = instruction & 0x7F
        
        # Sign extend immediate
        if imm & 0x800:
            imm |= 0xFFFFF000
        
        # Determine mnemonic
        mnemonic = "UNKNOWN"
        if opcode == RISCVEncoder.OPCODES['OP_IMM']:
            if funct3 == 0:
                mnemonic = "ADDI"
            elif funct3 == 2:
                mnemonic = "SLTI"
            elif funct3 == 3:
                mnemonic = "SLTIU"
            elif funct3 == 4:
                mnemonic = "XORI"
            elif funct3 == 6:
                mnemonic = "ORI"
            elif funct3 == 7:
                mnemonic = "ANDI"
            elif funct3 == 1:
                mnemonic = "SLLI"
            elif funct3 == 5:
                if (instruction >> 30) & 1:
                    mnemonic = "SRAI"
                else:
                    mnemonic = "SRLI"
        elif opcode == RISCVEncoder.OPCODES['LOAD']:
            if funct3 == 0:
                mnemonic = "LB"
            elif funct3 == 1:
                mnemonic = "LH"
            elif funct3 == 2:
                mnemonic = "LW"
            elif funct3 == 4:
                mnemonic = "LBU"
            elif funct3 == 5:
                mnemonic = "LHU"
        
        return Instruction(
            mnemonic=mnemonic,
            format=InstructionFormat.I,
            opcode=opcode,
            funct3=funct3,
            funct7=None,
            rd=rd,
            rs1=rs1,
            rs2=None,
            imm=imm,
            description=f"{mnemonic} x{rd}, x{rs1}, {imm}"
        )
    
    @staticmethod
    def _decode_s_type(instruction: int) -> Instruction:
        """Decode S-type instruction"""
        imm_11_5 = (instruction >> 25) & 0x7F
        rs2 = (instruction >> 20) & 0x1F
        rs1 = (instruction >> 15) & 0x1F
        funct3 = (instruction >> 12) & 0x7
        imm_4_0 = (instruction >> 7) & 0x1F
        opcode = instruction & 0x7F
        
        # Reconstruct immediate
        imm = (imm_11_5 << 5) | imm_4_0
        if imm & 0x800:
            imm |= 0xFFFFF000
        
        # Determine mnemonic
        mnemonic = "UNKNOWN"
        if funct3 == 0:
            mnemonic = "SB"
        elif funct3 == 1:
            mnemonic = "SH"
        elif funct3 == 2:
            mnemonic = "SW"
        
        return Instruction(
            mnemonic=mnemonic,
            format=InstructionFormat.S,
            opcode=opcode,
            funct3=funct3,
            funct7=None,
            rd=None,
            rs1=rs1,
            rs2=rs2,
            imm=imm,
            description=f"{mnemonic} x{rs2}, {imm}(x{rs1})"
        )
    
    @staticmethod
    def _decode_b_type(instruction: int) -> Instruction:
        """Decode B-type instruction"""
        imm_12 = (instruction >> 31) & 1
        imm_10_5 = (instruction >> 25) & 0x3F
        rs2 = (instruction >> 20) & 0x1F
        rs1 = (instruction >> 15) & 0x1F
        funct3 = (instruction >> 12) & 0x7
        imm_11 = (instruction >> 7) & 1
        imm_4_1 = (instruction >> 8) & 0xF
        opcode = instruction & 0x7F
        
        # Reconstruct immediate
        imm = (imm_12 << 12) | (imm_11 << 11) | (imm_10_5 << 5) | (imm_4_1 << 1)
        if imm & 0x1000:
            imm |= 0xFFFFE000
        
        # Determine mnemonic
        mnemonic = "UNKNOWN"
        if funct3 == 0:
            mnemonic = "BEQ"
        elif funct3 == 1:
            mnemonic = "BNE"
        elif funct3 == 4:
            mnemonic = "BLT"
        elif funct3 == 5:
            mnemonic = "BGE"
        elif funct3 == 6:
            mnemonic = "BLTU"
        elif funct3 == 7:
            mnemonic = "BGEU"
        
        return Instruction(
            mnemonic=mnemonic,
            format=InstructionFormat.B,
            opcode=opcode,
            funct3=funct3,
            funct7=None,
            rd=None,
            rs1=rs1,
            rs2=rs2,
            imm=imm,
            description=f"{mnemonic} x{rs1}, x{rs2}, {imm}"
        )
    
    @staticmethod
    def _decode_u_type(instruction: int) -> Instruction:
        """Decode U-type instruction"""
        imm = (instruction >> 12) & 0xFFFFF
        rd = (instruction >> 7) & 0x1F
        opcode = instruction & 0x7F
        
        # Determine mnemonic
        mnemonic = "UNKNOWN"
        if opcode == RISCVEncoder.OPCODES['LUI']:
            mnemonic = "LUI"
        elif opcode == RISCVEncoder.OPCODES['AUIPC']:
            mnemonic = "AUIPC"
        
        return Instruction(
            mnemonic=mnemonic,
            format=InstructionFormat.U,
            opcode=opcode,
            funct3=None,
            funct7=None,
            rd=rd,
            rs1=None,
            rs2=None,
            imm=imm << 12,
            description=f"{mnemonic} x{rd}, {imm}"
        )
    
    @staticmethod
    def _decode_j_type(instruction: int) -> Instruction:
        """Decode J-type instruction"""
        imm_20 = (instruction >> 31) & 1
        imm_19_12 = (instruction >> 12) & 0xFF
        imm_11 = (instruction >> 20) & 1
        imm_10_1 = (instruction >> 21) & 0x3FF
        rd = (instruction >> 7) & 0x1F
        opcode = instruction & 0x7F
        
        # Reconstruct immediate
        imm = (imm_20 << 20) | (imm_19_12 << 12) | (imm_11 << 11) | (imm_10_1 << 1)
        if imm & 0x100000:
            imm |= 0xFFE00000
        
        return Instruction(
            mnemonic="JAL",
            format=InstructionFormat.J,
            opcode=opcode,
            funct3=None,
            funct7=None,
            rd=rd,
            rs1=None,
            rs2=None,
            imm=imm,
            description=f"JAL x{rd}, {imm}"
        )


class ISAExtensionDesigner:
    """Design custom ISA extensions"""
    
    @staticmethod
    def create_extension(
        name: str,
        version: str,
        description: str,
        custom_instructions: List[Dict[str, any]]
    ) -> ISAExtension:
        """
        Create a custom ISA extension
        
        Args:
            name: Extension name (e.g., "Zfinx")
            version: Version (e.g., "1.0")
            description: Extension description
            custom_instructions: List of instruction definitions
        
        Returns:
            ISAExtension object
        """
        instructions = []
        
        for instr_def in custom_instructions:
            instruction = Instruction(
                mnemonic=instr_def['mnemonic'],
                format=InstructionFormat[instr_def['format']],
                opcode=instr_def['opcode'],
                funct3=instr_def.get('funct3'),
                funct7=instr_def.get('funct7'),
                rd=instr_def.get('rd'),
                rs1=instr_def.get('rs1'),
                rs2=instr_def.get('rs2'),
                imm=instr_def.get('imm'),
                description=instr_def.get('description', '')
            )
            instructions.append(instruction)
        
        return ISAExtension(
            name=name,
            version=version,
            description=description,
            instructions=instructions,
            registers=[],
            CSRs=[]
        )
    
    @staticmethod
    def validate_extension(extension: ISAExtension) -> List[str]:
        """
        Validate an ISA extension
        
        Args:
            extension: ISAExtension to validate
        
        Returns:
            List of validation errors (empty if valid)
        """
        errors = []
        
        # Check for duplicate mnemonics
        mnemonics = [i.mnemonic for i in extension.instructions]
        duplicates = [m for m in mnemonics if mnemonics.count(m) > 1]
        if duplicates:
            errors.append(f"Duplicate mnemonics: {set(duplicates)}")
        
        # Check for conflicting opcodes
        opcode_map = {}
        for instr in extension.instructions:
            key = (instr.opcode, instr.funct3, instr.funct7)
            if key in opcode_map:
                errors.append(f"Conflicting opcodes for {instr.mnemonic} and {opcode_map[key]}")
            opcode_map[key] = instr.mnemonic
        
        return errors


class CrossCompilationTarget:
    """Cross-compilation target configuration"""
    
    # Common RISC-V targets
    TARGETS = {
        'rv32imac': CompilationTarget(
            architecture="riscv32",
            abi="ilp32",
            extensions=["rv32im", "rv32a", "rv32c"],
            march="rv32imac",
            mabi="ilp32"
        ),
        'rv32gc': CompilationTarget(
            architecture="riscv32",
            abi="ilp32d",
            extensions=["rv32g"],
            march="rv32gc",
            mabi="ilp32d"
        ),
        'rv64gc': CompilationTarget(
            architecture="riscv64",
            abi="lp64d",
            extensions=["rv64g"],
            march="rv64gc",
            mabi="lp64d"
        ),
        'rv64imafdc': CompilationTarget(
            architecture="riscv64",
            abi="lp64d",
            extensions=["rv64i", "rv64m", "rv64a", "rv64f", "rv64d", "rv64c"],
            march="rv64imafdc",
            mabi="lp64d"
        ),
    }
    
    @staticmethod
    def get_target(target_name: str) -> Optional[CompilationTarget]:
        """
        Get a compilation target by name
        
        Args:
            target_name: Target name (e.g., "rv32imac")
        
        Returns:
            CompilationTarget or None
        """
        return CrossCompilationTarget.TARGETS.get(target_name)
    
    @staticmethod
    def generate_compiler_flags(target: CompilationTarget) -> Dict[str, str]:
        """
        Generate compiler flags for a target
        
        Args:
            target: CompilationTarget
        
        Returns:
            Dictionary of compiler flags
        """
        return {
            '-march': target.march,
            '-mabi': target.mabi,
            '-mcmodel': 'medlow' if 'rv32' in target.architecture else 'medany',
            '-O2': '',
            '-g': '',
            '-Wall': '',
            '-Wextra': '',
            '-ffunction-sections': '',
            '-fdata-sections': '',
            '-nostartfiles': '',
            '-T': 'linker.ld'
        }
    
    @staticmethod
    def generate_linker_script(target: CompilationTarget) -> str:
        """
        Generate a basic linker script for a target
        
        Args:
            target: CompilationTarget
        
        Returns:
            Linker script content
        """
        pointer_size = 4 if 'rv32' in target.architecture else 8
        
        return f"""
/* RISC-V Linker Script for {target.march} */
OUTPUT_FORMAT("elf32-littleriscv" if {pointer_size == 4} else "elf64-littleriscv")
OUTPUT_ARCH(riscv)
ENTRY(_start)

MEMORY
{{
    RAM (rwx) : ORIGIN = 0x80000000, LENGTH = 256M
    FLASH (rx) : ORIGIN = 0x20000000, LENGTH = 16M
}}

SECTIONS
{{
    .text : ALIGN(4)
    {{
        _text_start = .;
        *(.text.init)
        *(.text*)
        _text_end = .;
    }} > FLASH

    .rodata : ALIGN(4)
    {{
        _rodata_start = .;
        *(.rodata*)
        _rodata_end = .;
    }} > FLASH

    .data : ALIGN(4)
    {{
        _data_start = .;
        *(.data*)
        _data_end = .;
    }} > RAM AT> FLASH

    .bss : ALIGN(4)
    {{
        _bss_start = .;
        *(.bss*)
        *(COMMON)
        _bss_end = .;
    }} > RAM

    _heap_start = ALIGN(4);
    . += 0x10000;
    _heap_end = .;

    .stack (NOLOAD) : ALIGN(4)
    {{
        _stack_start = .;
        . += 0x4000;
        _stack_end = .;
    }} > RAM

    /DISCARD/ :
    {{
        *(.comment)
        *(.note*)
        *(.eh_frame*)
    }}
}}
"""