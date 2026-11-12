#!/usr/bin/env python3
"""
rbsim: minimal RV32I(M, C, Zbb rol/ror/rori) simulator for testing RiscBasic output.

The decoder is written from the RISC-V spec, independently of the compiler's
encoder, so encoding mistakes show up as wrong results.

Test devices:
  GPIO   0x40000000  OUT +0, IN +4, SET +8, CLR +12
  UART0  0x40001000  DATA +0 (write = output), STATUS +4 (bit 0 = TX ready, bit 1 = RX data
                     available), RXDATA +8 (read = next input character)
  UART1  0x40001100  DATA +0 (write = output, read = next input character),
                     STATUS +4 (bit 0 = TX busy, bit 1 = RX empty: both active-low ready)
  PLAIN  0x40002000  4 KB of plain read/write registers
  WO     0x40003000  write-only OUT +0, SET +4, CLR +8; every read returns 0x55AA55AA
"""

import sys

MASK = 0xFFFFFFFF


def sx(v, bits):
    v &= (1 << bits) - 1
    return v - (1 << bits) if v >> (bits - 1) else v


class Halt(Exception):
    pass

# ---- RVC expansion (decode side, from the spec tables)

def _I(imm, rs1, f3, rd, op):
    return ((imm & 0xFFF) << 20) | (rs1 << 15) | (f3 << 12) | (rd << 7) | op


def _S(imm, rs2, rs1, f3):
    imm &= 0xFFF
    return ((imm >> 5) << 25) | (rs2 << 20) | (rs1 << 15) | (f3 << 12) | ((imm & 31) << 7) | 0x23


def _R(f7, rs2, rs1, f3, rd):
    return (f7 << 25) | (rs2 << 20) | (rs1 << 15) | (f3 << 12) | (rd << 7) | 0x33


def _J(imm, rd):
    imm &= 0x1FFFFF
    return ((((imm >> 20) & 1) << 31) | (((imm >> 1) & 0x3FF) << 21) | (((imm >> 11) & 1) << 20) |
            (((imm >> 12) & 0xFF) << 12) | (rd << 7) | 0x6F)


def _B(imm, rs2, rs1, f3):
    imm &= 0x1FFF
    return ((((imm >> 12) & 1) << 31) | (((imm >> 5) & 0x3F) << 25) | (rs2 << 20) | (rs1 << 15) |
            (f3 << 12) | (((imm >> 1) & 15) << 8) | (((imm >> 11) & 1) << 7) | 0x63)


def bit(h, n):
    return (h >> n) & 1


def expand(h):
    """Expand a 16-bit RVC instruction to its 32-bit equivalent."""
    q = h & 3
    f3 = h >> 13
    rd = (h >> 7) & 31
    rs2 = (h >> 2) & 31
    rdp = 8 + ((h >> 2) & 7)
    rs1p = 8 + ((h >> 7) & 7)
    imm6 = sx((bit(h, 12) << 5) | ((h >> 2) & 31), 6)
    if q == 0:
        if f3 == 0:
            nz = (((h >> 11) & 3) << 4) | (((h >> 7) & 15) << 6) | (bit(h, 6) << 2) | (bit(h, 5) << 3)
            if nz == 0:
                raise RuntimeError('illegal compressed instruction 0x%04X' % h)
            return _I(nz, 2, 0, rdp, 0x13)
        uimm = (((h >> 10) & 7) << 3) | (bit(h, 6) << 2) | (bit(h, 5) << 6)
        if f3 == 2:
            return _I(uimm, rs1p, 2, rdp, 0x03)
        if f3 == 6:
            return _S(uimm, rdp, rs1p, 2)
    elif q == 1:
        jimm = sx((bit(h, 12) << 11) | (bit(h, 11) << 4) | (((h >> 9) & 3) << 8) | (bit(h, 8) << 10) |
                  (bit(h, 7) << 6) | (bit(h, 6) << 7) | (((h >> 3) & 7) << 1) | (bit(h, 2) << 5), 12)
        if f3 == 0:
            return _I(imm6, rd, 0, rd, 0x13)
        if f3 == 1:
            return _J(jimm, 1)
        if f3 == 2:
            return _I(imm6, 0, 0, rd, 0x13)
        if f3 == 3:
            if rd == 2:
                imm = sx((bit(h, 12) << 9) | (bit(h, 6) << 4) | (bit(h, 5) << 6) |
                         (((h >> 3) & 3) << 7) | (bit(h, 2) << 5), 10)
                return _I(imm, 2, 0, 2, 0x13)
            return ((imm6 & 0xFFFFF) << 12) | (rd << 7) | 0x37
        if f3 == 4:
            f2 = (h >> 10) & 3
            sh = (bit(h, 12) << 5) | ((h >> 2) & 31)
            if f2 == 0:
                return _I(sh, rs1p, 5, rs1p, 0x13)
            if f2 == 1:
                return _I(0x400 | sh, rs1p, 5, rs1p, 0x13)
            if f2 == 2:
                return _I(imm6, rs1p, 7, rs1p, 0x13)
            if not bit(h, 12):
                op = (h >> 5) & 3
                return _R(0x20 if op == 0 else 0, rdp, rs1p, (0, 4, 6, 7)[op], rs1p)
        if f3 == 5:
            return _J(jimm, 0)
        if f3 in (6, 7):
            bimm = sx((bit(h, 12) << 8) | (((h >> 10) & 3) << 3) | (((h >> 5) & 3) << 6) |
                      (((h >> 3) & 3) << 1) | (bit(h, 2) << 5), 9)
            return _B(bimm, 0, rs1p, f3 - 6)
    else:
        if f3 == 0:
            return _I((bit(h, 12) << 5) | rs2, rd, 1, rd, 0x13)
        if f3 == 2:
            uimm = (bit(h, 12) << 5) | (((h >> 4) & 7) << 2) | (((h >> 2) & 3) << 6)
            return _I(uimm, 2, 2, rd, 0x03)
        if f3 == 4:
            if not bit(h, 12):
                return _I(0, rd, 0, 0, 0x67) if rs2 == 0 else _R(0, rs2, 0, 0, rd)
            if rd == 0 and rs2 == 0:
                return 0x00100073
            return _I(0, rd, 0, 1, 0x67) if rs2 == 0 else _R(0, rs2, rd, 0, rd)
        if f3 == 6:
            uimm = (((h >> 9) & 15) << 2) | (((h >> 7) & 3) << 6)
            return _S(uimm, rs2, 2, 2)
    raise RuntimeError('unsupported compressed instruction 0x%04X' % h)


class Sim:
    def __init__(self, image, code_base=0, ram_base=0x8000, ram_size=0x800):
        self.code_base = code_base
        self.code = bytearray(image) + bytearray(max(0, 0x4000 - len(image)))
        self.ram_base = ram_base
        self.ram = bytearray(ram_size)
        self.plain = bytearray(0x1000)
        self.x = [0] * 32
        self.pc = code_base
        self.csr = {0x300: 0, 0x304: 0, 0x305: 0, 0x340: 0, 0x341: 0, 0x342: 0, 0x344: 0}
        self.out = []
        self.uart_out = {}          # UART data address -> characters, to check which UART was used
        self.uart_raw = []          # raw values written to UART data registers
        self.rx = {0: [], 1: []}    # input queues for UART0 and UART1
        self.wout = 0               # state of the write-only WO.OUT register
        self.wout_log = []          # every value WO.OUT took
        self.gpio_out = 0
        self.gpio_in = 0
        self.steps = 0
        self.compressed = 0
        self.irq_pending = set()
        # 4 KB each: around the sign boundary of LUI/ADDI and at the very top of the address space
        self.far = [(0x7FFFF000, bytearray(0x2000)), (0xFFFFF000, bytearray(0x1000))]

    # ---- memory

    def _region(self, addr, size):
        if self.code_base <= addr and addr + size <= self.code_base + len(self.code):
            return self.code, addr - self.code_base
        if self.ram_base <= addr and addr + size <= self.ram_base + len(self.ram):
            return self.ram, addr - self.ram_base
        if 0x40002000 <= addr and addr + size <= 0x40003000:
            return self.plain, addr - 0x40002000
        for base, buf in self.far:              # small memories spread over the 32-bit space
            if base <= addr and addr + size <= base + len(buf):
                return buf, addr - base
        return None, None

    def load(self, addr, size, signed):
        addr &= MASK
        if 0x40003000 <= addr < 0x40003010:
            v = 0x55AA55AA                  # write-only device: reads return garbage
        elif addr == 0x40000000:
            v = self.gpio_out
        elif addr == 0x40000004:
            v = self.gpio_in
        elif addr == 0x40001004:
            v = 1 | (2 if self.rx[0] else 0)
        elif addr == 0x40001008:
            v = ord(self.rx[0].pop(0)) if self.rx[0] else 0
        elif addr == 0x40001104:
            v = 0 if self.rx[1] else 2  # never busy; bit 1 = no input waiting
        elif addr == 0x40001100:
            v = ord(self.rx[1].pop(0)) if self.rx[1] else 0
        elif addr in (0x40001000, 0x40000008, 0x4000000C):
            v = 0
        else:
            buf, o = self._region(addr, size)
            if buf is None:
                raise RuntimeError('load from unmapped address 0x%08X at pc 0x%08X' % (addr, self.pc))
            v = int.from_bytes(buf[o:o + size], 'little')
        v &= (1 << (8 * size)) - 1
        return sx(v, 8 * size) & MASK if signed else v

    def store(self, addr, size, val):
        addr &= MASK
        val &= (1 << (8 * size)) - 1
        if addr == 0x40003000:
            self.wout = val
        elif addr == 0x40003004:
            self.wout |= val
        elif addr == 0x40003008:
            self.wout &= ~val & MASK
        if 0x40003000 <= addr < 0x4000300C:
            self.wout_log.append(self.wout)
        elif addr in (0x40001000, 0x40001100):
            self.out.append(chr(val & 0xFF))
            self.uart_out.setdefault(addr, []).append(chr(val & 0xFF))
            self.uart_raw.append(val)
        elif addr == 0x40000000:
            self.gpio_out = val
        elif addr == 0x40000008:
            self.gpio_out |= val
        elif addr == 0x4000000C:
            self.gpio_out &= ~val & MASK
        elif addr in (0x40000004, 0x40001004, 0x40001104, 0x40001008):
            pass
        else:
            buf, o = self._region(addr, size)
            if buf is None:
                raise RuntimeError('store to unmapped address 0x%08X at pc 0x%08X' % (addr, self.pc))
            buf[o:o + size] = val.to_bytes(size, 'little')

    # ---- execution

    def setx(self, rd, v):
        if rd:
            self.x[rd] = v & MASK

    def take_irq(self):
        if not self.irq_pending or not self.csr[0x300] & 8:
            return
        cause = min(self.irq_pending)
        self.irq_pending.discard(cause)
        st = self.csr[0x300]
        self.csr[0x300] = (st & ~0x88) | ((st & 8) << 4)   # MPIE = MIE, MIE = 0
        self.csr[0x341] = self.pc
        self.csr[0x342] = 0x80000000 | cause
        self.pc = self.csr[0x305] & ~3

    def step(self):
        self.take_irq()
        h = self.load(self.pc, 2, False)
        if h & 3 == 3:
            w, size = self.load(self.pc, 4, False), 4
        else:
            w, size = expand(h), 2
            self.compressed += 1
        if w == 0x0000006F:
            raise Halt()
        op = w & 0x7F
        rd = (w >> 7) & 31
        f3 = (w >> 12) & 7
        rs1 = (w >> 15) & 31
        rs2 = (w >> 20) & 31
        f7 = w >> 25
        a = self.x[rs1]
        b = self.x[rs2]
        sa, sb = sx(a, 32), sx(b, 32)
        imm_i = sx(w >> 20, 12)
        npc = (self.pc + size) & MASK
        if op == 0x37:
            self.setx(rd, w & 0xFFFFF000)
        elif op == 0x17:
            self.setx(rd, self.pc + (w & 0xFFFFF000))
        elif op == 0x6F:
            imm = sx((((w >> 31) & 1) << 20) | (((w >> 12) & 0xFF) << 12) |
                     (((w >> 20) & 1) << 11) | (((w >> 21) & 0x3FF) << 1), 21)
            self.setx(rd, npc)
            npc = (self.pc + imm) & MASK
        elif op == 0x67:
            t = (a + imm_i) & ~1 & MASK
            self.setx(rd, npc)
            npc = t
        elif op == 0x63:
            imm = sx((((w >> 31) & 1) << 12) | (((w >> 7) & 1) << 11) |
                     (((w >> 25) & 0x3F) << 5) | (((w >> 8) & 0xF) << 1), 13)
            take = {0: a == b, 1: a != b, 4: sa < sb, 5: sa >= sb, 6: a < b, 7: a >= b}[f3]
            if take:
                npc = (self.pc + imm) & MASK
        elif op == 0x03:
            size, signed = {0: (1, True), 1: (2, True), 2: (4, False), 4: (1, False), 5: (2, False)}[f3]
            self.setx(rd, self.load(a + imm_i, size, signed))
        elif op == 0x23:
            imm = sx(((w >> 25) << 5) | ((w >> 7) & 31), 12)
            self.store(a + imm, {0: 1, 1: 2, 2: 4}[f3], b)
        elif op == 0x13:
            sh = rs2
            if f3 == 0:
                v = a + imm_i
            elif f3 == 2:
                v = int(sa < imm_i)
            elif f3 == 3:
                v = int(a < (imm_i & MASK))
            elif f3 == 4:
                v = a ^ (imm_i & MASK)
            elif f3 == 6:
                v = a | (imm_i & MASK)
            elif f3 == 7:
                v = a & (imm_i & MASK)
            elif f3 == 1:
                assert f7 == 0, 'bad slli'
                v = a << sh
            elif f7 == 0:
                v = a >> sh
            elif f7 == 0x20:
                v = sa >> sh
            elif f7 == 0x30:
                v = (a >> sh) | (a << (32 - sh)) if sh else a
            else:
                raise RuntimeError('bad shift at 0x%08X' % self.pc)
            self.setx(rd, v)
        elif op == 0x33:
            sh = b & 31
            if f7 == 1:
                if f3 == 0:
                    v = a * b
                elif f3 == 1:
                    v = (sa * sb) >> 32
                elif f3 == 2:
                    v = (sa * b) >> 32
                elif f3 == 3:
                    v = (a * b) >> 32
                elif f3 == 4:
                    v = MASK if b == 0 else (sa // sb if sa * sb >= 0 else -(abs(sa) // abs(sb)))
                elif f3 == 5:
                    v = MASK if b == 0 else a // b
                elif f3 == 6:
                    v = a if b == 0 else sa - sb * (sa // sb if sa * sb >= 0 else -(abs(sa) // abs(sb)))
                else:
                    v = a if b == 0 else a % b
            elif f7 == 0x30:
                if f3 == 1:
                    v = (a << sh) | (a >> (32 - sh)) if sh else a
                elif f3 == 5:
                    v = (a >> sh) | (a << (32 - sh)) if sh else a
                else:
                    raise RuntimeError('bad Zbb op at 0x%08X' % self.pc)
            else:
                v = {(0, 0): lambda: a + b, (0x20, 0): lambda: a - b, (0, 1): lambda: a << sh,
                     (0, 2): lambda: int(sa < sb), (0, 3): lambda: int(a < b), (0, 4): lambda: a ^ b,
                     (0, 5): lambda: a >> sh, (0x20, 5): lambda: sa >> sh, (0, 6): lambda: a | b,
                     (0, 7): lambda: a & b}[(f7, f3)]()
            self.setx(rd, v)
        elif op == 0x73:
            if w == 0x30200073:      # mret
                st = self.csr[0x300]
                self.csr[0x300] = (st & ~0x88) | ((st >> 4) & 8) | 0x80
                npc = self.csr[0x341]
            elif f3 in (1, 2, 3, 5, 6, 7):
                csr = w >> 20
                old = self.csr.get(csr, 0)
                src = rs1 if f3 >= 5 else a
                if f3 in (1, 5):
                    new = src
                elif f3 in (2, 6):
                    new = old | src
                else:
                    new = old & ~src
                if (f3 in (1, 5)) or rs1:
                    self.csr[csr] = new & MASK
                self.setx(rd, old)
            else:
                raise RuntimeError('unsupported system instruction 0x%08X' % w)
        else:
            raise RuntimeError('illegal instruction 0x%08X at 0x%08X' % (w, self.pc))
        self.pc = npc
        self.steps += 1

    def run(self, max_steps=2_000_000, irq_at=None):
        """Run until the program reaches its final 'j .' loop. irq_at: {step: cause}."""
        irq_at = irq_at or {}
        try:
            while self.steps < max_steps:
                if self.steps in irq_at:
                    self.irq_pending.add(irq_at[self.steps])
                self.step()
        except Halt:
            return True
        return False

    def output(self):
        return ''.join(self.out)


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='run a RiscBasic .bin in the test simulator')
    ap.add_argument('bin')
    ap.add_argument('--code', type=lambda s: int(s, 0), default=0)
    ap.add_argument('--ram', type=lambda s: int(s, 0), default=0x8000)
    ap.add_argument('--ram-size', type=lambda s: int(s, 0), default=0x800)
    ap.add_argument('--steps', type=int, default=2_000_000)
    ap.add_argument('--input', default='', help=r'characters typed on UART0, e.g. "a12\r-5\rq" (\r = Enter)')
    a = ap.parse_args()
    s = Sim(open(a.bin, 'rb').read(), a.code, a.ram, a.ram_size)
    s.rx[0] = list(a.input.encode('latin-1').decode('unicode_escape'))
    done = s.run(a.steps)
    sys.stdout.write(s.output())
    print('\n[%s after %d instructions]' % ('halted' if done else 'step limit', s.steps))
