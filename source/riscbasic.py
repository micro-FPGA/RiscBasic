#!/usr/bin/env python3
"""
RiscBasic compiler v0.13
(v0.2: compressed instructions for RV32IC / RV32EC; v0.3: branch shrinking and far branches;
 v0.4: NOT bits and fields, runtime calls through RA; v0.5: temporaries in x8-x15; v0.6: ELF output; v0.7: PRINT without automatic line end, character constants; v0.8: DWARF line info; v0.9: CONSOLE; v0.10: CHR in PRINT; v0.11: INPUT; v0.12: LINE INPUT; v0.13: PRINT of BYTE buffers)

Compiles RiscBasic (spec: RiscBasic-Spec.md, Draft 0.12) directly to RISC-V
machine code in a single pass, without a linker.

    python riscbasic.py program.bas [-o outbase] [--no-lst]

Outputs: outbase.bin, outbase.hex (Intel HEX), outbase.mem ($readmemh, one
32-bit word per line), outbase.lst (listing, unless --no-lst) and outbase.elf
(ELF32 executable with symbols, unless --no-elf).

Language design: Antti Lukats (Identor #1). Compiler: Claude (Identor #9).
10 November 2026 (7 AG).
"""

import argparse
import os
import re
import sys

VERSION = '0.13'

# ---------------------------------------------------------------- registers

ABI = ['zero', 'ra', 'sp', 'gp', 'tp', 't0', 't1', 't2', 's0', 's1',
       'a0', 'a1', 'a2', 'a3', 'a4', 'a5', 'a6', 'a7',
       's2', 's3', 's4', 's5', 's6', 's7', 's8', 's9', 's10', 's11',
       't3', 't4', 't5', 't6']
ZERO, RA, SP, GP, TP = 0, 1, 2, 3, 4
A0 = 10
# Compiler temporaries live in x8-x15 so that 16-bit RVC forms can use them:
# T1 = s0 and T2 = s1 carry values, T0 = a5 holds device base addresses.
T0, T1, T2 = 15, 8, 9
# SUB / FUNCTION parameters: a0-a7 without a5 (RV32E: a0-a4).
PARAM_REGS = [10, 11, 12, 13, 14, 16, 17]

# Register names accepted in RiscBasic source: R0..R31 and ABI names.
BASIC_REGS = {n.upper(): i for i, n in enumerate(ABI)}
BASIC_REGS.update({'R%d' % i: i for i in range(32)})
BASIC_REGS['FP'] = 8
# Inline assembly also accepts x0..x31.
ASM_REGS = dict(BASIC_REGS)
ASM_REGS.update({'X%d' % i: i for i in range(32)})

CSRS = {'mstatus': 0x300, 'misa': 0x301, 'mie': 0x304, 'mtvec': 0x305,
        'mscratch': 0x340, 'mepc': 0x341, 'mcause': 0x342, 'mtval': 0x343,
        'mip': 0x344, 'mcycle': 0xB00, 'minstret': 0xB02, 'cycle': 0xC00,
        'time': 0xC01, 'instret': 0xC02, 'mhartid': 0xF14}
CSR_NAMES = {v: k for k, v in CSRS.items()}


def rname(r):
    return ABI[r]


class RBError(Exception):
    def __init__(self, msg, line=None):
        super().__init__(msg)
        self.msg = msg
        self.line = line


def s32(v):
    v &= 0xFFFFFFFF
    return v - (1 << 32) if v & 0x80000000 else v


def sext12(v):
    v &= 0xFFF
    return v - 0x1000 if v & 0x800 else v


def fits12(v):
    return -2048 <= v <= 2047


def split_hi_lo(v):
    """Split a 32-bit value into lui immediate and 12-bit signed offset."""
    lo = sext12(v)
    hi = ((v - lo) >> 12) & 0xFFFFF
    return hi, lo


def parse_int(s):
    s = s.strip().replace('_', '')
    neg = s.startswith('-')
    if neg:
        s = s[1:]
    if re.fullmatch(r'(0[xX]|&[Hh])[0-9A-Fa-f]+', s):
        v = int(s[2:], 16)
    elif re.fullmatch(r'(0[bB]|&[Bb])[01]+', s):
        v = int(s[2:], 2)
    elif re.fullmatch(r'[0-9]+', s):
        v = int(s, 10)
    else:
        return None
    return -v if neg else v

# ---------------------------------------------------------------- encoding

def enc_r(f7, rs2, rs1, f3, rd, op):
    return (f7 << 25) | (rs2 << 20) | (rs1 << 15) | (f3 << 12) | (rd << 7) | op


def enc_i(imm, rs1, f3, rd, op):
    return ((imm & 0xFFF) << 20) | (rs1 << 15) | (f3 << 12) | (rd << 7) | op


def enc_s(imm, rs2, rs1, f3, op):
    imm &= 0xFFF
    return ((imm >> 5) << 25) | (rs2 << 20) | (rs1 << 15) | (f3 << 12) | ((imm & 0x1F) << 7) | op


def b_imm(off):
    off &= 0x1FFF
    return ((((off >> 12) & 1) << 31) | (((off >> 5) & 0x3F) << 25) |
            (((off >> 1) & 0xF) << 8) | (((off >> 11) & 1) << 7))


def j_imm(off):
    off &= 0x1FFFFF
    return ((((off >> 20) & 1) << 31) | (((off >> 1) & 0x3FF) << 21) |
            (((off >> 11) & 1) << 20) | (((off >> 12) & 0xFF) << 12))


# mnemonic: (required extension, funct7, funct3)
R_TYPE = {
    'add': (None, 0, 0), 'sub': (None, 0x20, 0), 'sll': (None, 0, 1),
    'slt': (None, 0, 2), 'sltu': (None, 0, 3), 'xor': (None, 0, 4),
    'srl': (None, 0, 5), 'sra': (None, 0x20, 5), 'or': (None, 0, 6),
    'and': (None, 0, 7),
    'mul': ('M', 1, 0), 'mulh': ('M', 1, 1), 'mulhsu': ('M', 1, 2),
    'mulhu': ('M', 1, 3), 'div': ('M', 1, 4), 'divu': ('M', 1, 5),
    'rem': ('M', 1, 6), 'remu': ('M', 1, 7),
    'rol': ('ZBB', 0x30, 1), 'ror': ('ZBB', 0x30, 5),
}
I_ARITH = {'addi': 0, 'slti': 2, 'sltiu': 3, 'xori': 4, 'ori': 6, 'andi': 7}
SHIFT_I = {'slli': (None, 0, 1), 'srli': (None, 0, 5), 'srai': (None, 0x20, 5),
           'rori': ('ZBB', 0x30, 5)}
LOADS = {'lb': 0, 'lh': 1, 'lw': 2, 'lbu': 4, 'lhu': 5}
STORES = {'sb': 0, 'sh': 1, 'sw': 2}
BRANCHES = {'beq': 0, 'bne': 1, 'blt': 4, 'bge': 5, 'bltu': 6, 'bgeu': 7}
CSR_R = {'csrrw': 1, 'csrrs': 2, 'csrrc': 3}
CSR_I = {'csrrwi': 5, 'csrrsi': 6, 'csrrci': 7}
SYSTEM = {'ecall': 0x00000073, 'ebreak': 0x00100073, 'mret': 0x30200073,
          'wfi': 0x10500073, 'fence': 0x0FF0000F}


def _fmt(mn, *ops):
    return ('%-7s %s' % (mn, ', '.join(ops))).rstrip()

# ---------------------------------------------------------------- RVC

def _sx(v, bits):
    v &= (1 << bits) - 1
    return v - (1 << bits) if v >> (bits - 1) else v


def _rvc(r):
    return 8 <= r <= 15


def _cj(f3, off):
    """c.j / c.jal: imm[11|4|9:8|10|6|7|3:1|5]."""
    o = off & 0xFFF
    bits = ((((o >> 11) & 1) << 12) | (((o >> 4) & 1) << 11) | (((o >> 8) & 3) << 9) |
            (((o >> 10) & 1) << 8) | (((o >> 6) & 1) << 7) | (((o >> 7) & 1) << 6) |
            (((o >> 1) & 7) << 3) | (((o >> 5) & 1) << 2))
    return (f3 << 13) | bits | 0x1


def compress(w):
    """Return the 16-bit RVC form of a 32-bit instruction, or None."""
    op = w & 0x7F
    rd = (w >> 7) & 31
    f3 = (w >> 12) & 7
    rs1 = (w >> 15) & 31
    rs2 = (w >> 20) & 31
    f7 = w >> 25
    imm = _sx(w >> 20, 12)
    if op == 0x13:
        if f3 == 0:                                           # addi
            if rd == 0:
                return 0x0001 if rs1 == 0 and imm == 0 else None
            if rs1 == 0 and -32 <= imm <= 31:                 # c.li
                return (0b010 << 13) | (((imm >> 5) & 1) << 12) | (rd << 7) | ((imm & 31) << 2) | 0x1
            if rs1 == rd and imm != 0 and -32 <= imm <= 31:   # c.addi
                return (((imm >> 5) & 1) << 12) | (rd << 7) | ((imm & 31) << 2) | 0x1
            if imm == 0 and rs1 != 0:                         # c.mv
                return (0b100 << 13) | (rd << 7) | (rs1 << 2) | 0x2
            if rd == 2 and rs1 == 2 and imm and imm % 16 == 0 and -512 <= imm <= 496:   # c.addi16sp
                i = imm & 0x3FF
                return ((0b011 << 13) | (((i >> 9) & 1) << 12) | (2 << 7) | (((i >> 4) & 1) << 6) |
                        (((i >> 6) & 1) << 5) | (((i >> 7) & 3) << 3) | (((i >> 5) & 1) << 2) | 0x1)
            if _rvc(rd) and rs1 == 2 and 0 < imm <= 1020 and imm % 4 == 0:              # c.addi4spn
                return ((((imm >> 4) & 3) << 11) | (((imm >> 6) & 15) << 7) | (((imm >> 2) & 1) << 6) |
                        (((imm >> 3) & 1) << 5) | ((rd - 8) << 2))
        elif f3 == 1 and f7 == 0 and rd == rs1 and rd and rs2:                          # c.slli
            return (rd << 7) | (rs2 << 2) | 0x2
        elif f3 == 5 and f7 in (0, 0x20) and rd == rs1 and _rvc(rd) and rs2:            # c.srli / c.srai
            return (0b100 << 13) | ((0 if f7 == 0 else 1) << 10) | ((rd - 8) << 7) | (rs2 << 2) | 0x1
        elif f3 == 7 and rd == rs1 and _rvc(rd) and -32 <= imm <= 31:                   # c.andi
            return ((0b100 << 13) | (((imm >> 5) & 1) << 12) | (0b10 << 10) | ((rd - 8) << 7) |
                    ((imm & 31) << 2) | 0x1)
        return None
    if op == 0x33:
        if f7 == 0 and f3 == 0 and rd:                        # add
            if rs1 == rd and rs2:
                return (0b100 << 13) | (1 << 12) | (rd << 7) | (rs2 << 2) | 0x2
            if rs2 == rd and rs1:
                return (0b100 << 13) | (1 << 12) | (rd << 7) | (rs1 << 2) | 0x2
            if rs1 == 0 and rs2:
                return (0b100 << 13) | (rd << 7) | (rs2 << 2) | 0x2
            return None
        f2 = {(0x20, 0): 0, (0, 4): 1, (0, 6): 2, (0, 7): 3}.get((f7, f3))
        if f2 is None or not _rvc(rd):
            return None
        other = rs2 if rs1 == rd else (rs1 if rs2 == rd and f2 != 0 else None)
        if other is None or not _rvc(other):
            return None
        return (0b100 << 13) | (0b11 << 10) | ((rd - 8) << 7) | (f2 << 5) | ((other - 8) << 2) | 0x1
    if op == 0x37:                                            # c.lui
        v = _sx(w >> 12, 20)
        if rd not in (0, 2) and v and -32 <= v <= 31:
            return (0b011 << 13) | (((v >> 5) & 1) << 12) | (rd << 7) | ((v & 31) << 2) | 0x1
        return None
    if op == 0x03 and f3 == 2 and rd:                         # lw
        if rs1 == 2 and 0 <= imm <= 252 and imm % 4 == 0:     # c.lwsp
            return ((0b010 << 13) | (((imm >> 5) & 1) << 12) | (rd << 7) | (((imm >> 2) & 7) << 4) |
                    (((imm >> 6) & 3) << 2) | 0x2)
        if _rvc(rd) and _rvc(rs1) and 0 <= imm <= 124 and imm % 4 == 0:   # c.lw
            return ((0b010 << 13) | (((imm >> 3) & 7) << 10) | ((rs1 - 8) << 7) | (((imm >> 2) & 1) << 6) |
                    (((imm >> 6) & 1) << 5) | ((rd - 8) << 2))
        return None
    if op == 0x23 and f3 == 2:                                # sw
        simm = _sx(((w >> 25) << 5) | ((w >> 7) & 31), 12)
        if rs1 == 2 and 0 <= simm <= 252 and simm % 4 == 0:   # c.swsp
            return (0b110 << 13) | (((simm >> 2) & 15) << 9) | (((simm >> 6) & 3) << 7) | (rs2 << 2) | 0x2
        if _rvc(rs1) and _rvc(rs2) and 0 <= simm <= 124 and simm % 4 == 0:   # c.sw
            return ((0b110 << 13) | (((simm >> 3) & 7) << 10) | ((rs1 - 8) << 7) | (((simm >> 2) & 1) << 6) |
                    (((simm >> 6) & 1) << 5) | ((rs2 - 8) << 2))
        return None
    if op == 0x67 and f3 == 0 and imm == 0 and rs1 and rd in (0, 1):   # c.jr / c.jalr
        return (0b100 << 13) | (rd << 12) | (rs1 << 7) | 0x2
    if op == 0x6F and rd in (0, 1):                           # c.j / c.jal
        off = _sx(((w >> 31) << 20) | (((w >> 12) & 0xFF) << 12) | (((w >> 20) & 1) << 11) |
                  (((w >> 21) & 0x3FF) << 1), 21)
        if -2048 <= off <= 2046:
            return _cj(0b101 if rd == 0 else 0b001, off)
        return None
    if op == 0x63 and f3 in (0, 1) and rs2 == 0 and _rvc(rs1):   # c.beqz / c.bnez
        off = _sx(((w >> 31) << 12) | (((w >> 7) & 1) << 11) | (((w >> 25) & 0x3F) << 5) |
                  (((w >> 8) & 0xF) << 1), 13)
        if -256 <= off <= 254:
            o = off & 0x1FF
            return ((0b110 + f3) << 13) | (((o >> 8) & 1) << 12) | (((o >> 3) & 3) << 10) | \
                ((rs1 - 8) << 7) | (((o >> 6) & 3) << 5) | (((o >> 1) & 3) << 3) | (((o >> 5) & 1) << 2) | 0x1
        return None
    if w == 0x00100073:                                       # c.ebreak
        return 0x9002
    return None


def rvc_name(h):
    """Mnemonic of a 16-bit RVC instruction."""
    q, f3 = h & 3, h >> 13
    rd, rs2, b12 = (h >> 7) & 31, (h >> 2) & 31, (h >> 12) & 1
    if q == 0:
        return {0: 'c.addi4spn', 2: 'c.lw', 6: 'c.sw'}[f3]
    if q == 1:
        if f3 == 4:
            f2 = (h >> 10) & 3
            if f2 < 3:
                return ('c.srli', 'c.srai', 'c.andi')[f2]
            return ('c.sub', 'c.xor', 'c.or', 'c.and')[(h >> 5) & 3]
        if f3 == 3:
            return 'c.addi16sp' if rd == 2 else 'c.lui'
        if f3 == 0 and h == 1:
            return 'c.nop'
        return {0: 'c.addi', 1: 'c.jal', 2: 'c.li', 5: 'c.j', 6: 'c.beqz', 7: 'c.bnez'}[f3]
    if f3 == 4:
        if not b12:
            return 'c.jr' if rs2 == 0 else 'c.mv'
        if rd == 0 and rs2 == 0:
            return 'c.ebreak'
        return 'c.jalr' if rs2 == 0 else 'c.add'
    return {0: 'c.slli', 2: 'c.lwsp', 6: 'c.swsp'}[f3]


def rvc_text(name, text):
    """Rewrite the 32-bit listing text of an instruction in RVC assembler syntax."""
    note = ''
    if '   ;' in text:
        text, note = text.split('   ;', 1)
        note = '   ;' + note
    parts = text.split(None, 1)
    ops = [o.strip() for o in parts[1].split(',')] if len(parts) > 1 else []
    mn = parts[0]
    if name in ('c.nop', 'c.ebreak'):
        ops = []
    elif name == 'c.mv':
        ops = [ops[0], ops[2] if mn == 'add' else ops[1]]
    elif name in ('c.li', 'c.addi', 'c.addi16sp', 'c.slli', 'c.srli', 'c.srai', 'c.andi'):
        ops = [ops[0], ops[2]]
    elif name in ('c.add', 'c.sub', 'c.xor', 'c.or', 'c.and'):
        ops = [ops[0], ops[2] if ops[1] == ops[0] else ops[1]]
    elif name in ('c.jr', 'c.jalr'):
        ops = [ops[1].split('(')[1].rstrip(')')]
    elif name in ('c.j', 'c.jal'):
        ops = [ops[1]]
    elif name in ('c.beqz', 'c.bnez'):
        ops = [ops[0], ops[2]]
    return _fmt(name, *ops) + note


class Emitter:
    """Machine code buffer with labels, relocatable items and listing records.

    Items are emitted in one pass. Branches, jumps, %hi/%lo address loads and
    alignment padding stay relocatable until finalize(), which shrinks branches
    and jumps to 16-bit forms where they fit (C targets), lays out the final
    addresses and encodes everything.

    Item kinds: fixed, data, branch, jump, hi, loi, los, align.
    """

    def __init__(self):
        self.base = 0
        self.ext = set()
        self.code = []        # value of each item (base word for relocatable items)
        self.isz = []         # size in bytes: 0 (unused padding), 2, 4, or 8 (far branch)
        self.addr = []        # address of each item, final after finalize()
        self.kind = []
        self.ref = []         # label or symbol for relocatable items
        self.iline = []       # source line of each item, for error messages
        self.nbytes = 0
        self.end = 0
        self.text = []
        self.lst = []
        self.labels = {}      # label -> index of the item that follows it
        self.line = 0

    def pc(self):
        """Address of the next item during emission (final addresses come from finalize)."""
        return self.base + self.nbytes

    def mark(self):
        return len(self.code)

    def addr_at(self, idx):
        """Final address of item idx (or of the end of code)."""
        return self.addr[idx] if idx < len(self.addr) else self.end

    def label(self, name):
        if name in self.labels:
            raise RBError("label '%s' defined twice" % name)
        self.labels[name] = len(self.code)
        self.lst.append(('label', name))

    def comment(self, s):
        self.lst.append(('src', s))

    def head(self, s):
        self.lst.append(('head', s))

    def _put(self, word, text, fix=None, size=4, kind='fixed'):
        """Append one item. Fixed instructions are compressed at once when the target has C."""
        ref = None
        if fix:
            kind, ref = {'B': 'branch', 'J': 'jump', 'HI': 'hi', 'LOI': 'loi', 'LOS': 'los'}[fix[0]], fix[1]
        elif kind == 'fixed' and 'C' in self.ext:
            h = compress(word)
            if h is not None:
                word, size, text = h, 2, rvc_text(rvc_name(h), text)
        idx = len(self.code)
        self.code.append(word)
        self.isz.append(size)
        self.addr.append(self.pc())
        self.kind.append(kind)
        self.ref.append(ref)
        self.iline.append(self.line)
        self.nbytes += size
        self.text.append(text)
        self.lst.append(('ins', idx))
        return idx

    def data(self, word, text):
        return self._put(word, text, kind='data')

    def align4(self):
        """Pad to a 4-byte boundary with c.nop; the padding is recomputed in finalize()."""
        self._put(0x0001, 'c.nop   (align)', size=2 if self.pc() & 3 else 0, kind='align')

    def binary(self):
        return b''.join(v.to_bytes(s, 'little') for v, s in zip(self.code, self.isz) if s)

    def _need(self, ext, mn):
        if ext and ext not in self.ext:
            raise RBError("'%s' needs +%s in TARGET" % (mn, 'Zbb' if ext == 'ZBB' else ext))

    def _r(self, r):
        if not isinstance(r, int) or not 0 <= r < 32:
            raise RBError('bad register %r' % (r,))
        if 'E' in self.ext and r > 15:
            raise RBError('register %s does not exist on RV32E' % ABI[r])
        return r

    def ins(self, mn, *a):
        R = self._r
        if mn in R_TYPE:
            ext, f7, f3 = R_TYPE[mn]
            self._need(ext, mn)
            rd, rs1, rs2 = a
            return self._put(enc_r(f7, R(rs2), R(rs1), f3, R(rd), 0x33),
                             _fmt(mn, rname(rd), rname(rs1), rname(rs2)))
        if mn in I_ARITH:
            rd, rs1, imm = a
            if isinstance(imm, tuple):
                return self._put(enc_i(0, R(rs1), I_ARITH[mn], R(rd), 0x13),
                                 _fmt(mn, rname(rd), rname(rs1), '%%lo(%s)' % imm[1]), ('LOI', imm[1]))
            imm = s32(imm)
            if not fits12(imm):
                raise RBError('immediate %d does not fit in 12 bits (%s)' % (imm, mn))
            return self._put(enc_i(imm, R(rs1), I_ARITH[mn], R(rd), 0x13),
                             _fmt(mn, rname(rd), rname(rs1), str(imm)))
        if mn in SHIFT_I:
            ext, f7, f3 = SHIFT_I[mn]
            self._need(ext, mn)
            rd, rs1, sh = a
            if not 0 <= sh <= 31:
                raise RBError('shift amount %d out of range' % sh)
            return self._put(enc_i((f7 << 5) | sh, R(rs1), f3, R(rd), 0x13),
                             _fmt(mn, rname(rd), rname(rs1), str(sh)))
        if mn in LOADS:
            rd, imm, rs1 = a
            if isinstance(imm, tuple):
                return self._put(enc_i(0, R(rs1), LOADS[mn], R(rd), 0x03),
                                 _fmt(mn, rname(rd), '%%lo(%s)(%s)' % (imm[1], rname(rs1))), ('LOI', imm[1]))
            if not fits12(imm):
                raise RBError('offset %d does not fit in 12 bits' % imm)
            return self._put(enc_i(imm, R(rs1), LOADS[mn], R(rd), 0x03),
                             _fmt(mn, rname(rd), '%d(%s)' % (imm, rname(rs1))))
        if mn in STORES:
            rs2, imm, rs1 = a
            if isinstance(imm, tuple):
                return self._put(enc_s(0, R(rs2), R(rs1), STORES[mn], 0x23),
                                 _fmt(mn, rname(rs2), '%%lo(%s)(%s)' % (imm[1], rname(rs1))), ('LOS', imm[1]))
            if not fits12(imm):
                raise RBError('offset %d does not fit in 12 bits' % imm)
            return self._put(enc_s(imm, R(rs2), R(rs1), STORES[mn], 0x23),
                             _fmt(mn, rname(rs2), '%d(%s)' % (imm, rname(rs1))))
        if mn in BRANCHES:
            rs1, rs2, label = a
            if mn in ('beq', 'bne') and rs1 == ZERO:
                rs1, rs2 = rs2, rs1           # zero second, so c.beqz / c.bnez can apply
            return self._put(enc_r(0, R(rs2), R(rs1), BRANCHES[mn], 0, 0x63),
                             _fmt(mn, rname(rs1), rname(rs2), label), ('B', label))
        if mn in ('lui', 'auipc'):
            rd, imm = a
            op = 0x37 if mn == 'lui' else 0x17
            if isinstance(imm, tuple):
                return self._put((R(rd) << 7) | op, _fmt(mn, rname(rd), '%%hi(%s)' % imm[1]), ('HI', imm[1]))
            if not 0 <= imm <= 0xFFFFF:
                raise RBError('%s immediate 0x%X out of range' % (mn, imm))
            return self._put((imm << 12) | (R(rd) << 7) | op, _fmt(mn, rname(rd), '0x%X' % imm))
        if mn == 'jal':
            rd, label = a
            return self._put((R(rd) << 7) | 0x6F, _fmt(mn, rname(rd), label), ('J', label))
        if mn == 'jalr':
            rd, imm, rs1 = a
            if not fits12(imm):
                raise RBError('offset %d does not fit in 12 bits' % imm)
            return self._put(enc_i(imm, R(rs1), 0, R(rd), 0x67),
                             _fmt(mn, rname(rd), '%d(%s)' % (imm, rname(rs1))))
        if mn in CSR_R:
            rd, csr, rs1 = a
            return self._put(enc_i(csr, R(rs1), CSR_R[mn], R(rd), 0x73),
                             _fmt(mn, rname(rd), CSR_NAMES.get(csr, '0x%X' % csr), rname(rs1)))
        if mn in CSR_I:
            rd, csr, uimm = a
            if not 0 <= uimm <= 31:
                raise RBError('CSR immediate %d out of range' % uimm)
            return self._put(enc_i(csr, uimm, CSR_I[mn], R(rd), 0x73),
                             _fmt(mn, rname(rd), CSR_NAMES.get(csr, '0x%X' % csr), str(uimm)))
        if mn in SYSTEM:
            return self._put(SYSTEM[mn], mn)
        raise RBError("unknown instruction '%s'" % mn)

    # pseudo-instructions
    def li(self, rd, v):
        if not -(1 << 31) <= v <= 0xFFFFFFFF:
            raise RBError('constant %d does not fit in 32 bits' % v)
        v = s32(v)
        if fits12(v):
            self.ins('addi', rd, ZERO, v)
            return
        hi, lo = split_hi_lo(v)
        self.ins('lui', rd, hi)
        if lo:
            self.ins('addi', rd, rd, lo)

    def la(self, rd, sym):
        self.ins('lui', rd, ('hi', sym))
        self.ins('addi', rd, rd, ('lo', sym))

    def mv(self, rd, rs):
        if rd != rs:
            self.ins('addi', rd, rs, 0)

    def j(self, label):
        self.ins('jal', ZERO, label)

    # ---- final layout

    def layout(self):
        a = self.base
        for i, k in enumerate(self.kind):
            if k == 'align':
                self.isz[i] = 2 if a & 3 else 0
            self.addr[i] = a
            a += self.isz[i]
        self.end = a
        self.nbytes = a - self.base

    def target(self, i, symbols):
        name = self.ref[i]
        if name in self.labels:
            return self.addr_at(self.labels[name])
        if name in symbols:
            return symbols[name]
        raise RBError("undefined label '%s'" % name, self.iline[i])

    def shrinkable(self, i):
        w = self.code[i]
        if self.kind[i] == 'branch':     # beq/bne rs1', zero
            return (w >> 12) & 7 in (0, 1) and (w >> 20) & 31 == 0 and _rvc((w >> 15) & 31)
        return (w >> 7) & 31 in (0, 1)   # jal zero / jal ra

    def finalize(self, symbols):
        """Shrink branches and jumps, lay out final addresses and encode all relocatable items."""
        reloc = [i for i, k in enumerate(self.kind) if k in ('branch', 'jump')]
        cand = set(i for i in reloc if 'C' in self.ext and self.shrinkable(i))
        locked = set()
        while True:
            self.layout()
            changed = False
            for i in reloc:
                off = self.target(i, symbols) - self.addr[i]
                k, s = self.kind[i], self.isz[i]
                short = (-256 <= off <= 254) if k == 'branch' else (-2048 <= off <= 2046)
                if s == 2 and not short:              # padding moved a target out of reach
                    self.isz[i] = 4
                    locked.add(i)
                    changed = True
                elif s == 4 and i in cand and i not in locked and short:
                    self.isz[i] = 2
                    changed = True
                elif s == 4 and k == 'branch' and not -4096 <= off < 4096:
                    self.isz[i] = 8                   # far branch: inverted branch over a jal
                    locked.add(i)
                    changed = True
            if not changed:
                break
        for i, k in enumerate(self.kind):
            if k in ('fixed', 'data'):
                continue
            if k == 'align':
                continue
            tgt = self.target(i, symbols)
            w, text = self.code[i], self.text[i]
            if k in ('branch', 'jump'):
                off = tgt - self.addr[i]
                note = '   ; -> %08X' % tgt
                if k == 'jump':
                    if not -(1 << 20) <= off < (1 << 20):
                        raise RBError("jump to '%s' out of range" % self.ref[i], self.iline[i])
                    w |= j_imm(off)
                elif self.isz[i] == 8:
                    inv = w ^ (1 << 12) | b_imm(8)    # flipping funct3 bit 0 inverts the condition
                    jal = 0x6F | j_imm(off - 4)
                    if not -(1 << 20) <= off - 4 < (1 << 20):
                        raise RBError("branch to '%s' out of range" % self.ref[i], self.iline[i])
                    self.code[i] = (jal << 32) | inv
                    mn, ops = text.split(None, 1)
                    inv_mn = {'beq': 'bne', 'bne': 'beq', 'blt': 'bge', 'bge': 'blt',
                              'bltu': 'bgeu', 'bgeu': 'bltu'}[mn]
                    self.text[i] = (_fmt(inv_mn, *(ops.split(', ')[:2] + ['+8'])),
                                    _fmt('jal', 'zero', self.ref[i]) + note)
                    continue
                else:
                    w |= b_imm(off)
                if self.isz[i] == 2:
                    h = compress(w)
                    if h is None:
                        raise RBError('internal error: cannot compress %s' % text, self.iline[i])
                    self.code[i] = h
                    self.text[i] = rvc_text(rvc_name(h), text) + note
                else:
                    self.code[i] = w
                    self.text[i] = text + note
            elif k == 'hi':
                self.code[i] = w | (split_hi_lo(tgt)[0] << 12)
                self.text[i] = text + '   ; = 0x%X' % split_hi_lo(tgt)[0]
            elif k == 'loi':
                self.code[i] = w | ((sext12(tgt) & 0xFFF) << 20)
                self.text[i] = text + '   ; = %d' % sext12(tgt)
            elif k == 'los':
                imm = sext12(tgt) & 0xFFF
                self.code[i] = w | ((imm >> 5) << 25) | ((imm & 0x1F) << 7)
                self.text[i] = text + '   ; = %d' % sext12(tgt)

    def saved(self):
        """Bytes saved by compressed instructions."""
        return 2 * sum(1 for s, k in zip(self.isz, self.kind) if s == 2 and k != 'align')

# ---------------------------------------------------------------- assembler

class AsmResolver:
    """Name lookup for inline ASM. Returns None when a name is unknown."""

    def reg(self, name):
        return None

    def imm(self, name):
        return None

    def mem(self, name):
        return None


def asm_reg(s, res):
    u = s.strip().upper()
    if u in ASM_REGS:
        return ASM_REGS[u]
    if res:
        r = res.reg(u)
        if r is not None:
            return r
    raise RBError("bad register '%s'" % s.strip())


def asm_imm(s, res):
    v = parse_int(s)
    if v is not None:
        return v
    if res:
        v = res.imm(s.strip().upper())
        if v is not None:
            return v
    raise RBError("bad immediate '%s'" % s.strip())


def asm_mem(s, res):
    m = re.match(r'^(.*?)\(\s*(\w+)\s*\)$', s.strip())
    if m:
        imm = asm_imm(m.group(1), res) if m.group(1).strip() else 0
        return imm, asm_reg(m.group(2), res)
    if res:
        r = res.mem(s.strip().upper())
        if r is not None:
            return r
    raise RBError("bad memory operand '%s'" % s.strip())


def asm_csr(s, res):
    n = s.strip().lower()
    if n in CSRS:
        return CSRS[n]
    return asm_imm(s, res)


def asm_line(em, line, res=None):
    """Assemble one line of RISC-V assembly into the emitter."""
    line = re.split(r"[;#']", line, maxsplit=1)[0].strip()
    if not line:
        return
    m = re.match(r'^([A-Za-z_.][\w.]*)\s*:\s*(.*)$', line)
    if m:
        em.label(m.group(1))
        line = m.group(2).strip()
        if not line:
            return
    parts = line.split(None, 1)
    mn = parts[0].lower()
    ops = [p.strip() for p in parts[1].split(',')] if len(parts) > 1 else []

    def R(i):
        return asm_reg(ops[i], res)

    def I(i):
        return asm_imm(ops[i], res)

    def need(n):
        if len(ops) != n:
            raise RBError("'%s' expects %d operands" % (mn, n))

    if mn in R_TYPE:
        need(3); em.ins(mn, R(0), R(1), R(2))
    elif mn in I_ARITH or mn in SHIFT_I:
        need(3); em.ins(mn, R(0), R(1), I(2))
    elif mn in LOADS or mn in STORES:
        need(2); imm, base = asm_mem(ops[1], res); em.ins(mn, R(0), imm, base)
    elif mn in BRANCHES:
        need(3); em.ins(mn, R(0), R(1), ops[2])
    elif mn in ('lui', 'auipc'):
        need(2); em.ins(mn, R(0), I(1))
    elif mn == 'jal':
        if len(ops) == 1:
            em.ins('jal', RA, ops[0])
        else:
            need(2); em.ins('jal', R(0), ops[1])
    elif mn == 'jalr':
        if len(ops) == 1:
            em.ins('jalr', RA, 0, R(0))
        else:
            need(2)
            if '(' in ops[1]:
                imm, base = asm_mem(ops[1], res)
            else:
                imm, base = 0, R(1)
            em.ins('jalr', R(0), imm, base)
    elif mn in CSR_R:
        need(3); em.ins(mn, R(0), asm_csr(ops[1], res), R(2))
    elif mn in CSR_I:
        need(3); em.ins(mn, R(0), asm_csr(ops[1], res), I(2))
    elif mn in SYSTEM:
        need(0); em.ins(mn)
    elif mn == 'nop':
        need(0); em.ins('addi', ZERO, ZERO, 0)
    elif mn == 'li':
        need(2); em.li(R(0), I(1))
    elif mn == 'la':
        need(2); em.la(R(0), ops[1])
    elif mn == 'mv':
        need(2); em.ins('addi', R(0), R(1), 0)
    elif mn == 'not':
        need(2); em.ins('xori', R(0), R(1), -1)
    elif mn == 'neg':
        need(2); em.ins('sub', R(0), ZERO, R(1))
    elif mn == 'seqz':
        need(2); em.ins('sltiu', R(0), R(1), 1)
    elif mn == 'snez':
        need(2); em.ins('sltu', R(0), ZERO, R(1))
    elif mn == 'j':
        need(1); em.ins('jal', ZERO, ops[0])
    elif mn == 'jr':
        need(1); em.ins('jalr', ZERO, 0, R(0))
    elif mn == 'ret':
        need(0); em.ins('jalr', ZERO, 0, RA)
    elif mn == 'call':
        need(1); em.ins('jal', RA, ops[0])
    elif mn in ('beqz', 'bnez', 'bltz', 'bgez'):
        need(2)
        em.ins({'beqz': 'beq', 'bnez': 'bne', 'bltz': 'blt', 'bgez': 'bge'}[mn], R(0), ZERO, ops[1])
    elif mn in ('blez', 'bgtz'):
        need(2); em.ins('bge' if mn == 'blez' else 'blt', ZERO, R(0), ops[1])
    elif mn in ('bgt', 'ble', 'bgtu', 'bleu'):
        need(3)
        em.ins({'bgt': 'blt', 'ble': 'bge', 'bgtu': 'bltu', 'bleu': 'bgeu'}[mn], R(1), R(0), ops[2])
    elif mn == 'csrr':
        need(2); em.ins('csrrs', R(0), asm_csr(ops[1], res), ZERO)
    elif mn in ('csrw', 'csrs', 'csrc'):
        need(2); em.ins('csrr' + mn[3], ZERO, asm_csr(ops[0], res), R(1))
    elif mn in ('csrwi', 'csrsi', 'csrci'):
        need(2); em.ins('csrr' + mn[3] + 'i', ZERO, asm_csr(ops[0], res), I(1))
    else:
        raise RBError("unknown instruction '%s'" % mn)


def asm_text(em, text, res=None):
    for line in text.strip('\n').split('\n'):
        asm_line(em, line, res)

# ---------------------------------------------------------------- lexer

TOKEN_RE = re.compile(r'''
    (?P<ws>\s+)
  | (?P<str>"(?:[^"]|"")*")
  | (?P<hex>(?:&[Hh]|0[xX])[0-9A-Fa-f_]+)
  | (?P<bin>(?:&[Bb]|0[bB])[01_]+)
  | (?P<num>[0-9][0-9_]*[Kk]?)
  | (?P<id>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op><>|<=|>=|[-+*/=<>()\[\],:;.])
''', re.X)


class Tok:
    __slots__ = ('kind', 'val', 'up', 'text')

    def __init__(self, kind, val, text):
        self.kind = kind
        self.val = val
        self.text = text
        self.up = val.upper() if kind == 'id' else None

    def __repr__(self):
        return self.text


EOL = Tok('eol', '', '<end of line>')


def strip_comment(s):
    inq = False
    for i, ch in enumerate(s):
        if ch == '"':
            inq = not inq
        elif ch == "'" and not inq:
            return s[:i]
    return s


def lex(s):
    toks = []
    pos = 0
    while pos < len(s):
        m = TOKEN_RE.match(s, pos)
        if not m:
            raise RBError("unexpected character '%s'" % s[pos])
        pos = m.end()
        k, t = m.lastgroup, m.group()
        if k == 'ws':
            continue
        if k == 'str':
            toks.append(Tok('str', t[1:-1].replace('""', '"'), t))
        elif k == 'hex':
            toks.append(Tok('num', int(t[2:].replace('_', ''), 16), t))
        elif k == 'bin':
            toks.append(Tok('num', int(t[2:].replace('_', ''), 2), t))
        elif k == 'num':
            v = t.replace('_', '')
            mult = 1
            if v[-1] in 'Kk':
                mult, v = 1024, v[:-1]
            toks.append(Tok('num', int(v) * mult, t))
        elif k == 'id':
            toks.append(Tok('id', t, t))
        else:
            toks.append(Tok('op', t, t))
    return toks

# ---------------------------------------------------------------- objects

TYPES = {
    'BIT': (8, False, True),
    'BYTE': (8, False, False),
    'HALF': (16, False, False),
    'WORD': (32, False, False),
    'SBYTE': (8, True, False),
    'SHALF': (16, True, False),
    'SWORD': (32, True, False),
}
IRQ_NAMES = {'MSOFT': 3, 'MTIMER': 7, 'MEXT': 11}
CMP_OPS = ('=', '<>', '<', '>', '<=', '>=')
DECLARATIONS = {'TARGET', 'CLOCK', 'CPI', 'CODE', 'RAM', 'STACK', 'CONST',
                'DEVICE', 'ALIAS', 'DIM', 'CONSOLE'}
KEYWORDS = DECLARATIONS | {
    'IF', 'THEN', 'ELSEIF', 'ELSE', 'END', 'WHILE', 'WEND', 'DO', 'LOOP',
    'UNTIL', 'FOR', 'TO', 'STEP', 'NEXT', 'EXIT', 'GOTO', 'SELECT', 'CASE',
    'SUB', 'FUNCTION', 'RETURN', 'CALL', 'ON', 'IRQ', 'WAIT', 'DELAY_US',
    'DELAY_MS', 'PRINT', 'INPUT', 'LINE', 'HEX', 'DEC', 'CHR', 'SHL', 'SHR', 'SAR', 'ROL', 'ROR',
    'ATOMIC', 'ASM', 'LET', 'AND', 'OR', 'XOR', 'NOT', 'MOD', 'AS', 'IN',
    'AT', 'SIZE', 'REG', 'BIT', 'FIELD', 'READONLY', 'SETS', 'CLEARS',
    'MEM8', 'MEM16', 'MEM32'}


class Obj:
    """A RiscBasic object: anything that can stand on either side of '='.

    kind: reg (CPU register or register variable), ram (RAM variable),
    array (array declaration), arr (array element), loc (memory-mapped
    location: device register or MEMn), sub (bit or field of another object).
    """

    def __init__(self, kind, name='', **kw):
        self.kind = kind
        self.name = name
        self.width = 32
        self.signed = False
        self.isbit = False
        self.readonly = False
        self.setaddr = None
        self.clraddr = None
        self.addr = None
        self.aexpr = None
        self.reg = None
        self.base = None
        self.lo = 0
        self.w = 1
        self.inv = False          # sub only: bit or field is inverted (NOT) on read and write
        self.index = None
        self.count = 0
        self.__dict__.update(kw)


class Node:
    """Expression node. k: const | obj | bin | un | call."""
    __slots__ = ('k', 'a', 'signed', 'isbool', 'width')

    def __init__(self, k, a, signed=False, isbool=False, width=32):
        self.k = k
        self.a = a
        self.signed = signed
        self.isbool = isbool
        self.width = width


def fold(op, a, b):
    if op == '+':
        return a + b
    if op == '-':
        return a - b
    if op == '*':
        return a * b
    if op in ('/', 'MOD'):
        if b == 0:
            raise RBError('division by zero in constant expression')
        q = abs(a) // abs(b)
        if (a < 0) != (b < 0):
            q = -q
        return q if op == '/' else a - b * q
    if op == 'AND':
        return a & b
    if op == 'OR':
        return a | b
    if op == 'XOR':
        return a ^ b
    if op == 'SHL':
        return (a << b) & 0xFFFFFFFF
    if op == 'SHR':
        return (a & 0xFFFFFFFF) >> b
    if op == 'SAR':
        return s32(a) >> b
    a, b = s32(a), s32(b)
    return int({'=': a == b, '<>': a != b, '<': a < b, '>': a > b,
                '<=': a <= b, '>=': a >= b}[op])

# ---------------------------------------------------------------- runtime

def rt_mul():
    return '''
__mul:
    addi sp, sp, -8
    sw a0, 0(sp)
    sw a1, 4(sp)
    li a0, 0
__mul_l:
    beqz t2, __mul_d
    andi a1, t2, 1
    beqz a1, __mul_s
    add a0, a0, t1
__mul_s:
    slli t1, t1, 1
    srli t2, t2, 1
    j __mul_l
__mul_d:
    mv t1, a0
    lw a0, 0(sp)
    lw a1, 4(sp)
    addi sp, sp, 8
    ret
''', []


def rt_divu():
    return '''
__divu:
    addi sp, sp, -16
    sw a0, 0(sp)
    sw a1, 4(sp)
    sw a2, 8(sp)
    sw a3, 12(sp)
    li a0, 0
    li a1, 32
__divu_l:
    srli a2, a0, 31
    slli a0, a0, 1
    srli a3, t1, 31
    or a0, a0, a3
    slli t1, t1, 1
    bnez a2, __divu_sub
    bltu a0, t2, __divu_s
__divu_sub:
    sub a0, a0, t2
    ori t1, t1, 1
__divu_s:
    addi a1, a1, -1
    bnez a1, __divu_l
    mv t2, a0
    lw a0, 0(sp)
    lw a1, 4(sp)
    lw a2, 8(sp)
    lw a3, 12(sp)
    addi sp, sp, 16
    ret
''', []


def rt_div():
    return '''
__div:
    addi sp, sp, -12
    sw ra, 0(sp)
    sw a0, 4(sp)
    sw a1, 8(sp)
    srli a0, t1, 31
    srli a1, t2, 31
    beqz a0, __div_1
    sub t1, zero, t1
__div_1:
    beqz a1, __div_2
    sub t2, zero, t2
__div_2:
    jal ra, __divu
    xor a1, a1, a0
    beqz a1, __div_3
    sub t1, zero, t1
__div_3:
    beqz a0, __div_4
    sub t2, zero, t2
__div_4:
    lw ra, 0(sp)
    lw a0, 4(sp)
    lw a1, 8(sp)
    addi sp, sp, 12
    ret
''', ['__divu']


def rt_putc(dev, st_addr, st_ld, bit, data_ofs, data_st, inv=False):
    """inv: TXREADY is declared with NOT, so the status bit is 1 while busy."""
    lines = ['__putc_%s:' % dev,
             'addi sp, sp, -4',
             'sw a0, 0(sp)',
             'li a0, 0x%X' % st_addr,
             '__putc_%s_w:' % dev,
             '%s t2, 0(a0)' % st_ld]
    if bit < 11:
        lines.append('andi t2, t2, %d' % (1 << bit))
    else:
        lines += ['srli t2, t2, %d' % bit, 'andi t2, t2, 1']
    lines += ['%s t2, __putc_%s_w' % ('bnez' if inv else 'beqz', dev),
              '%s t1, %d(a0)' % (data_st, data_ofs),
              'lw a0, 0(sp)',
              'addi sp, sp, 4',
              'ret']
    return '\n'.join(lines), []


def rt_getc(dev, st_addr, st_ld, bit, data_ofs, data_ld, mask, inv=False):
    """Wait for RXREADY, read one character into t1. inv: RXREADY declared with NOT."""
    lines = ['__getc_%s:' % dev,
             'addi sp, sp, -4',
             'sw a0, 0(sp)',
             'li a0, 0x%X' % st_addr,
             '__getc_%s_w:' % dev,
             '%s t2, 0(a0)' % st_ld]
    if bit < 11:
        lines.append('andi t2, t2, %d' % (1 << bit))
    else:
        lines += ['srli t2, t2, %d' % bit, 'andi t2, t2, 1']
    lines += ['%s t2, __getc_%s_w' % ('bnez' if inv else 'beqz', dev),
              '%s t1, %d(a0)' % (data_ld, data_ofs)]
    if mask:
        lines.append('andi t1, t1, 255')
    lines += ['lw a0, 0(sp)',
              'addi sp, sp, 4',
              'ret']
    return '\n'.join(lines), []


def rt_getdec(dev, echo):
    """Read an optional '-' and decimal digits into t1; the first other character ends it."""
    e = 'jal ra, __putc_%s' % dev if echo else ''
    return '''
__getdec_{d}:
    addi sp, sp, -20
    sw ra, 0(sp)
    sw a1, 4(sp)
    sw a2, 8(sp)
    sw a3, 12(sp)
    sw a4, 16(sp)
    li a1, 0
    li a2, 0
    li a3, 1
__getdec_{d}_l:
    jal ra, __getc_{d}
    li t2, 45
    bne t1, t2, __getdec_{d}_d
    beqz a3, __getdec_{d}_x
    li a2, 1
    j __getdec_{d}_e
__getdec_{d}_d:
    addi a4, t1, -48
    li t2, 10
    bgeu a4, t2, __getdec_{d}_x
    slli t2, a1, 3
    slli a1, a1, 1
    add a1, a1, t2
    add a1, a1, a4
__getdec_{d}_e:
    li a3, 0
    {e}
    j __getdec_{d}_l
__getdec_{d}_x:
    mv t1, a1
    beqz a2, __getdec_{d}_r
    sub t1, zero, t1
__getdec_{d}_r:
    lw ra, 0(sp)
    lw a1, 4(sp)
    lw a2, 8(sp)
    lw a3, 12(sp)
    lw a4, 16(sp)
    addi sp, sp, 20
    ret
'''.format(d=dev, e=e), ['__getc_' + dev] + (['__putc_' + dev] if echo else [])


def rt_gethex(dev, echo):
    """Read hex digits (0-9, A-F, a-f) into t1; the first other character ends it."""
    e = 'jal ra, __putc_%s' % dev if echo else ''
    return '''
__gethex_{d}:
    addi sp, sp, -12
    sw ra, 0(sp)
    sw a1, 4(sp)
    sw a4, 8(sp)
    li a1, 0
__gethex_{d}_l:
    jal ra, __getc_{d}
    addi a4, t1, -48
    li t2, 10
    bltu a4, t2, __gethex_{d}_a
    ori a4, t1, 32
    addi a4, a4, -97
    li t2, 6
    bgeu a4, t2, __gethex_{d}_x
    addi a4, a4, 10
__gethex_{d}_a:
    slli a1, a1, 4
    or a1, a1, a4
    {e}
    j __gethex_{d}_l
__gethex_{d}_x:
    mv t1, a1
    lw ra, 0(sp)
    lw a1, 4(sp)
    lw a4, 8(sp)
    addi sp, sp, 12
    ret
'''.format(d=dev, e=e), ['__getc_' + dev] + (['__putc_' + dev] if echo else [])


def rt_getline(dev, echo):
    """Read a line into the byte buffer at t1, at most t2 characters; return the length in t1.

    CR ends the line, LF is ignored, BS and DEL remove the last character, other control
    characters are ignored, characters beyond the buffer are ignored. A 0 is stored after the text.
    """
    e = (lambda ch: 'li t1, %d\n    jal ra, __putc_%s' % (ch, dev)) if echo else (lambda ch: '')
    return '''
__getline_{d}:
    addi sp, sp, -20
    sw ra, 0(sp)
    sw a1, 4(sp)
    sw a2, 8(sp)
    sw a3, 12(sp)
    sw a4, 16(sp)
    mv a1, t1
    mv a2, t2
    li a3, 0
__getline_{d}_l:
    jal ra, __getc_{d}
    li t2, 13
    beq t1, t2, __getline_{d}_x
    li t2, 8
    beq t1, t2, __getline_{d}_b
    li t2, 127
    beq t1, t2, __getline_{d}_b
    li t2, 32
    bltu t1, t2, __getline_{d}_l
    bgeu a3, a2, __getline_{d}_l
    add a4, a1, a3
    sb t1, 0(a4)
    addi a3, a3, 1
    {echo}
    j __getline_{d}_l
__getline_{d}_b:
    beqz a3, __getline_{d}_l
    addi a3, a3, -1
    {bs}
    {sp}
    {bs}
    j __getline_{d}_l
__getline_{d}_x:
    add a4, a1, a3
    sb zero, 0(a4)
    mv t1, a3
    lw ra, 0(sp)
    lw a1, 4(sp)
    lw a2, 8(sp)
    lw a3, 12(sp)
    lw a4, 16(sp)
    addi sp, sp, 20
    ret
'''.format(d=dev, echo='jal ra, __putc_%s' % dev if echo else '', bs=e(8), sp=e(32)), \
        ['__getc_' + dev] + (['__putc_' + dev] if echo else [])


def rt_putbuf(dev):
    """Print the byte buffer at t1, at most t2 characters, stopping at the first 0."""
    return '''
__putbuf_{d}:
    addi sp, sp, -12
    sw ra, 0(sp)
    sw a1, 4(sp)
    sw a2, 8(sp)
    mv a1, t1
    add a2, t1, t2
__putbuf_{d}_l:
    bgeu a1, a2, __putbuf_{d}_x
    lbu t1, 0(a1)
    beqz t1, __putbuf_{d}_x
    jal ra, __putc_{d}
    addi a1, a1, 1
    j __putbuf_{d}_l
__putbuf_{d}_x:
    lw ra, 0(sp)
    lw a1, 4(sp)
    lw a2, 8(sp)
    addi sp, sp, 12
    ret
'''.format(d=dev), ['__putc_' + dev]


def rt_puts(dev):
    return '''
__puts_{d}:
    addi sp, sp, -8
    sw ra, 0(sp)
    sw a1, 4(sp)
    mv a1, t1
__puts_{d}_l:
    lbu t1, 0(a1)
    beqz t1, __puts_{d}_x
    jal ra, __putc_{d}
    addi a1, a1, 1
    j __puts_{d}_l
__puts_{d}_x:
    lw ra, 0(sp)
    lw a1, 4(sp)
    addi sp, sp, 8
    ret
'''.format(d=dev), ['__putc_' + dev]


def rt_puthex(dev):
    return '''
__puthex_{d}:
    addi sp, sp, -16
    sw ra, 0(sp)
    sw a1, 4(sp)
    sw a2, 8(sp)
    sw a3, 12(sp)
    mv a1, t1
    mv a2, t2
__puthex_{d}_l:
    addi a2, a2, -1
    slli a3, a2, 2
    srl t1, a1, a3
    andi t1, t1, 15
    addi t1, t1, 48
    li t2, 58
    blt t1, t2, __puthex_{d}_o
    addi t1, t1, 7
__puthex_{d}_o:
    jal ra, __putc_{d}
    bnez a2, __puthex_{d}_l
    lw ra, 0(sp)
    lw a1, 4(sp)
    lw a2, 8(sp)
    lw a3, 12(sp)
    addi sp, sp, 16
    ret
'''.format(d=dev), ['__putc_' + dev]


def rt_putdec(dev):
    return '''
__putdec_{d}:
    addi sp, sp, -20
    sw ra, 0(sp)
    sw a1, 4(sp)
    sw a2, 8(sp)
    sw a3, 12(sp)
    sw a4, 16(sp)
    mv a1, t1
    beqz t2, __putdec_{d}_u
    bgez a1, __putdec_{d}_u
    sub a1, zero, a1
    li t1, 45
    jal ra, __putc_{d}
__putdec_{d}_u:
    la a2, __pow10
    li a3, 0
__putdec_{d}_l:
    lw a4, 0(a2)
    li t1, 48
__putdec_{d}_s:
    bltu a1, a4, __putdec_{d}_e
    sub a1, a1, a4
    addi t1, t1, 1
    j __putdec_{d}_s
__putdec_{d}_e:
    addi a2, a2, 4
    li t2, 1
    beq a4, t2, __putdec_{d}_p
    bnez a3, __putdec_{d}_p
    li t2, 48
    beq t1, t2, __putdec_{d}_l
__putdec_{d}_p:
    li a3, 1
    jal ra, __putc_{d}
    li t2, 1
    bne a4, t2, __putdec_{d}_l
    lw ra, 0(sp)
    lw a1, 4(sp)
    lw a2, 8(sp)
    lw a3, 12(sp)
    lw a4, 16(sp)
    addi sp, sp, 20
    ret
'''.format(d=dev), ['__putc_' + dev, '__pow10']

def rt_regs(text):
    """Runtime templates are written with t1/t2 for the argument registers; map them to T1/T2."""
    return re.sub(r'\bt2\b', ABI[T2], re.sub(r'\bt1\b', ABI[T1], text))

# ---------------------------------------------------------------- compiler

class Compiler:

    def __init__(self, fname='program.bas'):
        self.fname = fname
        self.src_dir = ''         # directory of the source file, for DWARF DW_AT_comp_dir
        self.em = Emitter()
        self.ext = self.em.ext
        self.target = 'RV32I'
        self.clock = None
        self.cpi = 1
        self.code_size = 8192
        self.ram_base = None
        self.ram_size = 0
        self.ram_next = 0
        self.gpval = 0
        self.stack = 256
        self.consts = {}
        self.aliases = {}
        self.vars = {}
        self.devices = {}
        self.funcs = {}
        self.scope = None
        self.cur_func = None
        self.blocks = []
        self.lab = 0
        self.warnings = []
        self.strings = {}
        self.rt_needed = []
        self.irq = {}
        self.has_irq = False
        self.uses_ra = False      # user code reads or writes RA (R1) directly
        self.console = None       # default device for PRINT (CONSOLE declaration)
        self.ram_syms = []        # (name, address, size) of RAM variables, for the ELF symbol table
        self.started = False
        self.in_device = None
        self.in_asm = False
        self.sizes = []
        self.line = 0
        self.t = []
        self.i = 0

    # ---- driver

    def compile(self, src):
        lines = src.splitlines()
        self.prescan(lines)
        for n, raw in enumerate(lines, 1):
            self.line = n
            self.em.line = n
            try:
                self.process(raw)
            except RBError as e:
                if e.line is None:
                    e.line = n
                raise
        self.line = len(lines)
        self.finish()

    def warn(self, msg):
        self.warnings.append('%s:%d: warning: %s' % (self.fname, self.line, msg))

    def prescan(self, lines):
        sig_re = re.compile(r'^\s*(SUB|FUNCTION)\s+([A-Za-z_]\w*)\s*(?:\((.*)\))?\s*(?:AS\s+([A-Za-z]+))?\s*$', re.I)
        for n, raw in enumerate(lines, 1):
            s = strip_comment(raw)
            if re.match(r'^\s*ON\s+IRQ\b', s, re.I):
                self.has_irq = True
            m = sig_re.match(s)
            if not m:
                continue
            kind, name = m.group(1).upper(), m.group(2).upper()
            if name in self.funcs:
                raise RBError("%s '%s' defined twice" % (kind, name), n)
            params = []
            if m.group(3) and m.group(3).strip():
                for p in m.group(3).split(','):
                    pm = re.match(r'^\s*([A-Za-z_]\w*)\s*(?:AS\s+([A-Za-z]+))?\s*$', p, re.I)
                    if not pm:
                        raise RBError("bad parameter '%s'" % p.strip(), n)
                    typ = (pm.group(2) or 'WORD').upper()
                    if typ not in TYPES:
                        raise RBError("unknown type '%s'" % typ, n)
                    params.append((pm.group(1).upper(), typ))
            rtype = (m.group(4) or 'WORD').upper()
            if rtype not in TYPES:
                raise RBError("unknown type '%s'" % rtype, n)
            w, sg, isbit = TYPES[rtype]
            if len(params) > len(PARAM_REGS):
                raise RBError('%s %s: at most %d parameters' % (kind, name, len(PARAM_REGS)), n)
            busy = set(PARAM_REGS[i] for i in range(len(params)))
            if kind == 'FUNCTION':
                busy.add(A0)
            self.funcs[name] = {'kind': kind, 'name': name, 'params': params,
                                'label': 'sub_' + name, 'signed': sg,
                                'width': 1 if isbit else w, 'rtype': rtype,
                                'busy': busy}

    def process(self, raw):
        if self.in_asm:
            self.em.comment('%04d  %s' % (self.line, raw.rstrip()))
            if re.match(r'^\s*END\s+ASM\b', raw, re.I):
                self.in_asm = False
                return
            asm_line(self.em, raw, _BasicResolver(self))
            return
        s = strip_comment(raw)
        toks = lex(s)
        if toks and self.in_device is None and not (toks[0].kind == 'id' and toks[0].up in DECLARATIONS | {'REM'}):
            self.ensure_start()
        if raw.strip():
            self.em.comment('%04d  %s' % (self.line, raw.strip()))
        if not toks or (toks[0].kind == 'id' and toks[0].up == 'REM'):
            return
        if self.in_device is not None:
            self.device_line(toks)
            return
        self.stmt(toks)

    # ---- token cursor

    def peek(self, k=0):
        j = self.i + k
        return self.t[j] if j < len(self.t) else EOL

    def next(self):
        tok = self.peek()
        self.i += 1
        return tok

    def at_end(self):
        return self.i >= len(self.t)

    def isop(self, v):
        tok = self.peek()
        return tok.kind == 'op' and tok.val == v

    def iskw(self, *kws):
        tok = self.peek()
        return tok.kind == 'id' and tok.up in kws

    def accept_op(self, v):
        if self.isop(v):
            self.i += 1
            return True
        return False

    def accept_kw(self, kw):
        if self.iskw(kw):
            self.i += 1
            return True
        return False

    def expect_op(self, v):
        if not self.accept_op(v):
            raise RBError("'%s' expected, found '%s'" % (v, self.peek()))

    def expect_kw(self, kw):
        if not self.accept_kw(kw):
            raise RBError("%s expected, found '%s'" % (kw, self.peek()))

    def expect_id(self):
        tok = self.next()
        if tok.kind != 'id':
            raise RBError("name expected, found '%s'" % tok)
        return tok

    def expect_end(self):
        if not self.at_end():
            raise RBError("unexpected '%s'" % self.peek())

    def newlabel(self):
        self.lab += 1
        return '_L%d' % self.lab

    # ---- expressions

    def expr(self):
        return self.p_or()

    def p_or(self):
        l = self.p_and()
        while self.iskw('OR', 'XOR'):
            op = self.next().up
            l = self.mk_bin(op, l, self.p_and())
        return l

    def p_and(self):
        l = self.p_not()
        while self.iskw('AND'):
            self.next()
            l = self.mk_bin('AND', l, self.p_not())
        return l

    def p_not(self):
        if self.accept_kw('NOT'):
            return self.mk_un('NOT', self.p_not())
        return self.p_cmp()

    def p_cmp(self):
        l = self.p_shift()
        tok = self.peek()
        if tok.kind == 'op' and tok.val in CMP_OPS:
            self.i += 1
            return self.mk_bin(tok.val, l, self.p_shift())
        return l

    def p_shift(self):
        l = self.p_add()
        while self.iskw('SHL', 'SHR', 'SAR'):
            op = self.next().up
            l = self.mk_bin(op, l, self.p_add())
        return l

    def p_add(self):
        l = self.p_mul()
        while self.isop('+') or self.isop('-'):
            op = self.next().val
            l = self.mk_bin(op, l, self.p_mul())
        return l

    def p_mul(self):
        l = self.p_unary()
        while self.isop('*') or self.isop('/') or self.iskw('MOD'):
            tok = self.next()
            op = tok.up if tok.kind == 'id' else tok.val
            l = self.mk_bin(op, l, self.p_unary())
        return l

    def p_unary(self):
        if self.accept_op('-'):
            return self.mk_un('-', self.p_unary())
        if self.accept_op('+'):
            return self.p_unary()
        return self.primary()

    def primary(self):
        tok = self.peek()
        if tok.kind == 'num':
            self.i += 1
            return Node('const', tok.val)
        if tok.kind == 'str':
            self.i += 1
            if len(tok.val) != 1:
                raise RBError('only one-character strings can be used as values (use PRINT for text)')
            return Node('const', ord(tok.val))
        if self.accept_op('('):
            n = self.expr()
            self.expect_op(')')
            return n
        if tok.kind == 'id':
            return self.ref()
        raise RBError("value expected, found '%s'" % tok)

    def mk_bin(self, op, l, r):
        if l.k == 'const' and r.k == 'const':
            return Node('const', fold(op, l.a, r.a))
        isb = op in CMP_OPS or (op in ('AND', 'OR', 'XOR') and l.isbool and r.isbool)
        return Node('bin', (op, l, r), signed=l.signed or r.signed, isbool=isb)

    def mk_un(self, op, x):
        if x.k == 'const':
            return Node('const', -x.a if op == '-' else ~x.a)
        return Node('un', (op, x), signed=x.signed, isbool=(op == 'NOT' and x.isbool))

    def const_expr(self):
        n = self.expr()
        if n.k != 'const':
            raise RBError('constant expression expected')
        return n.a

    def obj_node(self, o):
        if o.kind == 'sub':
            return Node('obj', o, isbool=(o.w == 1), width=o.w)
        return Node('obj', o, signed=o.signed, isbool=o.isbit, width=1 if o.isbit else o.width)

    def make_sub(self, o, lo, w):
        if o.kind == 'sub':
            if lo + w > o.w:
                raise RBError('bit %d outside field of %d bits' % (lo + w - 1, o.w))
            s = self.make_sub(o.base, o.lo + lo, w)
            s.inv = o.inv
            return s
        if o.kind not in ('reg', 'ram', 'loc', 'arr'):
            raise RBError('bits can only be selected from registers, variables and device registers')
        if lo < 0 or w < 1 or lo + w > o.width:
            raise RBError('bit %d outside %d-bit object' % (lo + w - 1, o.width))
        return Obj('sub', o.name, base=o, lo=lo, w=w, width=w)

    def lookup_var(self, u):
        if self.scope is not None and u in self.scope:
            return self.scope[u]
        o = self.vars.get(u)
        if o is not None and self.cur_func and o.kind == 'reg' and o.reg in self.cur_func['busy']:
            raise RBError("global register variable '%s' (%s) is hidden by the parameters of %s"
                          % (u, ABI[o.reg], self.cur_func['name']))
        return o

    def ref(self):
        tok = self.expect_id()
        u = tok.up
        o = self.lookup_var(u)
        node = None
        if o is not None:
            if o.kind == 'array':
                self.expect_op('(')
                idx = self.expr()
                self.expect_op(')')
                if idx.k == 'const' and not 0 <= idx.a < o.count:
                    raise RBError('index %d out of range for %s(%d)' % (idx.a, u, o.count))
                o = Obj('arr', u, addr=o.addr, width=o.width, signed=o.signed,
                        isbit=o.isbit, count=o.count, index=idx)
            node = self.obj_node(o)
        elif u in ('MEM8', 'MEM16', 'MEM32'):
            self.expect_op('(')
            a = self.expr()
            self.expect_op(')')
            w = int(u[3:])
            if a.k == 'const':
                o = Obj('loc', u, width=w, addr=a.a & 0xFFFFFFFF)
            else:
                o = Obj('loc', u, width=w, aexpr=a)
            node = self.obj_node(o)
        elif u in self.consts:
            node = Node('const', self.consts[u])
        elif u in self.aliases:
            node = self.aliases[u]
        elif u in BASIC_REGS:
            r = BASIC_REGS[u]
            if r in (T0, T1, T2):
                raise RBError('%s is reserved for the compiler' % tok.val)
            if r == RA:
                self.uses_ra = True
            if 'E' in self.ext and r > 15:
                raise RBError('register %s does not exist on RV32E' % tok.val)
            node = self.obj_node(Obj('reg', u, reg=r))
        elif u in self.devices:
            dev = self.devices[u]
            self.expect_op('.')
            mu = self.expect_id().up
            if mu in dev['regs']:
                node = self.obj_node(dev['regs'][mu])
            elif mu in dev['bits']:
                node = self.obj_node(dev['bits'][mu])
            else:
                raise RBError("device %s has no member '%s'" % (u, mu))
        elif u in self.funcs:
            f = self.funcs[u]
            if f['kind'] != 'FUNCTION':
                raise RBError("SUB %s has no value; use FUNCTION" % u)
            args = self.arglist(True)
            return Node('call', (u, args), signed=f['signed'], width=f['width'])
        else:
            raise RBError("unknown name '%s'" % tok.val)
        while node.k == 'obj':
            if self.isop('.') and self.peek(1).kind == 'num':
                self.i += 1
                b = self.next().val
                node = self.obj_node(self.make_sub(node.a, b, 1))
            elif self.isop('['):
                self.i += 1
                hi = self.const_expr()
                lo = hi
                if self.accept_op(':'):
                    lo = self.const_expr()
                self.expect_op(']')
                if lo > hi:
                    raise RBError('field [%d:%d]: high bit first' % (hi, lo))
                node = self.obj_node(self.make_sub(node.a, lo, hi - lo + 1))
            else:
                break
        return node

    def arglist(self, paren):
        args = []
        if paren:
            self.expect_op('(')
            if self.accept_op(')'):
                return args
        elif self.at_end():
            return args
        while True:
            args.append(self.expr())
            if not self.accept_op(','):
                break
        if paren:
            self.expect_op(')')
        return args

    # ---- code generation helpers

    def push(self, r):
        self.em.ins('addi', SP, SP, -4)
        self.em.ins('sw', r, 0, SP)

    def pop(self, r):
        self.em.ins('lw', r, 0, SP)
        self.em.ins('addi', SP, SP, 4)

    @staticmethod
    def ld(width, signed):
        return {8: 'lb' if signed else 'lbu', 16: 'lh' if signed else 'lhu', 32: 'lw'}[width]

    @staticmethod
    def st(width):
        return {8: 'sb', 16: 'sh', 32: 'sw'}[width]

    def addr_base(self, addr):
        """Return (base register, offset) for an absolute address."""
        addr &= 0xFFFFFFFF
        if self.ram_base is not None and self.ram_base <= addr < self.ram_base + self.ram_size:
            ofs = addr - self.gpval
            if fits12(ofs):
                return GP, ofs
        if fits12(s32(addr)):
            return ZERO, s32(addr)
        hi, lo = split_hi_lo(addr)
        self.em.ins('lui', T0, hi)
        return T0, lo

    def leaf_obj(self, o):
        if o.kind in ('reg', 'ram'):
            return True
        if o.kind == 'loc':
            return o.addr is not None
        if o.kind == 'sub':
            return self.leaf_obj(o.base)
        return False

    def leafish(self, n):
        return n.k == 'const' or (n.k == 'obj' and self.leaf_obj(n.a))

    def extract(self, dst, src, lo, w):
        em = self.em
        if lo == 0 and w == 32:
            em.mv(dst, src)
        elif lo + w == 32:
            em.ins('srli', dst, src, lo)
        elif w <= 11:
            if lo:
                em.ins('srli', dst, src, lo)
                em.ins('andi', dst, dst, (1 << w) - 1)
            else:
                em.ins('andi', dst, src, (1 << w) - 1)
        else:
            em.ins('slli', dst, src, 32 - lo - w)
            em.ins('srli', dst, dst, 32 - w)

    def arr_addr(self, o):
        """Address of an array element: returns (base reg, offset). Uses T1 (and T0)."""
        size = o.width // 8
        idx = o.index
        if idx.k == 'const':
            return self.addr_base(o.addr + idx.a * size)
        r = self.gen(idx, T1)
        if size == 2:
            self.em.ins('slli', T1, r, 1)
            r = T1
        elif size == 4:
            self.em.ins('slli', T1, r, 2)
            r = T1
        if self.ram_base is not None and fits12(o.addr - self.gpval) and \
                self.ram_base <= o.addr < self.ram_base + self.ram_size:
            self.em.ins('add', T1, r, GP)
            return T1, o.addr - self.gpval
        hi, lo = split_hi_lo(o.addr)
        self.em.ins('lui', T0, hi)
        self.em.ins('add', T1, r, T0)
        return T1, lo

    def load_obj(self, o, dst, force=False):
        em = self.em
        k = o.kind
        if k == 'reg':
            if force:
                em.mv(dst, o.reg)
                return dst
            return o.reg
        if k == 'ram':
            base, ofs = self.addr_base(o.addr)
            em.ins(self.ld(o.width, o.signed), dst, ofs, base)
            return dst
        if k == 'loc':
            if o.addr is not None:
                base, ofs = self.addr_base(o.addr)
                em.ins(self.ld(o.width, o.signed), dst, ofs, base)
            else:
                r = self.gen(o.aexpr, T1)
                em.ins(self.ld(o.width, o.signed), dst, 0, r)
            return dst
        if k == 'arr':
            base, ofs = self.arr_addr(o)
            em.ins(self.ld(o.width, o.signed), dst, ofs, base)
            return dst
        if k == 'sub':
            src = o.base.reg if o.base.kind == 'reg' else self.load_obj(o.base, dst, True)
            if o.inv:
                em.ins('xori', dst, src, -1)
                src = dst
            self.extract(dst, src, o.lo, o.w)
            return dst
        raise RBError('cannot read %s' % o.name)

    def gen(self, n, dst=T1, force=False):
        """Generate code for expression n. Returns the register holding the value.

        The result lands in dst unless it is already in a register (user
        register variable, or zero for constant 0) and force is False.
        """
        em = self.em
        k = n.k
        if k == 'const':
            if n.a == 0 and not force:
                return ZERO
            em.li(dst, n.a)
            return dst
        if k == 'obj':
            return self.load_obj(n.a, dst, force)
        if k == 'un':
            op, x = n.a
            r = self.gen(x, T1)
            if op == '-':
                em.ins('sub', dst, ZERO, r)
            else:
                em.ins('xori', dst, r, 1 if x.isbool else -1)
            return dst
        if k == 'call':
            self.gen_call(n.a[0], n.a[1], True)
            em.mv(dst, T1)
            return dst
        return self.gen_bin(n, dst)

    def imm_ops(self, op, c, signed):
        """Immediate-form instruction sequence for 'x op c', or None."""
        c32 = s32(c & 0xFFFFFFFF) if -(1 << 31) <= c <= 0xFFFFFFFF else None
        if c32 is None:
            return None
        if op == '+' and fits12(c32):
            return [('addi', c32)]
        if op == '-' and fits12(-c32):
            return [('addi', -c32)]
        if op in ('AND', 'OR', 'XOR') and fits12(c32):
            return [({'AND': 'andi', 'OR': 'ori', 'XOR': 'xori'}[op], c32)]
        if op in ('SHL', 'SHR', 'SAR'):
            if not 0 <= c <= 31:
                raise RBError('shift count %d out of range' % c)
            return [({'SHL': 'slli', 'SHR': 'srli', 'SAR': 'srai'}[op], c)]
        pow2 = c > 0 and (c & (c - 1)) == 0
        if op == '*' and pow2:
            return [('slli', c.bit_length() - 1)] if c > 1 else [('addi', 0)]
        if op == '/' and pow2 and not signed:
            return [('srli', c.bit_length() - 1)] if c > 1 else [('addi', 0)]
        if op == 'MOD' and pow2 and not signed and fits12(c - 1):
            return [('andi', c - 1)]
        if op == '<' and fits12(c32):
            return [('slti' if signed else 'sltiu', c32)]
        if op == '>=' and fits12(c32):
            return [('slti' if signed else 'sltiu', c32), ('xori', 1)]
        if op == '=' and fits12(c32):
            return [('sltiu', 1)] if c32 == 0 else [('xori', c32), ('sltiu', 1)]
        if op == '<>' and fits12(c32):
            return [('snez', None)] if c32 == 0 else [('xori', c32), ('snez', None)]
        return None

    def gen_bin(self, n, dst):
        em = self.em
        op, L, R = n.a
        sg = n.signed
        if R.k == 'const':
            ops = self.imm_ops(op, R.a, sg)
            if ops is not None:
                src = self.gen(L, T1)
                for mn, imm in ops:
                    if mn == 'snez':
                        em.ins('sltu', dst, ZERO, src)
                    else:
                        em.ins(mn, dst, src, imm)
                    src = dst
                return dst
        if self.leafish(R):
            a = self.gen(L, T1)
            b = self.gen(R, T2)
        else:
            r = self.gen(R, T1)
            self.push(r)
            a = self.gen(L, T1)
            self.pop(T2)
            b = T2
        self.emit_op(op, dst, a, b, sg)
        return dst

    def emit_op(self, op, dst, a, b, sg):
        em = self.em
        simple = {'+': 'add', '-': 'sub', 'AND': 'and', 'OR': 'or', 'XOR': 'xor',
                  'SHL': 'sll', 'SHR': 'srl', 'SAR': 'sra'}
        slt = 'slt' if sg else 'sltu'
        if op in simple:
            em.ins(simple[op], dst, a, b)
        elif op == '<':
            em.ins(slt, dst, a, b)
        elif op == '>':
            em.ins(slt, dst, b, a)
        elif op == '<=':
            em.ins(slt, dst, b, a)
            em.ins('xori', dst, dst, 1)
        elif op == '>=':
            em.ins(slt, dst, a, b)
            em.ins('xori', dst, dst, 1)
        elif op == '=':
            em.ins('xor', dst, a, b)
            em.ins('sltiu', dst, dst, 1)
        elif op == '<>':
            em.ins('xor', dst, a, b)
            em.ins('sltu', dst, ZERO, dst)
        elif op in ('*', '/', 'MOD'):
            if 'M' in self.ext:
                mn = {'*': 'mul', '/': 'div' if sg else 'divu', 'MOD': 'rem' if sg else 'remu'}[op]
                em.ins(mn, dst, a, b)
            else:
                em.mv(T2, b)
                em.mv(T1, a)
                name = '__mul' if op == '*' else ('__div' if sg else '__divu')
                self.need(name)
                em.ins('jal', RA, name)
                em.mv(dst, T2 if op == 'MOD' else T1)
        else:
            raise RBError("unknown operator '%s'" % op)

    def gen_branch(self, cond, label, when):
        """Branch to label if cond is true (when=True) or false (when=False)."""
        em = self.em
        if cond.k == 'const':
            if bool(cond.a) == when:
                em.j(label)
            return
        if cond.k == 'un' and cond.a[0] == 'NOT' and cond.a[1].isbool:
            self.gen_branch(cond.a[1], label, not when)
            return
        if cond.k == 'bin' and cond.a[0] in CMP_OPS:
            op, L, R = cond.a
            if R.k == 'const' and R.a == 0:
                a, b = self.gen(L, T1), ZERO
            elif self.leafish(R):
                a = self.gen(L, T1)
                b = self.gen(R, T2)
            else:
                r = self.gen(R, T1)
                self.push(r)
                a = self.gen(L, T1)
                self.pop(T2)
                b = T2
            t, f, swap = {'=': ('beq', 'bne', False), '<>': ('bne', 'beq', False),
                          '<': ('blt', 'bge', False), '>=': ('bge', 'blt', False),
                          '>': ('blt', 'bge', True), '<=': ('bge', 'blt', True)}[op]
            mn = t if when else f
            if mn in ('blt', 'bge') and not cond.signed:
                mn += 'u'
            x, y = (b, a) if swap else (a, b)
            em.ins(mn, x, y, label)
            return
        if cond.k == 'obj' and cond.a.kind == 'sub' and cond.a.w == 1 and cond.a.lo <= 10 \
                and self.leaf_obj(cond.a.base):
            o = cond.a
            src = o.base.reg if o.base.kind == 'reg' else self.load_obj(o.base, T1, True)
            em.ins('andi', T1, src, 1 << o.lo)
            if o.inv:                         # NOT bit: test the raw bit the other way round
                when = not when
            em.ins('bne' if when else 'beq', T1, ZERO, label)
            return
        r = self.gen(cond, T1)
        em.ins('bne' if when else 'beq', r, ZERO, label)

    def need(self, name):
        if name not in self.rt_needed:
            self.rt_needed.append(name)

    # ---- assignment

    def check_write_reg(self, r):
        if r == ZERO:
            raise RBError('R0 (zero) is read-only')
        if r in (T0, T1, T2):
            raise RBError('s0, s1 and a5 are reserved for the compiler')
        if r == GP:
            raise RBError('GP is reserved for RAM variable access')
        if r == SP:
            self.warn('writing SP')

    def fit_const(self, v, width, signed, what):
        mask = (1 << width) - 1
        lo, hi = (-(1 << (width - 1)), (1 << (width - 1)) - 1) if signed else (0, mask)
        if width < 32 and not (lo <= v <= hi or -(1 << (width - 1)) <= v <= mask):
            self.warn('constant %d truncated to %d bits for %s' % (v, width, what))
        v &= mask
        if signed and v & (1 << (width - 1)):
            v -= 1 << width
        return v

    def normalize_reg(self, rd, o):
        """Bring a narrow register variable back into its type range."""
        em = self.em
        w = o.width
        if o.isbit or w == 32:
            return
        if o.signed:
            em.ins('slli', rd, rd, 32 - w)
            em.ins('srai', rd, rd, 32 - w)
        elif w <= 11:
            em.ins('andi', rd, rd, (1 << w) - 1)
        else:
            em.ins('slli', rd, rd, 32 - w)
            em.ins('srli', rd, rd, 32 - w)

    def source_fits(self, n, o):
        if n.isbool:
            return True
        if n.k == 'obj' and n.width <= o.width and (n.signed == o.signed or n.width < o.width and not n.signed):
            return True
        return False

    def assign(self, o, n):
        k = o.kind
        if k == 'reg':
            self.assign_reg(o, n)
        elif k == 'ram':
            self.assign_mem(o, n)
        elif k == 'arr':
            self.assign_arr(o, n)
        elif k == 'loc':
            self.assign_loc(o, n)
        elif k == 'sub':
            self.assign_sub(o, n)
        else:
            raise RBError('cannot assign to %s' % o.name)

    def assign_reg(self, o, n):
        em = self.em
        rd = o.reg
        self.check_write_reg(rd)
        if n.k == 'const':
            v = (1 if n.a else 0) if o.isbit else self.fit_const(n.a, o.width, o.signed, o.name)
            em.li(rd, v)
            return
        if o.isbit and not n.isbool:
            r = self.gen(n, T1)
            em.ins('sltu', rd, ZERO, r)
            return
        self.gen(n, rd, True)
        if not self.source_fits(n, o):
            self.normalize_reg(rd, o)

    def assign_mem(self, o, n):
        """Store to a RAM variable or constant-address location."""
        em = self.em
        if o.isbit and not n.isbool and n.k != 'const':
            r = self.gen(n, T1)
            em.ins('sltu', T1, ZERO, r)
            r = T1
        elif n.k == 'const':
            v = (1 if n.a else 0) if o.isbit else self.fit_const(n.a, o.width, o.signed, o.name)
            if v == 0:
                r = ZERO
            else:
                em.li(T1, v)
                r = T1
        else:
            if n.k == 'obj' and n.width > o.width and not n.isbool:
                self.warn('value truncated to %d bits for %s' % (o.width, o.name))
            r = self.gen(n, T1)
        base, ofs = self.addr_base(o.addr)
        em.ins(self.st(o.width), r, ofs, base)

    def assign_arr(self, o, n):
        em = self.em
        if o.index.k == 'const':
            self.assign_mem(Obj('ram', o.name, addr=o.addr + o.index.a * (o.width // 8),
                                width=o.width, signed=o.signed, isbit=o.isbit), n)
            return
        if o.isbit and not n.isbool:
            r = self.gen(n, T1)
            em.ins('sltu', T1, ZERO, r)
            r = T1
        else:
            r = self.gen(n, T1)
        pushed = r == T1
        if pushed:
            self.push(T1)
        base, ofs = self.arr_addr(o)
        if pushed:
            self.pop(T2)
            r = T2
        em.ins(self.st(o.width), r, ofs, base)

    def assign_loc(self, o, n):
        em = self.em
        if o.readonly:
            raise RBError('%s is READONLY' % o.name)
        if o.addr is not None:
            self.assign_mem(o, n)
            return
        r = self.gen(n, T1)
        pushed = r == T1
        if pushed:
            self.push(T1)
        a = self.gen(o.aexpr, T1)
        if pushed:
            self.pop(T2)
            r = T2
        em.ins(self.st(o.width), r, 0, a)

    def and_not(self, rd, src, mask, tmp):
        nm = s32(~mask)
        if fits12(nm):
            self.em.ins('andi', rd, src, nm)
        else:
            self.em.li(tmp, ~mask & 0xFFFFFFFF)
            self.em.ins('and', rd, src, tmp)

    def or_imm(self, rd, src, v, tmp):
        if fits12(s32(v)):
            self.em.ins('ori', rd, src, s32(v))
        else:
            self.em.li(tmp, v)
            self.em.ins('or', rd, src, tmp)

    def position(self, n, lo, w, inv=False):
        """Value in T1 -> (inverted if inv,) masked to w bits and shifted to bit lo."""
        em = self.em
        if w == 1:
            if inv:
                if n.isbool:
                    em.ins('xori', T1, T1, 1)
                else:
                    em.ins('sltiu', T1, T1, 1)     # seqz: non-zero -> 0, zero -> 1
            elif not n.isbool:
                em.ins('sltu', T1, ZERO, T1)
            if lo:
                em.ins('slli', T1, T1, lo)
            return
        if inv:
            em.ins('xori', T1, T1, -1)
        if lo + w == 32:
            if lo:
                em.ins('slli', T1, T1, lo)
        elif w <= 11:
            em.ins('andi', T1, T1, (1 << w) - 1)
            if lo:
                em.ins('slli', T1, T1, lo)
        else:
            em.ins('slli', T1, T1, 32 - w)
            em.ins('srli', T1, T1, 32 - w - lo)

    def assign_sub(self, o, n):
        em = self.em
        base, lo, w = o.base, o.lo, o.w
        fmask = (1 << w) - 1
        mask = (fmask << lo) & 0xFFFFFFFF
        if n.k == 'const':
            c = n.a
            if w == 1:
                c = 1 if c else 0
            elif not -(1 << (w - 1)) <= c <= fmask:
                self.warn('constant %d truncated to %d bits for %s' % (c, w, o.name))
            if o.inv:
                c = ~c
            setbits = ((c & fmask) << lo) & 0xFFFFFFFF
        if base.kind == 'reg':
            rd = base.reg
            self.check_write_reg(rd)
            if n.k == 'const':
                if setbits != mask:
                    self.and_not(rd, rd, mask, T0)
                if setbits:
                    self.or_imm(rd, rd, setbits, T0)
            else:
                self.gen(n, T1, True)
                self.position(n, lo, w, o.inv)
                self.and_not(rd, rd, mask, T0)
                em.ins('or', rd, rd, T1)
            if base.width < 32 and base.signed and lo + w == base.width:
                self.normalize_reg(rd, base)
            return
        if base.kind not in ('ram', 'loc') or base.addr is None:
            raise RBError('bit and field assignment needs a register, RAM variable or device register')
        if base.readonly:
            raise RBError('%s is READONLY' % base.name)
        S, C = base.setaddr, base.clraddr
        st = self.st(base.width)
        if n.k == 'const':
            if w == 1 and setbits and S is not None:
                em.li(T1, mask)
                b, ofs = self.addr_base(S)
                em.ins(st, T1, ofs, b)
                return
            if w == 1 and not setbits and C is not None:
                em.li(T1, mask)
                b, ofs = self.addr_base(C)
                em.ins(st, T1, ofs, b)
                return
            if w > 1 and S is not None and C is not None:
                em.li(T1, mask)
                b, ofs = self.addr_base(C)
                em.ins(st, T1, ofs, b)
                if setbits:
                    em.li(T1, setbits)
                    b, ofs = self.addr_base(S)
                    em.ins(st, T1, ofs, b)
                return
            b, ofs = self.addr_base(base.addr)
            em.ins(self.ld(base.width, False), T1, ofs, b)
            if setbits != mask:
                self.and_not(T1, T1, mask, T2)
            if setbits:
                self.or_imm(T1, T1, setbits, T2)
            em.ins(st, T1, ofs, b)
            return
        if S is not None and C is not None:
            if w == 1:
                if o.inv:                     # NOT bit: value 1 clears, value 0 sets
                    S, C = C, S
                r = self.gen(n, T1)
                lclr, lend = self.newlabel(), self.newlabel()
                em.li(T2, mask)
                em.ins('beq', r, ZERO, lclr)
                b, ofs = self.addr_base(S)
                em.ins(st, T2, ofs, b)
                em.j(lend)
                em.label(lclr)
                b, ofs = self.addr_base(C)
                em.ins(st, T2, ofs, b)
                em.label(lend)
            else:
                self.gen(n, T1, True)
                self.position(n, lo, w, o.inv)
                em.li(T2, mask)
                b, ofs = self.addr_base(C)
                em.ins(st, T2, ofs, b)
                b, ofs = self.addr_base(S)
                em.ins(st, T1, ofs, b)
            return
        self.gen(n, T1, True)
        self.position(n, lo, w, o.inv)
        b, ofs = self.addr_base(base.addr)
        em.ins(self.ld(base.width, False), T2, ofs, b)
        nm = s32(~mask)
        if fits12(nm):
            em.ins('andi', T2, T2, nm)
        else:
            em.li(T0, ~mask & 0xFFFFFFFF)
            em.ins('and', T2, T2, T0)
            if b == T0:
                b, ofs = self.addr_base(base.addr)
        em.ins('or', T2, T2, T1)
        em.ins(st, T2, ofs, b)

    # ---- calls

    def gen_call(self, name, args, result):
        em = self.em
        f = self.funcs[name]
        if len(args) != len(f['params']):
            raise RBError('%s expects %d arguments, got %d' % (name, len(f['params']), len(args)))
        if 'E' in self.ext and len(args) > 5:
            raise RBError('RV32E allows at most 5 parameters')
        used = set(PARAM_REGS[i] for i in range(len(args)))
        if f['kind'] == 'FUNCTION':
            used.add(A0)
        live = set(o.reg for o in self.vars.values() if o.kind == 'reg')
        if self.cur_func:
            live |= set(PARAM_REGS[i] for i in range(len(self.cur_func['params'])))
        save = sorted(r for r in live if r in used)
        for r in save:
            self.push(r)
        consts, pushed = {}, []
        for i, a in enumerate(args):
            if a.k == 'const':
                consts[i] = a.a
            else:
                self.push(self.gen(a, T1))
                pushed.append(i)
        for i in reversed(pushed):
            self.pop(PARAM_REGS[i])
        for i, v in consts.items():
            em.li(PARAM_REGS[i], v)
        em.ins('jal', RA, f['label'])
        if result:
            em.mv(T1, A0)
        for r in reversed(save):
            self.pop(r)

    # ---- statements

    def stmt(self, toks):
        self.t, self.i = toks, 0
        tok = self.peek()
        if tok.kind == 'id' and self.peek(1).kind == 'op' and self.peek(1).val == ':' \
                and tok.up not in KEYWORDS:
            self.ensure_start()
            self.em.label('L_' + tok.up)
            self.i = 2
            if self.at_end():
                return
            tok = self.peek()
        self.dispatch()

    def dispatch(self):
        tok = self.peek()
        if tok.kind != 'id':
            raise RBError("statement expected, found '%s'" % tok)
        kw = tok.up
        if kw not in DECLARATIONS:
            self.ensure_start()
        h = getattr(self, 'st_' + kw.lower(), None) if kw in KEYWORDS else None
        if h:
            self.i += 1
            h()
        elif kw in self.funcs and not self.isop_at(1, '='):
            self.i += 1
            paren = self.isop('(')
            self.gen_call(kw, self.arglist(paren), False)
        else:
            self.st_let()
        self.expect_end()

    def isop_at(self, k, v):
        tok = self.peek(k)
        return tok.kind == 'op' and tok.val == v

    def sub_stmt(self, toks):
        saved = self.t, self.i
        self.t, self.i = toks, 0
        try:
            if not toks:
                raise RBError('statement expected')
            self.dispatch()
        finally:
            self.t, self.i = saved

    def ensure_start(self):
        if self.started:
            return
        if self.ram_base is None:
            raise RBError('RAM must be declared before the first statement')
        self.started = True
        em = self.em
        line, em.line = em.line, 0          # startup code has no source line
        em.head('startup')
        em.label('__start')
        em.li(SP, self.ram_base + self.ram_size)
        em.li(GP, self.gpval)
        em.li(T1, self.ram_base)
        em.la(T2, '__ram_end')
        em.label('__clr')
        em.ins('bgeu', T1, T2, '__clr_done')
        em.ins('sw', ZERO, 0, T1)
        em.ins('addi', T1, T1, 4)
        em.j('__clr')
        em.label('__clr_done')
        if self.has_irq:
            em.la(T1, '__trap')
            em.ins('csrrw', ZERO, CSRS['mtvec'], T1)
        em.head('program')
        em.label('main')                    # where the BASIC program starts (debuggers stop here)
        em.line = line

    # declarations

    def st_target(self):
        t = self.expect_id().up
        if t not in ('RV32I', 'RV32IC', 'RV32E', 'RV32EC'):
            raise RBError("unknown target '%s'" % t)
        self.target = t
        if 'E' in t[4:]:
            self.ext.add('E')
        if 'C' in t[4:]:
            self.ext.add('C')
        while self.accept_op('+'):
            e = self.expect_id().up
            if e not in ('M', 'ZBB'):
                raise RBError("unknown extension '%s' (supported: M, Zbb)" % e)
            self.ext.add(e)
            self.target += '+' + ('Zbb' if e == 'ZBB' else e)

    def st_clock(self):
        self.clock = self.const_expr()
        if self.clock <= 0:
            raise RBError('CLOCK must be positive')

    def st_cpi(self):
        self.cpi = self.const_expr()
        if self.cpi <= 0:
            raise RBError('CPI must be positive')

    def st_code(self):
        if self.em.code:
            raise RBError('CODE must be declared before the first statement')
        self.expect_kw('AT')
        self.em.base = self.const_expr()
        if self.em.base & 3:
            raise RBError('CODE address must be word aligned')
        self.expect_kw('SIZE')
        self.code_size = self.const_expr()

    def st_ram(self):
        if self.ram_base is not None:
            raise RBError('RAM declared twice')
        self.expect_kw('AT')
        self.ram_base = self.const_expr()
        if self.ram_base & 3:
            raise RBError('RAM address must be word aligned')
        self.expect_kw('SIZE')
        self.ram_size = self.const_expr()
        self.ram_next = self.ram_base
        self.gpval = self.ram_base + 2048

    def st_stack(self):
        self.stack = self.const_expr()

    def check_newname(self, u):
        if u in KEYWORDS or u in BASIC_REGS:
            raise RBError("'%s' is reserved" % u)
        scope = self.scope if self.scope is not None else self.vars
        if u in scope or u in self.consts or u in self.devices or u in self.aliases or u in self.funcs:
            raise RBError("'%s' is already defined" % u)

    def st_const(self):
        u = self.expect_id().up
        self.check_newname(u)
        self.expect_op('=')
        self.consts[u] = self.const_expr()

    def st_alias(self):
        u = self.expect_id().up
        self.check_newname(u)
        self.expect_op('=')
        inv = self.accept_kw('NOT')
        n = self.ref()
        if n.k not in ('obj', 'const'):
            raise RBError('ALIAS needs an object')
        if inv:
            o = n.a if n.k == 'obj' else None
            if o is None or o.kind != 'sub':
                raise RBError('ALIAS ... = NOT needs a bit or field')
            o = Obj('sub', o.name, base=o.base, lo=o.lo, w=o.w, width=o.w, inv=not o.inv)
            n = self.obj_node(o)
        self.aliases[u] = n

    def alloc_ram(self, size, align):
        self.ram_next = (self.ram_next + align - 1) & ~(align - 1)
        addr = self.ram_next
        self.ram_next += size
        return addr

    def st_dim(self):
        while True:
            u = self.expect_id().up
            self.check_newname(u)
            count = None
            if self.accept_op('('):
                count = self.const_expr()
                self.expect_op(')')
                if count < 1:
                    raise RBError('array size must be at least 1')
            typ = 'WORD'
            if self.accept_kw('AS'):
                typ = self.expect_id().up
                if typ not in TYPES:
                    raise RBError("unknown type '%s'" % typ)
            w, sg, isbit = TYPES[typ]
            if self.accept_kw('IN'):
                if count:
                    raise RBError('arrays cannot live in a register')
                rt = self.expect_id()
                r = BASIC_REGS.get(rt.up)
                if r is None:
                    raise RBError("'%s' is not a register" % rt.val)
                if r in (ZERO, SP, GP, T0, T1, T2):
                    raise RBError('%s cannot hold a variable' % rt.val)
                if 'E' in self.ext and r > 15:
                    raise RBError('register %s does not exist on RV32E' % rt.val)
                if r == RA:
                    self.uses_ra = True
                taken = [k for k, v in self.vars.items() if v.kind == 'reg' and v.reg == r]
                if self.scope:
                    taken += [k for k, v in self.scope.items() if v.kind == 'reg' and v.reg == r]
                if taken:
                    raise RBError('register %s already holds %s' % (rt.val, taken[0]))
                o = Obj('reg', u, reg=r, width=32 if isbit else w, signed=sg, isbit=isbit)
            else:
                if self.ram_base is None:
                    raise RBError('RAM must be declared before DIM')
                size = w // 8
                if count:
                    o = Obj('array', u, addr=self.alloc_ram(size * count, size), width=w,
                            signed=sg, isbit=isbit, count=count)
                else:
                    o = Obj('ram', u, addr=self.alloc_ram(size, size), width=w, signed=sg, isbit=isbit)
                qual = '%s.%s' % (self.cur_func['name'], u) if self.cur_func else u
                self.ram_syms.append((qual, o.addr, size * (count or 1)))
            (self.scope if self.scope is not None else self.vars)[u] = o
            if not self.accept_op(','):
                break

    def st_device(self):
        u = self.expect_id().up
        self.check_newname(u)
        self.expect_kw('AT')
        base = self.const_expr()
        self.in_device = {'name': u, 'base': base, 'regs': {}, 'bits': {}}

    def device_line(self, toks):
        self.t, self.i = toks, 0
        d = self.in_device
        kw = self.expect_id().up
        if kw == 'END':
            self.expect_kw('DEVICE')
            self.devices[d['name']] = d
            self.in_device = None
        elif kw == 'REG':
            name = self.expect_id().up
            self.expect_kw('AT')
            ofs = self.const_expr()
            width = 32
            if self.accept_op(':'):
                width = self.const_expr()
                if width not in (8, 16, 32):
                    raise RBError('register width must be 8, 16 or 32')
            o = Obj('loc', '%s.%s' % (d['name'], name), addr=(d['base'] + ofs) & 0xFFFFFFFF, width=width)
            while not self.at_end():
                k = self.expect_id().up
                if k == 'READONLY':
                    o.readonly = True
                elif k in ('SETS', 'CLEARS'):
                    tn = self.expect_id().up
                    tgt = d['regs'].get(tn)
                    if tgt is None:
                        raise RBError("%s: register '%s' must be declared first" % (k, tn))
                    if k == 'SETS':
                        tgt.setaddr = o.addr
                    else:
                        tgt.clraddr = o.addr
                else:
                    raise RBError("unexpected '%s'" % k)
            d['regs'][name] = o
        elif kw in ('BIT', 'FIELD'):
            name = self.expect_id().up
            self.expect_op('=')
            inv = self.accept_kw('NOT')
            rn_ = self.expect_id().up
            reg = d['regs'].get(rn_)
            if reg is None:
                raise RBError("unknown register '%s'" % rn_)
            if self.accept_op('.'):
                lo, w = self.const_expr(), 1
            else:
                self.expect_op('[')
                hi = self.const_expr()
                lo = hi
                if self.accept_op(':'):
                    lo = self.const_expr()
                self.expect_op(']')
                w = hi - lo + 1
            o = self.make_sub(reg, lo, w)
            o.name = '%s.%s' % (d['name'], name)
            o.inv = inv
            d['bits'][name] = o
        else:
            raise RBError("REG, BIT, FIELD or END DEVICE expected, found '%s'" % kw)
        self.expect_end()

    # assignment and commands

    def st_let(self):
        dn = self.ref()
        if dn.k != 'obj':
            raise RBError('cannot assign to a constant')
        self.expect_op('=')
        self.assign(dn.a, self.expr())

    def st_atomic(self):
        em = self.em
        rest = self.t[self.i:]
        self.i = len(self.t)
        em.ins('csrrci', T1, CSRS['mstatus'], 8)
        em.ins('andi', T1, T1, 8)
        self.push(T1)
        self.sub_stmt(rest)
        self.pop(T1)
        em.ins('csrrs', ZERO, CSRS['mstatus'], T1)

    def shift_cmd(self, op):
        em = self.em
        dn = self.ref()
        o = dn.a if dn.k == 'obj' else None
        if o is None or o.kind not in ('reg', 'ram') or o.isbit:
            raise RBError('%s needs a register or RAM variable' % op)
        count = Node('const', 1)
        if self.accept_op(','):
            count = self.expr()
        W = o.width
        if count.k == 'const':
            c = count.a
            if op in ('ROL', 'ROR'):
                c %= W
            elif not 0 <= c < W:
                raise RBError('shift count %d out of range for %d bits' % (c, W))
            if c == 0:
                return
            cnt = None
        else:
            if count.k != 'obj' or count.a.kind != 'reg' or count.a.width != 32:
                raise RBError('shift count must be a constant or a 32-bit register')
            if W != 32:
                raise RBError('variable shift counts need a 32-bit destination')
            cnt = count.a.reg
        if o.kind == 'reg':
            r = o.reg
            self.check_write_reg(r)
            if op == 'SHR' or op in ('ROL', 'ROR'):
                if W < 32 and o.signed:
                    self.normalize_reg(r, Obj('reg', width=W, signed=False))
        else:
            r = T1
            base, ofs = self.addr_base(o.addr)
            em.ins(self.ld(W, False), T1, ofs, base)
        tmp, tmp2 = (T1, T2) if r != T1 else (T2, T0)
        if cnt is None:
            if op == 'SHL':
                em.ins('slli', r, r, c)
            elif op == 'SHR':
                em.ins('srli', r, r, c)
            elif W == 32 and 'ZBB' in self.ext:
                em.ins('rori', r, r, (32 - c) % 32 if op == 'ROL' else c)
            elif op == 'ROL':
                em.ins('slli', tmp, r, c)
                em.ins('srli', r, r, W - c)
                em.ins('or', r, r, tmp)
            else:
                em.ins('srli', tmp, r, c)
                em.ins('slli', r, r, W - c)
                em.ins('or', r, r, tmp)
        else:
            if op == 'SHL':
                em.ins('sll', r, r, cnt)
            elif op == 'SHR':
                em.ins('srl', r, r, cnt)
            elif 'ZBB' in self.ext:
                em.ins('rol' if op == 'ROL' else 'ror', r, r, cnt)
            else:
                em.ins('sll' if op == 'ROL' else 'srl', tmp, r, cnt)
                em.ins('sub', tmp2, ZERO, cnt)
                em.ins('srl' if op == 'ROL' else 'sll', r, r, tmp2)
                em.ins('or', r, r, tmp)
        if o.kind == 'reg':
            if W < 32:
                self.normalize_reg(r, o)
        else:
            base, ofs = self.addr_base(o.addr)
            em.ins(self.st(W), T1, ofs, base)

    def st_shl(self):
        self.shift_cmd('SHL')

    def st_shr(self):
        self.shift_cmd('SHR')

    def st_rol(self):
        self.shift_cmd('ROL')

    def st_ror(self):
        self.shift_cmd('ROR')

    # control flow

    def st_goto(self):
        self.em.j('L_' + self.expect_id().up)

    def st_if(self):
        em = self.em
        cond = self.expr()
        self.expect_kw('THEN')
        if self.at_end():
            nxt = self.newlabel()
            self.gen_branch(cond, nxt, False)
            self.blocks.append({'type': 'IF', 'next': nxt, 'end': self.newlabel(), 'else': False})
            return
        rest = self.t[self.i:]
        self.i = len(self.t)
        k = next((j for j, tk in enumerate(rest) if tk.kind == 'id' and tk.up == 'ELSE'), None)
        then_t, else_t = (rest, None) if k is None else (rest[:k], rest[k + 1:])
        nxt = self.newlabel()
        self.gen_branch(cond, nxt, False)
        self.sub_stmt(then_t)
        if else_t is not None:
            end = self.newlabel()
            em.j(end)
            em.label(nxt)
            self.sub_stmt(else_t)
            em.label(end)
        else:
            em.label(nxt)

    def top_block(self, *types):
        if not self.blocks or self.blocks[-1]['type'] not in types:
            raise RBError('no matching %s' % '/'.join(types))
        return self.blocks[-1]

    def st_elseif(self):
        b = self.top_block('IF')
        if b['else']:
            raise RBError('ELSEIF after ELSE')
        self.em.j(b['end'])
        self.em.label(b['next'])
        cond = self.expr()
        self.expect_kw('THEN')
        b['next'] = self.newlabel()
        self.gen_branch(cond, b['next'], False)

    def st_else(self):
        b = self.top_block('IF')
        if b['else']:
            raise RBError('ELSE twice')
        self.em.j(b['end'])
        self.em.label(b['next'])
        b['next'] = None
        b['else'] = True

    def st_end(self):
        if self.at_end():
            self.em.j('__end')
            return
        kw = self.expect_id().up
        if kw == 'IF':
            b = self.top_block('IF')
            self.blocks.pop()
            if b['next']:
                self.em.label(b['next'])
            self.em.label(b['end'])
        elif kw in ('SUB', 'FUNCTION'):
            self.end_sub(kw)
        elif kw == 'SELECT':
            b = self.top_block('SELECT')
            self.blocks.pop()
            if b['next']:
                self.em.label(b['next'])
            self.em.label(b['end'])
        elif kw == 'IRQ':
            self.end_sub('IRQ')
        else:
            raise RBError("END %s is not a block end" % kw)

    def st_while(self):
        top, ex = self.newlabel(), self.newlabel()
        self.em.label(top)
        self.gen_branch(self.expr(), ex, False)
        self.blocks.append({'type': 'WHILE', 'top': top, 'exit': ex})

    def st_wend(self):
        b = self.top_block('WHILE')
        self.blocks.pop()
        self.em.j(b['top'])
        self.em.label(b['exit'])

    def st_do(self):
        top, ex = self.newlabel(), self.newlabel()
        self.em.label(top)
        self.blocks.append({'type': 'DO', 'top': top, 'exit': ex})

    def st_loop(self):
        b = self.top_block('DO')
        self.blocks.pop()
        if self.accept_kw('UNTIL'):
            self.gen_branch(self.expr(), b['top'], False)
        elif self.accept_kw('WHILE'):
            self.gen_branch(self.expr(), b['top'], True)
        else:
            self.em.j(b['top'])
        self.em.label(b['exit'])

    def st_for(self):
        vn = self.ref()
        o = vn.a if vn.k == 'obj' else None
        if o is None or o.kind not in ('reg', 'ram') or o.isbit:
            raise RBError('FOR needs a register or RAM variable as counter')
        self.expect_op('=')
        start = self.expr()
        self.expect_kw('TO')
        end = self.expr()
        step = 1
        if self.accept_kw('STEP'):
            step = self.const_expr()
            if step == 0:
                raise RBError('STEP 0')
        self.assign(o, start)
        top, ex = self.newlabel(), self.newlabel()
        self.em.label(top)
        var = Node('obj', o, signed=True, width=o.width)
        cond = Node('bin', ('>' if step > 0 else '<', var, end), signed=True, isbool=True)
        self.gen_branch(cond, ex, True)
        self.blocks.append({'type': 'FOR', 'obj': o, 'step': step, 'top': top, 'exit': ex})

    def st_next(self):
        b = self.top_block('FOR')
        if not self.at_end():
            self.ref()
        self.blocks.pop()
        o = b['obj']
        self.assign(o, Node('bin', ('+', self.obj_node(o), Node('const', b['step']))))
        self.em.j(b['top'])
        self.em.label(b['exit'])

    def st_exit(self):
        kw = self.expect_id().up
        if kw not in ('FOR', 'DO', 'WHILE', 'SUB', 'FUNCTION'):
            raise RBError('EXIT FOR, DO, WHILE, SUB or FUNCTION expected')
        for b in reversed(self.blocks):
            if b['type'] == kw:
                self.em.j(b['exit'])
                return
        raise RBError('EXIT %s outside %s' % (kw, kw))

    def st_select(self):
        self.expect_kw('CASE')
        n = self.expr()
        slot = Obj('ram', '__select', addr=self.alloc_ram(4, 4), width=32)
        self.assign_mem(slot, n)
        self.blocks.append({'type': 'SELECT', 'slot': slot, 'end': self.newlabel(),
                            'next': None, 'cases': 0, 'else': False})

    def st_case(self):
        em = self.em
        b = self.top_block('SELECT')
        if b['else']:
            raise RBError('CASE after CASE ELSE')
        if b['cases']:
            em.j(b['end'])
        if b['next']:
            em.label(b['next'])
        b['cases'] += 1
        if self.accept_kw('ELSE'):
            b['next'] = None
            b['else'] = True
            return
        vals = [self.const_expr()]
        while self.accept_op(','):
            vals.append(self.const_expr())
        base, ofs = self.addr_base(b['slot'].addr)
        em.ins('lw', T1, ofs, base)
        body = self.newlabel()
        b['next'] = self.newlabel()
        for j, v in enumerate(vals):
            if v == 0:
                r = ZERO
            else:
                em.li(T2, v)
                r = T2
            if j < len(vals) - 1:
                em.ins('beq', T1, r, body)
            else:
                em.ins('bne', T1, r, b['next'])
        em.label(body)

    def st_sub(self):
        self.begin_sub('SUB')

    def st_function(self):
        self.begin_sub('FUNCTION')

    def begin_sub(self, kind):
        em = self.em
        if self.scope is not None or self.cur_func is not None:
            raise RBError('%s inside another SUB, FUNCTION or IRQ' % kind)
        u = self.expect_id().up
        f = self.funcs[u]
        self.i = len(self.t)
        skip = self.newlabel()
        em.j(skip)
        em.head('%s %s' % (kind, u))
        start = em.mark()
        em.label(f['label'])
        self.push(RA)
        self.scope = {}
        for j, (pn, typ) in enumerate(f['params']):
            w, sg, isbit = TYPES[typ]
            self.scope[pn] = Obj('reg', pn, reg=PARAM_REGS[j], width=32 if isbit else w, signed=sg, isbit=isbit)
        self.cur_func = f
        self.blocks.append({'type': kind, 'exit': self.newlabel(), 'skip': skip, 'name': u, 'start': start})

    def end_sub(self, kind):
        em = self.em
        b = self.top_block(kind)
        self.blocks.pop()
        em.label(b['exit'])
        self.pop(RA)
        em.ins('jalr', ZERO, 0, RA)
        self.sizes.append(('%s %s' % (kind, b['name']), b['start'], em.mark()))
        em.head('program')
        em.label(b['skip'])
        self.scope = None
        self.cur_func = None

    def st_return(self):
        b = next((b for b in reversed(self.blocks) if b['type'] in ('SUB', 'FUNCTION', 'IRQ')), None)
        if b is None:
            raise RBError('RETURN outside SUB, FUNCTION or IRQ')
        if b['type'] == 'FUNCTION':
            n = self.expr()
            self.gen(n, A0, True)
            f = self.cur_func
            if f['width'] < 32 and not self.source_fits(n, Obj('reg', width=f['width'], signed=f['signed'])):
                self.normalize_reg(A0, Obj('reg', width=f['width'], signed=f['signed']))
        self.em.j(b['exit'])

    def st_call(self):
        u = self.expect_id().up
        if u not in self.funcs:
            raise RBError("unknown SUB '%s'" % u)
        self.gen_call(u, self.arglist(self.isop('(')), False)

    def st_on(self):
        em = self.em
        self.expect_kw('IRQ')
        tok = self.peek()
        if tok.kind == 'id' and tok.up in IRQ_NAMES:
            self.i += 1
            n = IRQ_NAMES[tok.up]
        else:
            n = self.const_expr()
        if not 0 <= n < 2048:
            raise RBError('IRQ number %d out of range' % n)
        if n in self.irq:
            raise RBError('IRQ %d handled twice' % n)
        if self.scope is not None or self.cur_func is not None:
            raise RBError('ON IRQ inside SUB or FUNCTION')
        lab = '__irq%d' % n
        self.irq[n] = lab
        skip = self.newlabel()
        em.j(skip)
        em.head('IRQ %d' % n)
        start = em.mark()
        em.label(lab)
        self.push(RA)
        self.blocks.append({'type': 'IRQ', 'exit': self.newlabel(), 'skip': skip, 'name': str(n), 'start': start})

    def st_wait(self):
        self.expect_kw('UNTIL')
        top = self.newlabel()
        self.em.label(top)
        self.gen_branch(self.expr(), top, False)

    def delay(self, per_second):
        """Busy loop of 2 instructions per pass, sized from CLOCK and CPI."""
        if self.clock is None:
            raise RBError('CLOCK must be declared for DELAY_US / DELAY_MS')
        t = self.const_expr()
        n = (self.clock * t) // (per_second * 2 * self.cpi)
        n = max(1, n)
        if n > 0xFFFFFFFF:
            raise RBError('delay too long')
        loop = self.newlabel()
        self.em.li(T1, n)
        self.em.label(loop)
        self.em.ins('addi', T1, T1, -1)
        self.em.ins('bne', T1, ZERO, loop)

    def st_delay_us(self):
        self.delay(1000000)

    def st_delay_ms(self):
        self.delay(1000)

    def st_asm(self):
        self.expect_end()
        self.in_asm = True

    def check_print_device(self, dn):
        dev = self.devices.get(dn)
        if dev is None:
            raise RBError("'%s' is not a device" % dn)
        if 'DATA' not in dev['regs'] or 'TXREADY' not in dev['bits']:
            raise RBError('device %s needs REG DATA and BIT TXREADY for PRINT' % dn)

    def device_arg(self, stmt):
        """Device of a PRINT / INPUT statement: an explicit device name followed by ',' or the
        end of the line, else the console. Returns (device, first) where first means the first
        item follows without a separator."""
        tok, after = self.peek(), self.peek(1)
        if tok.kind == 'id' and tok.up in self.devices and \
                (after.kind == 'eol' or (after.kind == 'op' and after.val in (',', ';'))):
            self.i += 1
            return tok.up, False
        if self.console is None:
            raise RBError('%s without a device needs a CONSOLE declaration (CONSOLE UART0), '
                          'or name the device: %s UART0, ...' % (stmt, stmt))
        return self.console, True

    def check_input_device(self, dn):
        dev = self.devices.get(dn)
        if dev is None:
            raise RBError("'%s' is not a device" % dn)
        if 'RXREADY' not in dev['bits'] or not ('RXDATA' in dev['regs'] or 'DATA' in dev['regs']):
            raise RBError('device %s needs BIT RXREADY and REG RXDATA (or DATA) for INPUT' % dn)
        rdy = dev['bits']['RXREADY']
        if rdy.w != 1:
            raise RBError('device %s: RXREADY must be a single bit' % dn)

    def can_print(self, dn):
        dev = self.devices[dn]
        return 'DATA' in dev['regs'] and 'TXREADY' in dev['bits']

    def st_input(self):
        """INPUT [dev,] item, ...

        Items: "prompt" (sent first), CHR target (one character, no echo), DEC target
        (optional '-' and decimal digits), HEX target (hex digits). DEC and HEX echo what they
        accept when the device can transmit; the first other character ends the number.
        """
        em = self.em
        dn, first = self.device_arg('INPUT')
        self.check_input_device(dn)
        self.need('__getc_' + dn)
        items = 0
        while True:
            if first:
                first = False
            elif not (self.accept_op(',') or self.accept_op(';')):
                break
            if self.at_end():
                break
            items += 1
            tok = self.peek()
            if tok.kind == 'str':
                self.i += 1
                if not self.can_print(dn):
                    raise RBError('INPUT prompt needs a device that can transmit (DATA and TXREADY)')
                bad = [ch for ch in tok.val if ord(ch) > 255]
                if bad:
                    raise RBError("INPUT: character '%s' is not in the 8-bit character set" % bad[0])
                self.need('__putc_' + dn)
                self.print_chars(dn, [ord(ch) for ch in tok.val])
                continue
            kind = next((k for k in ('CHR', 'DEC', 'HEX') if self.accept_kw(k)), None)
            if kind is None:
                raise RBError('INPUT items: "prompt", CHR var, DEC var or HEX var')
            dest = self.ref()
            if dest.k != 'obj':
                raise RBError('INPUT needs a variable, register or device register to store into')
            routine = {'CHR': '__getc_', 'DEC': '__getdec_', 'HEX': '__gethex_'}[kind] + dn
            self.need(routine)
            em.ins('jal', RA, routine)
            value = Node('obj', Obj('reg', 'input', reg=T1, width=8 if kind == 'CHR' else 32),
                         signed=kind == 'DEC', width=8 if kind == 'CHR' else 32)
            self.assign(dest.a, value)
        self.expect_end()
        if not items:
            raise RBError('INPUT needs at least one item')

    def st_line(self):
        """LINE INPUT [dev,] ["prompt",] buffer [, length]

        Reads characters into a BYTE array until Enter, with echo and backspace. The text is
        zero-terminated, so a buffer of N bytes holds up to N-1 characters. The optional
        length target receives the number of characters read.
        """
        em = self.em
        self.expect_kw('INPUT')
        dn, first = self.device_arg('LINE INPUT')
        self.check_input_device(dn)
        if not first:
            self.expect_op(',')
        while self.peek().kind == 'str':
            tok = self.next()
            if not self.can_print(dn):
                raise RBError('LINE INPUT prompt needs a device that can transmit (DATA and TXREADY)')
            bad = [ch for ch in tok.val if ord(ch) > 255]
            if bad:
                raise RBError("LINE INPUT: character '%s' is not in the 8-bit character set" % bad[0])
            self.need('__putc_' + dn)
            self.print_chars(dn, [ord(ch) for ch in tok.val])
            self.expect_op(',')
        tok = self.expect_id()
        buf = self.lookup_var(tok.up)
        if buf is None or buf.kind != 'array' or buf.width != 8 or buf.isbit:
            raise RBError("LINE INPUT needs a BYTE array as buffer, '%s' is not one" % tok.val)
        if buf.count < 2:
            raise RBError('LINE INPUT buffer needs room for at least one character and the 0')
        dest = None
        if self.accept_op(','):
            dest = self.ref()
            if dest.k != 'obj':
                raise RBError('LINE INPUT length needs a variable, register or device register')
        self.expect_end()
        self.need('__getline_' + dn)
        em.li(T1, buf.addr)
        em.li(T2, buf.count - 1)
        em.ins('jal', RA, '__getline_' + dn)
        if dest is not None:
            self.assign(dest.a, Node('obj', Obj('reg', 'length', reg=T1), width=32))

    def st_console(self):
        """CONSOLE dev: PRINT statements after this line send to dev unless they name a device."""
        dn = self.expect_id().up
        self.check_print_device(dn)
        self.console = dn

    def st_print(self):
        """PRINT [dev,] item, ...

        Without a device, PRINT uses the device of the last CONSOLE declaration.
        Items: "text", character constants (0-255, also CONST names), CHR expr, HEX expr, DEC expr,
        and BYTE array names (zero-terminated text, at most the array size).
        No line end is added: write 13, 10 for CR LF. Adjacent text and constants are
        merged at compile time into one string.
        """
        em = self.em
        dn, first = self.device_arg('PRINT')
        self.check_print_device(dn)
        self.need('__putc_' + dn)
        items = 0
        run = []                      # characters waiting to be sent as one string
        while True:
            if first:
                first = False
            elif not (self.accept_op(',') or self.accept_op(';')):
                break
            if self.at_end():
                break
            items += 1
            tok = self.peek()
            if tok.kind == 'str':
                self.i += 1
                bad = [ch for ch in tok.val if ord(ch) > 255]
                if bad:
                    raise RBError("PRINT: character '%s' is not in the 8-bit character set" % bad[0])
                run += [ord(ch) for ch in tok.val]
            elif self.accept_kw('HEX'):
                self.print_chars(dn, run)
                run = []
                n = self.expr()
                self.gen(n, T1, True)
                em.li(T2, min(8, max(1, (n.width + 3) // 4)))
                self.need('__puthex_' + dn)
                em.ins('jal', RA, '__puthex_' + dn)
            elif self.accept_kw('CHR'):
                n = self.expr()
                if n.k == 'const':
                    if not 0 <= n.a <= 255:
                        raise RBError('PRINT: character code %d out of range 0-255' % n.a)
                    run.append(n.a)
                    continue
                self.print_chars(dn, run)
                run = []
                self.gen(n, T1, True)
                data = self.devices[dn]['regs']['DATA']
                if data.width > 8 and not (n.k == 'obj' and n.width <= 8 and not n.signed):
                    em.ins('andi', T1, T1, 255)       # only the low byte is a character
                em.ins('jal', RA, '__putc_' + dn)
            elif self.accept_kw('DEC'):
                self.print_chars(dn, run)
                run = []
                n = self.expr()
                self.gen(n, T1, True)
                em.li(T2, 1 if n.signed else 0)
                self.need('__putdec_' + dn)
                em.ins('jal', RA, '__putdec_' + dn)
            elif tok.kind == 'id' and not self.isop_at(1, '(') and \
                    getattr(self.lookup_var(tok.up), 'kind', None) == 'array':
                self.i += 1
                buf = self.lookup_var(tok.up)
                if buf.width != 8 or buf.isbit:
                    raise RBError("PRINT of an array needs a BYTE array (zero-terminated text), "
                                  "'%s' is not one" % tok.val)
                self.print_chars(dn, run)
                run = []
                em.li(T1, buf.addr)
                em.li(T2, buf.count)
                self.need('__putbuf_' + dn)
                em.ins('jal', RA, '__putbuf_' + dn)
            else:
                n = self.expr()
                if n.k != 'const':
                    raise RBError('PRINT: only constants are sent as characters; '
                                  'use CHR for a character value, DEC or HEX to print a number')
                if not 0 <= n.a <= 255:
                    raise RBError('PRINT: character code %d out of range 0-255' % n.a)
                run.append(n.a)
        self.expect_end()
        if not items:
            raise RBError('PRINT needs at least one item')
        self.print_chars(dn, run)

    def print_chars(self, dn, run):
        """Send constant characters: one string for runs of 3 or more, single putc otherwise.
        A zero byte cannot be part of a string, so it splits the run."""
        em = self.em
        segs, cur = [], []
        for ch in run + [None]:
            if ch is None or ch == 0:
                if cur:
                    segs.append(cur)
                if ch == 0:
                    segs.append([0])
                cur = []
            else:
                cur.append(ch)
        for seg in segs:
            if len(seg) <= 2 or seg == [0]:
                for ch in seg:
                    em.li(T1, ch)
                    em.ins('jal', RA, '__putc_' + dn)
            else:
                text = ''.join(chr(ch) for ch in seg)
                lab = self.strings.setdefault(text, '__str%d' % len(self.strings))
                em.la(T1, lab)
                self.need('__puts_' + dn)
                em.ins('jal', RA, '__puts_' + dn)

    # ---- end of compilation

    def runtime_text(self, name):
        if name in ('__mul', '__divu', '__div'):
            return {'__mul': rt_mul, '__divu': rt_divu, '__div': rt_div}[name]()
        kind, dn = name[2:].split('_', 1)
        dev = self.devices[dn]
        if kind == 'getc':
            rdy = dev['bits']['RXREADY']
            data = dev['regs'].get('RXDATA') or dev['regs']['DATA']
            st_reg = rdy.base
            ofs = data.addr - st_reg.addr
            if not fits12(ofs):
                raise RBError('device %s: RXDATA and the RXREADY register too far apart' % dn)
            return rt_getc(dn, st_reg.addr, self.ld(st_reg.width, False), rdy.lo, ofs,
                           self.ld(data.width, False), data.width > 8, rdy.inv)
        if kind in ('getdec', 'gethex'):
            return (rt_getdec if kind == 'getdec' else rt_gethex)(dn, self.can_print(dn))
        if kind == 'getline':
            return rt_getline(dn, self.can_print(dn))
        if kind == 'putc':
            data, rdy = dev['regs']['DATA'], dev['bits']['TXREADY']
            st_reg = rdy.base
            ofs = data.addr - st_reg.addr
            if not fits12(ofs):
                raise RBError('device %s: DATA and STATUS too far apart' % dn)
            if rdy.w != 1:
                raise RBError('device %s: TXREADY must be a single bit' % dn)
            return rt_putc(dn, st_reg.addr, self.ld(st_reg.width, False), rdy.lo, ofs,
                           self.st(data.width), rdy.inv)
        return {'puts': rt_puts, 'putbuf': rt_putbuf, 'puthex': rt_puthex, 'putdec': rt_putdec}[kind](dn)

    def finish(self):
        em = self.em
        if self.in_device is not None:
            raise RBError('DEVICE without END DEVICE')
        if self.in_asm:
            raise RBError('ASM without END ASM')
        if self.blocks:
            raise RBError('%s block not closed' % self.blocks[-1]['type'])
        self.ensure_start()
        em.line = 0                         # end loop, runtime and data have no source line
        em.head('end of program')
        em.label('__end')
        em.j('__end')
        prog_end = em.mark()
        self.sizes.insert(0, ('program', 0, prog_end))
        done = set()
        k = 0
        while k < len(self.rt_needed):
            name = self.rt_needed[k]
            k += 1
            if name in done or name == '__pow10':
                continue
            done.add(name)
            text, deps = self.runtime_text(name)
            for d in deps:
                self.need(d)
            em.head('runtime ' + name)
            start = em.mark()
            asm_text(em, rt_regs(text))
            self.sizes.append(('runtime ' + name, start, em.mark()))
        if self.irq:
            self.emit_trap()
        if self.strings or '__pow10' in self.rt_needed:
            em.head('data')
            em.align4()
            start = em.mark()
            for text, lab in self.strings.items():
                em.label(lab)
                raw = text.encode('latin-1') + b'\0'
                raw += b'\0' * (-len(raw) % 4)
                for j in range(0, len(raw), 4):
                    w = int.from_bytes(raw[j:j + 4], 'little')
                    em.data(w, '.asciz "%s"' % text.encode('unicode_escape').decode('ascii')
                            if j == 0 else '.word   0x%08X' % w)
            if '__pow10' in self.rt_needed:
                em.label('__pow10')
                for p in range(9, -1, -1):
                    em.data(10 ** p, '.word   %d' % 10 ** p)
            self.sizes.append(('data', start, em.mark()))
        if self.uses_ra and (self.rt_needed or self.funcs or self.irq):
            self.warn('RA (R1) is used as a variable, but SUB/FUNCTION calls and runtime calls '
                      '(PRINT, software * / MOD) overwrite it')
        ram_end = (self.ram_next + 3) & ~3
        self.ram_used = ram_end - self.ram_base
        if self.ram_used + self.stack > self.ram_size:
            raise RBError('RAM overflow: %d bytes of variables + %d bytes of stack > %d bytes of RAM'
                          % (self.ram_used, self.stack, self.ram_size))
        em.finalize({'__ram_end': ram_end})
        self.code_used = em.nbytes
        if self.code_used > self.code_size:
            raise RBError('code overflow: %d bytes > CODE SIZE %d bytes' % (self.code_used, self.code_size))

    def emit_trap(self):
        em = self.em
        regs = [RA, 5, 6, 7, T1, T2] + list(range(A0, A0 + (6 if 'E' in self.ext else 8)))
        if 'E' not in self.ext:
            regs += [28, 29, 30, 31]
        frame = 4 * len(regs)
        lines = ['__trap:', 'addi sp, sp, -%d' % frame]
        lines += ['sw %s, %d(sp)' % (ABI[r], 4 * j) for j, r in enumerate(regs)]
        lines += ['csrr t1, mcause', 'bgez t1, __trap_exc', 'slli t1, t1, 1', 'srli t1, t1, 1']
        for n, lab in sorted(self.irq.items()):
            lines += ['li t2, %d' % n, 'bne t1, t2, __trap_n%d' % n, 'jal ra, %s' % lab,
                      'j __trap_ret', '__trap_n%d:' % n]
        lines += ['j __trap_ret', '__trap_exc:', 'csrr t1, mepc', 'addi t1, t1, 4',
                  'csrw mepc, t1', '__trap_ret:']
        lines += ['lw %s, %d(sp)' % (ABI[r], 4 * j) for j, r in enumerate(regs)]
        lines += ['addi sp, sp, %d' % frame, 'mret']
        em.head('trap dispatcher')
        em.align4()                 # mtvec needs a 4-byte aligned address
        start = em.mark()
        asm_text(em, '\n'.join(lines))
        self.sizes.append(('trap dispatcher', start, em.mark()))

    # ---- output

    def listing(self):
        em = self.em
        out = ['; RiscBasic %s listing: %s' % (VERSION, self.fname),
               '; TARGET %s   CODE 0x%08X size %d   RAM 0x%08X size %d   GP = 0x%08X'
               % (self.target, em.base, self.code_size, self.ram_base or 0, self.ram_size, self.gpval)]
        if 'C' in self.ext:
            out.append('; compressed (C) instructions are marked c.; forward branches and calls stay 32-bit')
        out.append(';')
        for e in em.lst:
            if e[0] == 'src':
                out.append('; ' + e[1])
            elif e[0] == 'head':
                out.append('; ---- %s ----' % e[1])
            elif e[0] == 'label':
                out.append('%18s%s:' % ('', e[1]))
            else:
                idx = e[1]
                size, a = em.isz[idx], em.addr[idx]
                if size == 0:
                    continue
                if size == 8:
                    w = em.code[idx]
                    out.append('%08X  %08X   %s' % (a, w & 0xFFFFFFFF, em.text[idx][0]))
                    out.append('%08X  %08X   %s' % (a + 4, w >> 32, em.text[idx][1]))
                    continue
                code = ('%08X' if size == 4 else '    %04X') % em.code[idx]
                out.append('%08X  %s   %s' % (a, code, em.text[idx]))
        out += [';', '; ---- summary ----']
        for name, a, b in self.sizes:
            a, b = em.addr_at(a), em.addr_at(b)
            out.append(';   %-28s %6d bytes   0x%08X - 0x%08X' % (name, b - a, a, b))
        out.append(';   code used %d of %d bytes (%d%%)'
                   % (self.code_used, self.code_size, 100 * self.code_used // max(1, self.code_size)))
        nfar = sum(1 for s in em.isz if s == 8)
        if nfar:
            out.append(';   far branches (inverted branch + jal): %d' % nfar)
        if 'C' in self.ext:
            n16 = sum(1 for s, k in zip(em.isz, em.kind) if s == 2 and k != 'align')
            out.append(';   compressed instructions: %d of %d, %d bytes saved'
                       % (n16, sum(1 for k in em.kind if k != 'align'), em.saved()))
        out.append(';   RAM variables %d bytes, stack %d bytes, RAM %d bytes'
                   % (self.ram_used, self.stack, self.ram_size))
        for w in self.warnings:
            out.append(';   ' + w)
        return '\n'.join(out) + '\n'

    def binary(self):
        return self.em.binary()

    def elf_symbols(self):
        """Symbols for the ELF file: code labels, routines with sizes, RAM variables."""
        em = self.em
        sizes = {}
        for name, a, b in self.sizes:
            kind, _, rest = name.partition(' ')
            label = {'program': '__start', 'SUB': 'sub_' + rest, 'FUNCTION': 'sub_' + rest,
                     'runtime': rest, 'IRQ': '__irq' + rest, 'trap': '__trap', 'data': None}.get(kind)
            if label:
                sizes[label] = em.addr_at(b) - em.addr_at(a)
        syms = [('$x', em.base, 0, 'notype', 'text')]
        data = next((a for name, a, b in self.sizes if name == 'data'), None)
        if data is not None:
            syms.append(('$d', em.addr_at(data), 0, 'notype', 'text'))
        for label, i in self.em.labels.items():
            addr = em.addr_at(i)
            if label.startswith('L_'):
                syms.append((label[2:], addr, 0, 'notype', 'text'))
            elif label.startswith('sub_'):
                syms.append((label[4:], addr, sizes.get(label, 0), 'func', 'text'))
            elif label in sizes:
                syms.append((label, addr, sizes[label], 'func', 'text'))
            elif label == 'main':
                syms.append(('main', addr, em.addr_at(self.em.labels['__end']) - addr, 'func', 'text'))
            elif label.startswith('__str') or label == '__pow10':
                syms.append((label, addr, 0, 'object', 'text'))
            else:
                syms.append((label, addr, 0, 'notype', 'text'))
        for name, addr, size in self.ram_syms:
            syms.append((name, addr, size, 'object', 'bss'))
        return syms

    def line_rows(self):
        """(address, source line) wherever the source line changes; 0 = no source line."""
        em = self.em
        rows, prev = [], None
        for i, (k, size) in enumerate(zip(em.kind, em.isz)):
            if size == 0:
                continue
            line = 0 if k == 'data' else em.iline[i]
            if line != prev:
                rows.append((em.addr[i], line))
                prev = line
        return rows

    def elf(self, debug=True):
        bss = (self.ram_base, self.ram_used) if self.ram_base is not None and self.ram_used else None
        extra = dwarf_sections(self.fname, self.src_dir, self.em.base, self.em.end, self.line_rows(),
                               'RiscBasic %s' % VERSION) if debug else ()
        return elf32(self.binary(), self.em.base, self.ext, self.elf_symbols(), bss, self.em.base, extra)


class _BasicResolver(AsmResolver):
    """Lets inline ASM use RiscBasic variables, aliases and constants by name."""

    def __init__(self, c):
        self.c = c

    def _obj(self, u):
        o = self.c.lookup_var(u)
        if o is None and u in self.c.aliases and self.c.aliases[u].k == 'obj':
            o = self.c.aliases[u].a
        return o

    def reg(self, u):
        o = self._obj(u)
        return o.reg if o is not None and o.kind == 'reg' else None

    def imm(self, u):
        if u in self.c.consts:
            return self.c.consts[u]
        if u in self.c.aliases and self.c.aliases[u].k == 'const':
            return self.c.aliases[u].a
        return None

    def mem(self, u):
        o = self._obj(u)
        if o is None or o.kind != 'ram':
            return None
        ofs = o.addr - self.c.gpval
        if not fits12(ofs):
            raise RBError("variable '%s' is not reachable from GP in ASM" % u)
        return ofs, GP


# ---------------------------------------------------------------- ELF

def elf_arch(ext):
    """ISA string for the .riscv.attributes section, e.g. rv32i2p1_m2p0_c2p0_zicsr2p0."""
    a = 'rv32e2p0' if 'E' in ext else 'rv32i2p1'
    if 'M' in ext:
        a += '_m2p0'
    if 'C' in ext:
        a += '_c2p0'
    a += '_zicsr2p0'
    if 'ZBB' in ext:
        a += '_zbb1p0'
    return a


GLOBAL_SYMS = ('__start', 'main')


def uleb(v):
    out = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        out.append(b | (0x80 if v else 0))
        if not v:
            return bytes(out)


def sleb(v):
    out = bytearray()
    while True:
        b = v & 0x7F
        v >>= 7
        done = (v == 0 and not b & 0x40) or (v == -1 and b & 0x40)
        out.append(b | (0 if done else 0x80))
        if done:
            return bytes(out)


def dwarf_sections(name, comp_dir, low, high, rows, producer):
    """DWARF 3 debug sections for one compile unit: line table plus the unit that points to it.

    rows: (address, line) in address order; line 0 marks code without a source line
    (startup, runtime routines, data).
    """
    import struct
    abbrev = (uleb(1) + uleb(0x11) + b'\0' +                 # DW_TAG_compile_unit, no children
              uleb(0x25) + uleb(0x08) +                        # DW_AT_producer, string
              uleb(0x13) + uleb(0x05) +                        # DW_AT_language, data2
              uleb(0x03) + uleb(0x08) +                        # DW_AT_name, string
              uleb(0x1B) + uleb(0x08) +                        # DW_AT_comp_dir, string
              uleb(0x11) + uleb(0x01) +                        # DW_AT_low_pc, addr
              uleb(0x12) + uleb(0x01) +                        # DW_AT_high_pc, addr
              uleb(0x10) + uleb(0x06) +                        # DW_AT_stmt_list, data4
              b'\0\0' + b'\0')
    body = (struct.pack('<HIB', 3, 0, 4) + uleb(1) + producer.encode() + b'\0' +
            struct.pack('<H', 0x8001) +                        # DW_LANG_Mips_Assembler: source-level asm
            name.encode() + b'\0' + comp_dir.encode() + b'\0' +
            struct.pack('<III', low, high, 0))
    info = struct.pack('<I', len(body)) + body

    hdr = (bytes([1, 1, (-5) & 0xFF, 14, 13]) +              # min inst length, is_stmt, line_base, line_range, opcode_base
           bytes([0, 1, 1, 1, 1, 0, 0, 0, 1, 0, 0, 1]) +       # standard opcode lengths
           b'\0' +                                            # no include directories: file is in comp_dir
           name.encode() + b'\0' + uleb(0) + uleb(0) + uleb(0) + b'\0')
    prog = b'\0' + uleb(5) + b'\x02' + struct.pack('<I', rows[0][0] if rows else low)   # DW_LNE_set_address
    addr, line = (rows[0][0] if rows else low), 1
    for a, ln in rows:
        if a != addr:
            prog += b'\x02' + uleb(a - addr)                  # DW_LNS_advance_pc
            addr = a
        if ln != line:
            prog += b'\x03' + sleb(ln - line)                 # DW_LNS_advance_line
            line = ln
        prog += b'\x01'                                       # DW_LNS_copy
    prog += b'\x02' + uleb(high - addr) + b'\0' + uleb(1) + b'\x01'   # advance to end, DW_LNE_end_sequence
    rest = struct.pack('<HI', 3, len(hdr)) + hdr + prog
    line_sec = struct.pack('<I', len(rest)) + rest

    ar = struct.pack('<HIBB', 2, 0, 4, 0) + b'\0' * 4 + struct.pack('<IIII', low, high - low, 0, 0)
    aranges = struct.pack('<I', len(ar)) + ar
    return [('.debug_abbrev', abbrev), ('.debug_info', info), ('.debug_line', line_sec),
            ('.debug_aranges', aranges)]


def elf32(image, base, ext=(), symbols=(), bss=None, entry=None, extra=()):
    """Build an ELF32 little-endian RISC-V executable as bytes.

    image:   code and constant data, loaded at base
    symbols: (name, value, size, type, section) with type 'func', 'object' or 'notype'
             and section 'text', 'bss' or 'abs'; '$x' / '$d' are mapping symbols
    bss:     (address, size) of RAM variables, or None
    extra:   (name, bytes) of additional non-loaded sections, e.g. DWARF .debug_*
    """
    import struct
    ext = set(ext)
    extra = list(extra)
    secnames = (['', '.text'] + (['.bss'] if bss else []) + ['.riscv.attributes'] +
                [n for n, _ in extra] + ['.symtab', '.strtab', '.shstrtab'])
    idx = {n: i for i, n in enumerate(secnames)}
    shstr = b'\0'
    shname = {}
    for n in secnames[1:]:
        shname[n] = len(shstr)
        shstr += n.encode() + b'\0'

    arch = elf_arch(ext).encode() + b'\0'
    file_attr = b'\x05' + arch                          # Tag_RISCV_arch
    file_attr = b'\x01' + struct.pack('<I', 5 + len(file_attr)) + file_attr
    sub = b'riscv\0' + file_attr
    attrs = b'A' + struct.pack('<I', 4 + len(sub)) + sub

    strtab = b'\0'
    types = {'notype': 0, 'object': 1, 'func': 2}
    ordered = sorted(symbols, key=lambda s: s[0] in GLOBAL_SYMS)    # locals first, then globals
    syms = [struct.pack('<IIIBBH', 0, 0, 0, 0, 0, 0)]
    nlocal = 1
    for name, value, size, typ, sec in ordered:
        bind = 1 if name in GLOBAL_SYMS else 0
        nlocal += bind == 0
        shndx = 0xFFF1 if sec == 'abs' else idx['.bss' if sec == 'bss' else '.text']
        syms.append(struct.pack('<IIIBBH', len(strtab), value & 0xFFFFFFFF, size,
                                (bind << 4) | types[typ], 0, shndx))
        strtab += name.encode() + b'\0'
    symtab = b''.join(syms)

    def pad4(b):
        return b + b'\0' * (-len(b) % 4)

    text_off = 52 + 32
    off = text_off + len(image)
    attr_off = off
    off += len(attrs)
    extra_off = []
    for _, data in extra:
        extra_off.append(off)
        off += len(data)
    off += -off % 4
    sym_off = off
    off += len(symtab)
    str_off = off
    off += len(strtab)
    shs_off = off
    off += len(shstr)
    off += -off % 4
    sh_off = off

    flags = (0x1 if 'C' in ext else 0) | (0x8 if 'E' in ext else 0)   # EF_RISCV_RVC, EF_RISCV_RVE
    ehdr = (b'\x7fELF' + bytes([1, 1, 1, 0]) + bytes(8) +
            struct.pack('<HHIIIIIHHHHHH', 2, 243, 1, base if entry is None else entry, 52, sh_off,
                        flags, 52, 32, 1, 40, len(secnames), idx['.shstrtab']))
    phdr = struct.pack('<IIIIIIII', 1, text_off, base, base, len(image), len(image), 5, 4)   # PT_LOAD, R+X

    def sh(name, typ, fl, addr, offs, size, link=0, info=0, align=1, entsize=0):
        return struct.pack('<IIIIIIIIII', shname[name] if name else 0, typ, fl, addr, offs, size,
                           link, info, align, entsize)

    shdrs = [sh('', 0, 0, 0, 0, 0)]
    shdrs.append(sh('.text', 1, 0x6, base, text_off, len(image), align=2 if 'C' in ext else 4))
    if bss:
        shdrs.append(sh('.bss', 8, 0x3, bss[0], text_off + len(image), bss[1], align=4))
    shdrs.append(sh('.riscv.attributes', 0x70000003, 0, 0, attr_off, len(attrs)))
    for (name, data), offs in zip(extra, extra_off):
        shdrs.append(sh(name, 1, 0, 0, offs, len(data)))
    shdrs.append(sh('.symtab', 2, 0, 0, sym_off, len(symtab), idx['.strtab'], nlocal, 4, 16))
    shdrs.append(sh('.strtab', 3, 0, 0, str_off, len(strtab)))
    shdrs.append(sh('.shstrtab', 3, 0, 0, shs_off, len(shstr)))

    out = ehdr + phdr + image + attrs + b''.join(data for _, data in extra)
    out = pad4(out) + symtab + strtab + shstr
    out = pad4(out) + b''.join(shdrs)
    return out


def intel_hex(data, base):
    out = []

    def rec(t, a, d):
        b = bytes([len(d), (a >> 8) & 0xFF, a & 0xFF, t]) + d
        out.append(':%s%02X' % (b.hex().upper(), (-sum(b)) & 0xFF))

    ext = None
    for off in range(0, len(data), 16):
        addr = base + off
        if addr >> 16 != ext:
            ext = addr >> 16
            rec(0x04, 0, ext.to_bytes(2, 'big'))
        rec(0x00, addr & 0xFFFF, data[off:off + 16])
    out.append(':00000001FF')
    return '\n'.join(out) + '\n'


def compile_file(path, outbase=None, lst=True, elf=True, debug=True):
    with open(path, encoding='utf-8-sig') as f:    # accepts files with or without a BOM
        src = f.read()
    c = Compiler(os.path.basename(path))
    c.src_dir = os.path.dirname(os.path.abspath(path))
    c.compile(src)
    outbase = outbase or os.path.splitext(path)[0]
    data = c.binary()
    with open(outbase + '.bin', 'wb') as f:
        f.write(data)
    with open(outbase + '.hex', 'w') as f:
        f.write(intel_hex(data, c.em.base))
    words = data + b'\0' * (-len(data) % 4)
    with open(outbase + '.mem', 'w') as f:
        f.write(''.join('%08X\n' % int.from_bytes(words[j:j + 4], 'little') for j in range(0, len(words), 4)))
    if lst:
        with open(outbase + '.lst', 'w', encoding='utf-8') as f:
            f.write(c.listing())
    if elf:
        with open(outbase + '.elf', 'wb') as f:
            f.write(c.elf(debug))
    return c


def main(argv=None):
    ap = argparse.ArgumentParser(description='RiscBasic compiler %s' % VERSION)
    ap.add_argument('source', help='RiscBasic source file (.bas)')
    ap.add_argument('-o', '--output', help='output base name (default: source name)')
    ap.add_argument('--no-lst', action='store_true', help='do not write the .lst listing')
    ap.add_argument('--no-elf', action='store_true', help='do not write the .elf file')
    ap.add_argument('--no-debug', action='store_true', help='no DWARF line information in the .elf')
    a = ap.parse_args(argv)
    try:
        c = compile_file(a.source, a.output, not a.no_lst, not a.no_elf, not a.no_debug)
    except RBError as e:
        print('%s:%s: error: %s' % (a.source, e.line if e.line is not None else '?', e.msg), file=sys.stderr)
        return 1
    except OSError as e:
        print('error: %s' % e, file=sys.stderr)
        return 1
    for w in c.warnings:
        print(w, file=sys.stderr)
    print('%s: %d bytes code (of %d), %d bytes RAM variables'
          % (a.source, c.code_used, c.code_size, c.ram_used))
    return 0


if __name__ == '__main__':
    sys.exit(main())
