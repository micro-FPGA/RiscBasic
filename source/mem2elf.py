#!/usr/bin/env python3
# License: MIT
# Copyright 7AG (2026) MicroFPGA UG(h)
# https://github.com/micro-FPGA/RiscBasic

"""
mem2elf: convert a $readmemh .mem file (one 32-bit word per line) into an ELF32 RISC-V executable.

    python mem2elf.py program.mem [-o program.elf] [--base 0x0] [--arch rv32imc] [--entry 0x0]

A .mem file carries no addresses or symbols, so give the load address with --base.
The ELF gets the code as one .text section, an entry point, the ISA string and the
RVC / RVE flags, plus a __start symbol; nothing else. For an ELF with all labels
and variables, compile with riscbasic.py instead: it writes the .elf directly.

$readmemh '@address' lines are supported; the address is a word index. Comments
(// and /* */ on one line) are ignored.
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from riscbasic import elf32   # noqa: E402


def parse_arch(s):
    m = re.fullmatch(r'rv32([ie])([a-z]*)((?:_[a-z0-9]+)*)', s.strip().lower())
    if not m:
        raise ValueError("bad --arch '%s' (examples: rv32i, rv32imc, rv32ec, rv32imc_zbb)" % s)
    ext = set()
    if m.group(1) == 'e':
        ext.add('E')
    letters = m.group(2)
    if 'm' in letters:
        ext.add('M')
    if 'c' in letters:
        ext.add('C')
    if 'zbb' in m.group(3).split('_'):
        ext.add('ZBB')
    return ext


def read_mem(path):
    words = {}
    pos = 0
    with open(path, encoding='utf-8-sig') as f:
        for n, line in enumerate(f, 1):
            line = re.sub(r'/\*.*?\*/', ' ', line).split('//')[0].split('#')[0]
            for tok in line.split():
                if tok.startswith('@'):
                    pos = int(tok[1:], 16)
                    continue
                try:
                    words[pos] = int(tok.replace('_', ''), 16) & 0xFFFFFFFF
                except ValueError:
                    raise ValueError('%s:%d: bad word %r' % (path, n, tok))
                pos += 1
    if not words:
        raise ValueError('%s: no data' % path)
    data = bytearray(4 * (max(words) + 1))
    for i, w in words.items():
        data[4 * i:4 * i + 4] = w.to_bytes(4, 'little')
    return bytes(data)


def main(argv=None):
    ap = argparse.ArgumentParser(description='convert a .mem file into an ELF32 RISC-V executable')
    ap.add_argument('mem')
    ap.add_argument('-o', '--output', help='output .elf (default: input name with .elf)')
    ap.add_argument('--base', type=lambda s: int(s, 0), default=0, help='load address (default 0)')
    ap.add_argument('--entry', type=lambda s: int(s, 0), help='entry point (default: base)')
    ap.add_argument('--arch', default='rv32i', help='ISA, e.g. rv32i, rv32imc, rv32ec_zbb (default rv32i)')
    a = ap.parse_args(argv)
    try:
        ext = parse_arch(a.arch)
        image = read_mem(a.mem)
    except (ValueError, OSError) as e:
        print('error: %s' % e, file=sys.stderr)
        return 1
    entry = a.base if a.entry is None else a.entry
    out = a.output or os.path.splitext(a.mem)[0] + '.elf'
    syms = [('$x', a.base, 0, 'notype', 'text'), ('__start', entry, 0, 'func', 'text')]
    with open(out, 'wb') as f:
        f.write(elf32(image, a.base, ext, syms, None, entry))
    print('%s: %d bytes at 0x%08X, entry 0x%08X, %s' % (out, len(image), a.base, entry, a.arch))
    return 0


if __name__ == '__main__':
    sys.exit(main())
