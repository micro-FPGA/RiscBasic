#!/usr/bin/env python3
# License: MIT
# Copyright 7AG (2026) MicroFPGA UG(h)
# https://github.com/micro-FPGA/RiscBasic

"""
Run examples/fit_interrupt.bas on a model of the MicroBlaze V MCS IO Module.

The model has the UART TX, GPO1, GPI1, FIT1 and the interrupt controller: latched IRQ_STATUS,
IRQ_PENDING = STATUS AND ENABLE, write-only IRQ_ENABLE / IRQ_MODE / IRQ_ACK (reads give
garbage) and the vector table. The interrupt line is level-sensitive, as in the MCS-V, and is
taken only with mie.MEIE set and mstatus.MIE on for at least LATENCY instructions, and raised again only after the CPU has
set mstatus.MIE again after the previous one (IO Module handshake). As on the board, mret and
ecall do nothing: the program must return from interrupts without them. The core then jumps to the vector address the
IO Module supplies (MicroBlaze V low-latency mode, as seen on hardware); it must be __trap.

The system starts as after an XSDB 'dow' without reset: interrupts still enabled in the CPU,
FIT1 enabled in the IO Module, all interrupts in fast mode, the AMD default vectors (0x10),
and the first FIT strobe comes during the startup code.

The example is built with -D FIT1_CLOCKS=10_000_000 (10 interrupts per second), so a short
run covers several seconds. If the example is not there (flat layout), the check is skipped.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, 'source') if os.path.isfile(os.path.join(ROOT, 'source', 'riscbasic.py')) else ROOT
sys.path.insert(0, SRC)

import riscbasic                      # noqa: E402
from rbsim import Sim, MASK           # noqa: E402

EXAMPLE = os.path.join(ROOT, 'examples', 'fit_interrupt.bas')
IOM = 0x80000000
GARBAGE = 0xBADC0DE5
FIT_STEPS = 250                       # instructions between FIT1 strobes
FIT_PHASE = 20                        # the first strobe: during the startup code
LATENCY = 10                          # MIE must be on this many instructions before an interrupt
                                      # is taken (model assumption: the core needs a few cycles)
STEPS = 60_000
PRESS, RELEASE = 20_000, 40_000       # button (active low) pressed and released


class MCSV(Sim):
    """MicroBlaze V MCS: 16 KB BRAM for code and RAM, IO Module at 0x80000000."""

    def __init__(self, image, trap):
        super().__init__(image, 0, 0x3800, 2048)
        self.trap = trap
        self.isr, self.ier, self.imr = 0, 0x80, 0xFFFFFFFF
        self.ivar = [0x10] * 32                 # AMD default: into the startup code
        self.csr[0x300] = 8                     # mstatus.MIE and mie.MEIE left on
        self.csr[0x304] = 0x800
        self.gpo, self.gpi = 0, 1
        self.gpo_log, self.wo_reads, self.uart = [], [], []
        self.taken = self.since_init = self.mrets = self.returns = 0
        self.handling = False
        self.mie_run = 0

    def load(self, addr, size, signed):
        a = addr & MASK
        if IOM <= a < IOM + 0x100:
            v = {0x00: 0, 0x08: 0, 0x20: self.gpi, 0x30: self.isr,
                 0x34: self.isr & self.ier}.get(a - IOM)
            if v is None:                 # write-only register
                self.wo_reads.append((a - IOM, self.pc))
                v = GARBAGE
            return v & ((1 << (8 * size)) - 1)
        return super().load(addr, size, signed)

    def store(self, addr, size, val):
        a = addr & MASK
        if not IOM <= a < IOM + 0x100:
            return super().store(addr, size, val)
        o = a - IOM
        if o == 0x04:
            self.uart.append(chr(val & 0xFF))
        elif o == 0x10:
            self.gpo = val
            self.gpo_log.append((self.steps, val))
        elif o == 0x0C:
            self.imr = val
        elif o == 0x38:
            self.ier = val
        elif o == 0x3C:
            self.isr &= ~val & MASK
            if val == 0xFFFFFFFF:
                self.since_init = 0       # IntcInit acknowledged everything
        elif 0x80 <= o < 0x100:
            self.ivar[(o - 0x80) // 4] = val
        else:
            raise RuntimeError('store to IO Module offset 0x%02X at pc 0x%X' % (o, self.pc))

    def take_irq(self):
        # IO Module handshake (Fast_FSM): after the CPU takes an interrupt, INTC_IRQ stays low
        # until the CPU returns from it (mret, second Interrupt_Ack)
        p = self.isr & self.ier
        if self.handling or not p or self.mie_run < LATENCY or not self.csr[0x304] & 0x800:
            return
        n = (p & -p).bit_length() - 1     # lowest number = highest priority
        if self.imr >> n & 1:
            raise RuntimeError('interrupt %d taken in fast mode' % n)
        if self.ivar[n] != self.trap:
            raise RuntimeError('interrupt %d jumps to vector 0x%X, not __trap 0x%X (pc 0x%X)'
                               % (n, self.ivar[n], self.trap, self.pc))
        st = self.csr[0x300]
        self.csr[0x300] = (st & ~0x88) | ((st & 8) << 4)
        self.csr[0x341] = self.pc
        self.csr[0x342] = 0x8000000B      # machine external interrupt
        self.pc = self.ivar[n]                # low-latency mode: the IO Module's vector
        self.taken += 1
        self.handling = True

    def step(self):
        self.mie_run = self.mie_run + 1 if self.csr[0x300] & 8 else 0
        w = self.load(self.pc, 4, False)
        if w in (0x30200073, 0x00000073):       # mret, ecall: no-ops on the MicroBlaze V MCS
            self.mrets += w == 0x30200073
            self.pc += 4
            self.steps += 1
            return
        mie_before = self.csr[0x300] & 8
        if self.steps % FIT_STEPS == FIT_PHASE:
            self.isr |= 0x80              # FIT1 strobe, latched by the INTC
            self.since_init += 1
        if self.steps == PRESS:
            self.gpi = 0
        elif self.steps == RELEASE:
            self.gpi = 1
        super().step()
        if self.handling and not mie_before and self.csr[0x300] & 8:
            self.handling = False                 # MIE set again: the handshake is complete
            self.returns += 1


def run():
    """Build and run the example; returns a list of (description, ok)."""
    defines = {'FIT1_CLOCKS': '10_000_000'}
    c = riscbasic.Compiler('fit_interrupt.bas')
    c.src_dir = os.path.dirname(EXAMPLE)
    c.src_path = EXAMPLE
    c.defines = defines
    try:
        with open(EXAMPLE, encoding='utf-8') as f:
            c.compile(f.read())
    except riscbasic.RBError as e:
        where = '%s:%s' % (e.file or os.path.basename(EXAMPLE), e.line)
        return [('compiles: %s: %s' % (where, e), False)]
    trap = c.em.addr_at(c.em.labels['__trap'])
    s = MCSV(c.binary(), trap)
    try:
        s.run(max_steps=STEPS)
    except RuntimeError as e:
        return [('runs without a fault: %s' % e, False)]
    ram = {name.upper(): addr for name, addr, size in c.ram_syms}
    word = lambda name: s.load(ram[name], 4, False)     # code and RAM share the BRAM
    ticks, seconds = word('TICKS'), word('SECONDS')
    out = ''.join(s.uart)
    green = [v & 1 for _, v in s.gpo_log]
    toggles = sum(1 for a, b in zip(green, green[1:]) if a != b)

    def red_at(step):
        return next((v >> 1 & 1 for st, v in reversed(s.gpo_log) if st <= step), None)

    return [
        ('IRQ_MODE normal, vectors 0..15 = __trap', s.imr == 0 and s.ivar[:16] == [trap] * 16),
        ('IRQ_ENABLE = FIT1 only, mie.MEIE and MIE (or MPIE in the handler) set',
         s.ier == 0x80 and bool(s.csr[0x304] & 0x800 and s.csr[0x300] & 0x88)),
        ('no reads of write-only registers', not s.wo_reads),
        ('every interrupt returns without mret (%d of %d returns, %d mret executed)' % (s.returns, s.taken, s.mrets),
         s.taken - 1 <= s.returns <= s.taken and s.mrets == 0),
        ('every FIT strobe since IntcInit counted once (ticks %d, strobes %d, interrupts %d)'
         % (ticks, s.since_init, s.taken),
         s.since_init - 1 <= ticks <= s.since_init and s.taken == ticks and ticks > 50),
        ('seconds = ticks / 10, green LED toggled each second (%d s, %d toggles)' % (seconds, toggles),
         seconds == ticks // 10 and toggles == seconds),
        ('banner and an uptime line for every second',
         out.startswith('RiscBasic FIT1 interrupt demo, 10 interrupts per second\r\n')
         and all(('uptime %d s' % k) in out for k in range(1, seconds + 1))),
        ('red LED follows the button', (red_at(PRESS - 1000), red_at(RELEASE - 1000), red_at(STEPS)) == (0, 1, 0)),
    ]


def main():
    if not os.path.isfile(EXAMPLE):
        print('skip MicroBlaze V MCS FIT example: %s not found' % os.path.relpath(EXAMPLE, ROOT))
        return 0
    failed = 0
    for text, ok in run():
        print('%s MCS-V FIT example: %s' % ('ok  ' if ok else 'FAIL', text))
        failed += not ok
    return failed


if __name__ == '__main__':
    n = main()
    print('all MCS-V checks passed' if n == 0 else '%d MCS-V check(s) failed' % n)
    sys.exit(1 if n else 0)
