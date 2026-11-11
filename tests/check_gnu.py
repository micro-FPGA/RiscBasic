#!/usr/bin/env python3
"""
Cross-check RiscBasic output against the GNU RISC-V toolchain.

For every test program on every target:
  1. objdump -d -M no-aliases decodes each instruction; address, encoding, mnemonic and
     registers must match the compiler's own listing.
  2. objcopy -O binary from the .elf must equal the compiler's .bin.
  3. readelf -a must run without warnings.
  4. mem2elf on the .mem file, then objcopy, must give the .bin again.

    python tests/check_gnu.py [--prefix G:/gcc/bin/riscv-none-elf-]
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

import run_tests as T          # noqa: E402
import riscbasic as rb         # noqa: E402

REG = re.compile(r'\b(zero|ra|sp|gp|tp|t[0-6]|s1[01]|s[0-9]|a[0-7])\b')
OBJ_LINE = re.compile(r'^\s*([0-9a-f]+):\s+([0-9a-f]+)\s+(\S+)\s*(.*)$')


def find_prefix():
    for p in ('riscv-none-elf-', 'riscv64-unknown-elf-', 'riscv32-unknown-elf-'):
        if shutil.which(p + 'objdump'):
            return p
    if os.path.exists(r'G:\gcc\bin\riscv-none-elf-objdump.exe'):
        return r'G:\gcc\bin\riscv-none-elf-'
    return None


def ours(c):
    """(address, encoding, mnemonic, registers) for every instruction the compiler emitted."""
    em = c.em
    out = []
    for i, (k, s) in enumerate(zip(em.kind, em.isz)):
        if s == 0 or k == 'data':
            continue
        if s == 8:
            parts = [(em.addr[i], em.code[i] & 0xFFFFFFFF, em.text[i][0]),
                     (em.addr[i] + 4, em.code[i] >> 32, em.text[i][1])]
        else:
            parts = [(em.addr[i], em.code[i], em.text[i])]
        for a, w, text in parts:
            text = text.split('   ;')[0]
            mn, _, ops = text.partition(' ')
            out.append((a, w, mn.strip(), REG.findall(ops)))
    return out


def gnu(prefix, elf):
    r = subprocess.run([prefix + 'objdump', '-d', '-M', 'no-aliases', elf],
                       capture_output=True, text=True, check=True)
    out = {}
    for line in r.stdout.splitlines():
        m = OBJ_LINE.match(line)
        if not m:
            continue
        mn = m.group(3)
        ops = re.sub(r'<[^>]*>', '', m.group(4).split('#')[0]).split(',')
        if mn in BRANCHY:              # last operand is a target address in hex, e.g. 'a4'
            ops = ops[:-1]
        regs = REG.findall(','.join(ops))
        if mn == 'c.addi' and regs == ['zero']:
            mn, regs = 'c.nop', []     # c.nop is c.addi zero, 0
        out[int(m.group(1), 16)] = (int(m.group(2), 16), mn, regs)
    return out


BRANCHY = {'beq', 'bne', 'blt', 'bge', 'bltu', 'bgeu', 'jal', 'c.j', 'c.jal', 'c.beqz', 'c.bnez'}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--prefix', help='toolchain prefix, e.g. G:/gcc/bin/riscv-none-elf-')
    a = ap.parse_args()
    prefix = a.prefix or find_prefix()
    if not prefix:
        print('no RISC-V GNU toolchain found; use --prefix')
        return 2
    tmp = tempfile.mkdtemp(prefix='rbgnu')
    total = bad = programs = 0
    lines_checked = [0]
    try:
        for name, body, expect, targets, irq_at, check in T.TESTS:
            for target in targets:
                programs += 1
                c = rb.Compiler('test.bas')
                c.compile(T.PRELUDE.format(target=target) + body)
                base = os.path.join(tmp, 'p%d' % programs)
                elf, binf, memf = base + '.elf', base + '.bin', base + '.mem'
                data = c.binary()
                open(elf, 'wb').write(c.elf())
                words = data + b'\0' * (-len(data) % 4)
                open(memf, 'w').write(''.join('%08X\n' % int.from_bytes(words[j:j + 4], 'little')
                                              for j in range(0, len(words), 4)))
                problems = []
                g = gnu(prefix, elf)
                for addr, w, mn, regs in ours(c):
                    total += 1
                    got = g.get(addr)
                    if got is None:
                        problems.append('%08X: objdump has no instruction here (ours: %s)' % (addr, mn))
                    elif got[0] != w or got[1] != mn or got[2] != regs:
                        problems.append('%08X: ours %08X %s %s | GNU %08X %s %s'
                                        % (addr, w, mn, regs, got[0], got[1], got[2]))
                subprocess.run([prefix + 'objcopy', '-O', 'binary', elf, binf], check=True)
                if open(binf, 'rb').read() != data:
                    problems.append('objcopy -O binary differs from the compiler .bin')
                r = subprocess.run([prefix + 'readelf', '-a', '-W', '--debug-dump=info,abbrev,line,aranges', elf],
                                   capture_output=True, text=True)
                if r.returncode or r.stderr.strip() or 'Warning' in r.stdout:
                    problems.append('readelf: %s' % (r.stderr.strip() or 'warning in output')[:200])
                r = subprocess.run([prefix + 'readelf', '--debug-dump=decodedline', elf],
                                   capture_output=True, text=True, check=True)
                decoded = []
                for line in r.stdout.splitlines():
                    t = line.split()
                    if len(t) >= 3 and t[0] == 'test.bas' and t[1].isdigit():
                        decoded.append((int(t[2], 16), int(t[1])))      # address 0 is printed as '0'
                expect_rows = c.line_rows()
                if decoded != expect_rows:
                    problems.append('DWARF line table differs: GNU %s... ours %s...'
                                    % (decoded[:4], expect_rows[:4]))
                lines_checked[0] += len(decoded)
                arch = rb.elf_arch(c.ext).split('_')[0][:5] + ('m' if 'M' in c.ext else '') + \
                    ('c' if 'C' in c.ext else '') + ('_zbb' if 'ZBB' in c.ext else '')
                subprocess.run([sys.executable, os.path.join(ROOT, 'mem2elf.py'), memf, '-o', base + 'm.elf',
                                '--arch', arch], check=True, capture_output=True)
                subprocess.run([prefix + 'objcopy', '-O', 'binary', base + 'm.elf', base + 'm.bin'], check=True)
                if open(base + 'm.bin', 'rb').read() != words:
                    problems.append('mem2elf + objcopy differs from the .mem contents')
                status = 'ok  ' if not problems else 'FAIL'
                print('%s %-50s %-12s' % (status, name[:50], target))
                for p in problems[:5]:
                    print('       ' + p)
                bad += bool(problems)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print('\n%d programs, %d instructions and %d DWARF line rows compared with GNU binutils: %s'
          % (programs, total, lines_checked[0], 'all match' if not bad else '%d program(s) with differences' % bad))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
