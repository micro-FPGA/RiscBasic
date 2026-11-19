#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
#
# litex2bas: make a RiscBasic include file from the csr.json of a LiteX SoC.
#
#   python tools/litex2bas.py build/<board>/csr.json -o examples/include/CPU/LITEX.bas
#
# csr.json is written by any LiteX target or by litex_sim with --csr-json=<file>.
# The include declares TARGET, CLOCK, CODE, RAM and one DEVICE for each CSR
# module of the SoC (UART0, TIMER0, LEDS, CTRL, ...), with the console handler
# of the UART. No dependencies.

import argparse
import json
import re
import sys

# TARGET for the LiteX CPUs with a known instruction set (cpu name, variant).
TARGETS = {
    ('vexriscv', 'minimal'):  'RV32I',
    ('vexriscv', 'lite'):     'RV32I+M',
    ('vexriscv', 'standard'): 'RV32I+M',
    ('vexriscv', 'full'):     'RV32I+M',
    ('vexriscv', 'imac'):     'RV32IC+M',
    ('picorv32', 'minimal'):  'RV32I',
    ('picorv32', 'standard'): 'RV32I+M',
    ('serv',     'standard'): 'RV32I',
    ('serv',     'mdu'):      'RV32I+M',
    ('femtorv',  'standard'): 'RV32I',
    ('firev',    'standard'): 'RV32I',
}

# CSR module names that get another DEVICE name (the examples use UART0).
RENAME = {'uart': 'UART0'}


def hexlit(v):
    s = '%08X' % v
    return '&H%s_%s' % (s[:4], s[4:])


def find_target(constants):
    cpu = constants.get('config_cpu_name')
    for key in constants:
        if key.startswith('config_cpu_variant_'):
            variant = key[len('config_cpu_variant_'):]
            return TARGETS.get((cpu, variant)), cpu, variant
    return None, cpu, None


def generate(soc, code='main_ram', ram='sram', target=None, code_size=None,
             stack=None, cpi=None, source='csr.json'):
    constants = soc['constants']
    memories = soc['memories']
    out = []
    w = out.append

    found, cpu, variant = find_target(constants)
    if target is None:
        target = found
    if target is None:
        raise SystemExit('litex2bas: no TARGET known for CPU %s (%s): give --target'
                         % (cpu, variant))
    for name in (code, ram):
        if name not in memories:
            raise SystemExit('litex2bas: no memory region "%s" in %s (regions: %s)'
                             % (name, source, ', '.join(memories)))
    if code_size is None:
        code_size = memories[code]['size']

    w("'")
    w("' LiteX SoC: %s" % constants.get('config_identifier', '?'))
    w("' CPU %s (%s), code in %s, variables in %s" % (cpu, variant, code, ram))
    w("' Made by tools/litex2bas.py from %s: do not edit, make it again." % source)
    w("'")
    w('')
    w('TARGET %s' % target)
    w('CLOCK  %d' % constants['config_clock_frequency'])
    if cpi is not None:
        w('CPI    %d' % cpi)
    w('CODE   AT %s SIZE %d' % (hexlit(memories[code]['base']), code_size))
    w('RAM    AT %s SIZE %d' % (hexlit(memories[ram]['base']), memories[ram]['size']))
    if stack is not None:
        w('STACK  %d' % stack)

    # CSR registers, grouped by module. The longest module name that matches wins.
    modules = sorted(soc['csr_bases'], key=len, reverse=True)
    regs = {m: [] for m in modules}
    for full, reg in soc['csr_registers'].items():
        for m in modules:
            if full.startswith(m + '_'):
                regs[m].append((reg['addr'], full[len(m) + 1:], reg))
                break

    for m in sorted(soc['csr_bases'], key=lambda m: soc['csr_bases'][m]):
        if not regs[m]:
            continue                      # a memory (identifier_mem), not registers
        base = soc['csr_bases'][m]
        dev = RENAME.get(m, re.sub(r'\W', '_', m).upper())
        w('')
        w('CONST LITEX_%s = 1' % dev)
        w('DEVICE %s AT %s' % (dev, hexlit(base)))
        for addr, name, reg in sorted(regs[m]):
            ro = '  READONLY' if reg['type'] == 'ro' else ''
            name = name.upper()
            if reg['size'] == 1:
                w('  REG %-13s AT +%-3d%s' % (name, addr - base, ro))
            else:                         # wider than 32 bits: high word first
                for i in range(reg['size']):
                    w('  REG %-13s AT +%-3d%s' % ('%s_W%d' % (name, reg['size'] - 1 - i),
                                                 addr - base + 4 * i, ro))
        if m == 'uart':
            w("  REG DATA          AT +%-3d            ' same register as RXTX"
              % (soc['csr_registers']['uart_rxtx']['addr'] - base))
            w('  BIT TXREADY = NOT TXFULL.0')
            w('  BIT RXREADY = NOT RXEMPTY.0')
            w("  Get                                  ' INPUT calls UART0Get")
        w('END DEVICE')
        if m == 'uart':
            w('')
            w("' PRINT uses DATA and TXREADY. INPUT needs this handler: the LiteX UART keeps a")
            w("' received character until the RX event is cleared.")
            w('FUNCTION UART0Get AS BYTE')
            w('DIM c AS WORD')
            w('  WAIT UNTIL UART0.RXREADY')
            w('  c = UART0.RXTX')
            w("  UART0.EV_PENDING = 2          ' clear the RX event: takes the character from the FIFO")
            w('  RETURN c')
            w('END FUNCTION')
    return '\n'.join(out) + '\n'


def main():
    ap = argparse.ArgumentParser(description='Make a RiscBasic include from a LiteX csr.json.')
    ap.add_argument('csr_json', help='csr.json of the LiteX SoC (--csr-json)')
    ap.add_argument('-o', '--output', help='include file to write (default: stdout)')
    ap.add_argument('--code', default='main_ram',
                    help='memory region for the program: main_ram (loaded by the LiteX BIOS, '
                         'default) or rom (in place of the BIOS)')
    ap.add_argument('--ram', default='sram', help='memory region for variables and stack')
    ap.add_argument('--target', help='TARGET, for example RV32I+M (default: from the CPU)')
    ap.add_argument('--code-size', type=lambda s: int(s, 0), help='CODE size in bytes')
    ap.add_argument('--stack', type=int, help='STACK size in bytes')
    ap.add_argument('--cpi', type=int, help='CPI, clock cycles per instruction')
    a = ap.parse_args()

    with open(a.csr_json) as f:
        soc = json.load(f)
    text = generate(soc, a.code, a.ram, a.target, a.code_size, a.stack, a.cpi, a.csr_json)
    if a.output:
        with open(a.output, 'w') as f:
            f.write(text)
    else:
        sys.stdout.write(text)


if __name__ == '__main__':
    main()
