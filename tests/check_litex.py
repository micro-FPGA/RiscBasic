#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# https://github.com/micro-FPGA/RiscBasic

"""
Check tools/litex2bas.py: make the include from tests/litex/csr.json (a LiteX simulation SoC
with LEDs) for code in main_ram and in rom, and compile a program that uses it.

    python tests/check_litex.py
"""

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'tools'))

import litex2bas               # noqa: E402

PROGRAM = '''
.include "LITEX.bas"
.include "%s"
.include "%s"
DIM c AS BYTE
ON IRQ MEXT
  TIMER0.EV_PENDING = 1
END IRQ
IrqEnable IRQ_TIMER0
IrqOn
PRINT "hello", CR, LF
#IF DEFINED(LITEX_LEDS)
LEDS.OUT = 5
#ENDIF
TIMER0.EN = 0
INPUT CHR c
PRINT HEX TIMER0.UPTIME_CYCLES_W0, CHR c, CR, LF
''' % tuple(os.path.join(ROOT, 'examples', 'lib', f).replace('\\', '/')
            for f in ('litex_uart.bas', 'litex_vexriscv_irq.bas'))


def main():
    with open(os.path.join(HERE, 'litex', 'csr.json')) as f:
        soc = json.load(f)
    failed = 0
    for code, base in (('main_ram', '&H4000_0000'), ('rom', '&H0000_0000')):
        text = litex2bas.generate(soc, code=code)
        want = ['TARGET RV32I+M', 'CLOCK  1000000', 'CODE   AT %s' % base,
                'RAM    AT &H1000_0000 SIZE 8192', 'DEVICE UART0 AT &HF000_1800',
                'BIT TXREADY = NOT TXFULL.0', 'DEVICE LEDS AT &HF000_2000',
                'REG UPTIME_CYCLES_W1 AT +36', 'REG UPTIME_CYCLES_W0 AT +40',
                'CONST IRQ_TIMER0 = 1', 'CONST IRQ_UART0 = 0', 'CONST LITEX_CLOCK = 1000000']
        missing = [w for w in want if w not in text]
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, 'LITEX.bas'), 'w') as f:
                f.write(text)
            with open(os.path.join(tmp, 'prog.bas'), 'w') as f:
                f.write(PROGRAM)
            r = subprocess.run([sys.executable, os.path.join(ROOT, 'source', 'riscbasic.py'),
                                os.path.join(tmp, 'prog.bas')], capture_output=True, text=True)
            ok = not missing and r.returncode == 0 and os.path.isfile(os.path.join(tmp, 'prog.bin'))
        print('%s  litex2bas --code %s' % ('ok  ' if ok else 'FAIL', code))
        if not ok:
            failed += 1
            print('   missing: %s\n%s%s' % (missing, r.stdout, r.stderr))
    print('all tests passed' if not failed else '%d failed' % failed)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
