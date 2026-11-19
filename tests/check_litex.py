#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# https://github.com/micro-FPGA/RiscBasic

"""
Check tools/litex2bas.py: make the include from tests/litex/csr.json (a LiteX simulation SoC
with LEDs) for code in main_ram and in rom, and compile the LiteX examples (*_litex.bas) with it.

    python tests/check_litex.py
"""

import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, 'tools'))

import litex2bas               # noqa: E402

WANT = ['TARGET RV32I+M', 'CLOCK  1000000', 'RAM    AT &H1000_0000 SIZE 8192',
        'DEVICE UART0 AT &HF000_1800', 'BIT TXREADY = NOT TXFULL.0', 'FUNCTION UART0Get AS BYTE',
        'DEVICE LEDS AT &HF000_2000', 'REG UPTIME_CYCLES_W1 AT +36', 'REG UPTIME_CYCLES_W0 AT +40']


def main():
    with open(os.path.join(HERE, 'litex', 'csr.json')) as f:
        soc = json.load(f)
    failed = 0
    for code, base in (('main_ram', '&H4000_0000'), ('rom', '&H0000_0000')):
        text = litex2bas.generate(soc, code=code)
        missing = [w for w in WANT + ['CODE   AT %s' % base] if w not in text]
        print('%s  litex2bas --code %s' % ('FAIL' if missing else 'ok  ', code))
        if missing:
            failed += 1
            print('   missing: %s' % missing)
        with tempfile.TemporaryDirectory() as tmp:
            ex = os.path.join(tmp, 'examples')          # a copy: the include is made new
            shutil.copytree(os.path.join(ROOT, 'examples'), ex)
            with open(os.path.join(ex, 'include', 'CPU', 'LITEX.bas'), 'w') as f:
                f.write(text)
            for src in sorted(glob.glob(os.path.join(ex, '*_litex.bas'))):
                r = subprocess.run([sys.executable, os.path.join(ROOT, 'source', 'riscbasic.py'),
                                    src], capture_output=True, text=True)
                ok = r.returncode == 0 and os.path.isfile(src[:-4] + '.bin')
                print('%s    %s' % ('ok  ' if ok else 'FAIL', os.path.basename(src)))
                if not ok:
                    failed += 1
                    print(r.stdout + r.stderr)
    print('all tests passed' if not failed else '%d failed' % failed)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
