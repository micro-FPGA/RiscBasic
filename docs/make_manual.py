#!/usr/bin/env python3
# License: MIT
# Copyright 7AG (2026) MicroFPGA UG(h)
# https://github.com/micro-FPGA/RiscBasic

"""
make_manual: build the RiscBasic Reference Manual as .docx, and optionally as .pdf.

    python docs/make_manual.py            # docs/RiscBasic-Reference-Manual.docx
    python docs/make_manual.py --pdf      # also update the table of contents and write the .pdf

The compiler version is taken from riscbasic.py, the draft number and date from RiscBasic-Spec.md.
Every complete example program in the manual is compiled first; the build stops if one fails.

Needs python-docx. --pdf needs Microsoft Word on Windows and pywin32: Word updates the table of
contents and exports the PDF. The script starts its own Word process, checks that it is a new one,
and never touches a Word that is already running.
"""

import argparse
import os
import re
import subprocess
import sys
import time

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_BREAK, WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

HERE = os.path.dirname(os.path.abspath(__file__))
ap = argparse.ArgumentParser(description='Build the RiscBasic Reference Manual')
ap.add_argument('-o', '--output', default=os.path.join(HERE, 'RiscBasic-Reference-Manual.docx'),
                help='output .docx (default: docs/RiscBasic-Reference-Manual.docx)')
ap.add_argument('--root', default=os.path.dirname(HERE), help='repository root (default: parent of docs/)')
ap.add_argument('--pdf', action='store_true', help='also update the TOC and export a .pdf with Word')
ARGS = ap.parse_args()
ROOT = os.path.abspath(ARGS.root)
OUT = os.path.abspath(ARGS.output)


def find(name, subdir):
    """A file in ROOT/subdir (GitHub layout: source/, docs/) or in ROOT itself."""
    for d in (os.path.join(ROOT, subdir), ROOT):
        if os.path.isfile(os.path.join(d, name)):
            return os.path.join(d, name)
    raise SystemExit('%s not found in %s or %s' % (name, os.path.join(ROOT, subdir), ROOT))


sys.path.insert(0, os.path.dirname(find('riscbasic.py', 'source')))
import riscbasic  # noqa: E402

ACCENT = RGBColor(0x1F, 0x4E, 0x79)
GREY = RGBColor(0x59, 0x59, 0x59)
BODY_FONT, CODE_FONT = 'Calibri', 'Consolas'
VERSION = riscbasic.VERSION
with open(find('RiscBasic-Spec.md', 'docs'), encoding='utf-8') as f:
    m = re.search(r'\*\*Draft ([0-9.]+)\*\*\s*\S+\s*(.+)', f.read())
if not m:
    raise SystemExit('RiscBasic-Spec.md: no "**Draft x.y** — date" line found')
DRAFT, DATE = m.group(1), m.group(2).strip()

# ---------------------------------------------------------------- example check

HEADER_BAS = '''TARGET RV32IC
CLOCK  25_000_000
CODE   AT &H0000 SIZE 8K
RAM    AT &H8000 SIZE 2K
'''


def compile_check(src, name):
    c = riscbasic.Compiler(name + '.bas')
    try:
        c.compile(src)
    except riscbasic.RBError as e:
        raise SystemExit('example %s does not compile: line %s: %s' % (name, e.line, e))
    return c


# ---------------------------------------------------------------- document helpers

doc = Document()
CHAPTERS = []          # (number, title) for cross references
h_counter = [0, 0, 0]


def set_font(obj, name):
    rpr = obj.element.get_or_add_rPr() if hasattr(obj, 'element') else obj
    rf = rpr.find(qn('w:rFonts'))
    if rf is None:
        rf = OxmlElement('w:rFonts')
        rpr.insert(0, rf)
    for a in list(rf.attrib):
        if a.endswith('Theme'):
            del rf.attrib[a]
    for a in ('w:ascii', 'w:hAnsi', 'w:cs', 'w:eastAsia'):
        rf.set(qn(a), name)


PPR_AFTER_SHD = ['w:tabs', 'w:suppressAutoHyphens', 'w:kinsoku', 'w:wordWrap', 'w:overflowPunct',
                 'w:topLinePunct', 'w:autoSpaceDE', 'w:autoSpaceDN', 'w:bidi', 'w:adjustRightInd',
                 'w:snapToGrid', 'w:spacing', 'w:ind', 'w:contextualSpacing', 'w:mirrorIndents',
                 'w:suppressOverlap', 'w:jc', 'w:textDirection', 'w:textAlignment',
                 'w:textboxTightWrap', 'w:outlineLvl', 'w:divId', 'w:cnfStyle', 'w:rPr',
                 'w:sectPr', 'w:pPrChange']


def shade_ppr(ppr, fill, left=None):
    if left:
        bdr = OxmlElement('w:pBdr')
        el = OxmlElement('w:left')
        el.set(qn('w:val'), 'single')
        el.set(qn('w:sz'), '18')
        el.set(qn('w:space'), '6')
        el.set(qn('w:color'), left)
        bdr.append(el)
        ppr.insert_element_before(bdr, 'w:shd', *PPR_AFTER_SHD)
    shd = OxmlElement('w:shd')
    shd.set(qn('w:val'), 'clear')
    shd.set(qn('w:color'), 'auto')
    shd.set(qn('w:fill'), fill)
    ppr.insert_element_before(shd, *PPR_AFTER_SHD)


def setup_styles():
    st = doc.styles
    normal = st['Normal']
    set_font(normal, BODY_FONT)
    normal.font.size = Pt(10.5)
    normal.paragraph_format.space_after = Pt(5)
    normal.paragraph_format.line_spacing = 1.12
    for name, size, before, after in (('Title', 30, 0, 6), ('Heading 1', 20, 0, 10),
                                      ('Heading 2', 14, 14, 5), ('Heading 3', 11.5, 10, 3)):
        s = st[name]
        set_font(s, BODY_FONT)
        s.font.size = Pt(size)
        s.font.bold = name != 'Title'
        s.font.italic = False
        s.font.color.rgb = ACCENT
        pf = s.paragraph_format
        pf.space_before = Pt(before)
        pf.space_after = Pt(after)
        pf.keep_with_next = True
    st['Heading 1'].paragraph_format.page_break_before = True
    code = st.add_style('Code', 1)
    code.base_style = normal
    set_font(code, CODE_FONT)
    code.font.size = Pt(8.5)
    pf = code.paragraph_format
    pf.space_after = Pt(0)
    pf.space_before = Pt(0)
    pf.line_spacing = 1.0
    pf.left_indent = Cm(0.25)
    shade_ppr(code.element.get_or_add_pPr(), 'F3F5F8', left='9DB3CC')
    for level, indent, bold in ((1, 0, True), (2, 0.5, False)):    # Word's built-in 'toc n' styles
        el = OxmlElement('w:style')
        el.set(qn('w:type'), 'paragraph')
        el.set(qn('w:styleId'), 'TOC%d' % level)
        for tag, val in (('w:name', 'toc %d' % level), ('w:basedOn', 'Normal'), ('w:next', 'Normal'),
                         ('w:uiPriority', '39')):
            x = OxmlElement(tag)
            x.set(qn('w:val'), val)
            el.append(x)
        el.append(OxmlElement('w:unhideWhenUsed'))
        ppr = OxmlElement('w:pPr')
        tabs = OxmlElement('w:tabs')
        tab = OxmlElement('w:tab')
        tab.set(qn('w:val'), 'right')
        tab.set(qn('w:leader'), 'dot')
        tab.set(qn('w:pos'), str(int(16.4 * 567)))
        tabs.append(tab)
        ppr.append(tabs)
        sp = OxmlElement('w:spacing')
        sp.set(qn('w:before'), '60' if level == 1 else '0')
        sp.set(qn('w:after'), '20')
        sp.set(qn('w:line'), '240')
        sp.set(qn('w:lineRule'), 'auto')
        ppr.append(sp)
        ind = OxmlElement('w:ind')
        ind.set(qn('w:left'), str(int(indent * 567)))
        ppr.append(ind)
        el.append(ppr)
        if bold:
            rpr = OxmlElement('w:rPr')
            rpr.append(OxmlElement('w:b'))
            el.append(rpr)
        st.element.append(el)
    note = st.add_style('Note', 1)
    note.base_style = normal
    note.font.size = Pt(10)
    note.paragraph_format.left_indent = Cm(0.25)
    note.paragraph_format.space_before = Pt(4)
    note.paragraph_format.space_after = Pt(8)
    shade_ppr(note.element.get_or_add_pPr(), 'FFF6DB', left='D9A400')
    for name in ('List Bullet', 'List Number', 'List Bullet 2'):
        s = st[name]
        set_font(s, BODY_FONT)
        s.font.size = Pt(10.5)
        s.paragraph_format.space_after = Pt(3)


INLINE_RE = re.compile(r'(\*\*.+?\*\*|`[^`]+`|\*[A-Za-z][^*`]*[A-Za-z]\*)')


def runs(p, text, size=None, bold=False, color=None):
    for part in INLINE_RE.split(text):
        if not part:
            continue
        if part.startswith('**') and part.endswith('**') and len(part) > 4:
            r = p.add_run(part[2:-2])
            r.bold = True
        elif part.startswith('`') and part.endswith('`'):
            r = p.add_run(part[1:-1])
            set_font(r, CODE_FONT)
            r.font.size = Pt((size or 10.5) - 1)
            r.font.color.rgb = RGBColor(0x1A, 0x3A, 0x5C)
            if bold:
                r.bold = True
        elif part.startswith('*') and part.endswith('*') and len(part) > 2:
            r = p.add_run(part[1:-1])
            r.italic = True
            if bold:
                r.bold = True
        else:
            r = p.add_run(part)
            if bold:
                r.bold = True
        if size and not (part.startswith('`') and part.endswith('`')):
            r.font.size = Pt(size)
        if color is not None:
            r.font.color.rgb = color
    return p


def P(text, style=None):
    return runs(doc.add_paragraph(style=style), text)


def B(*items):
    for it in items:
        runs(doc.add_paragraph(style='List Bullet'), it)


def N(*items):
    numbering = doc.part.numbering_part.element
    style_num = doc.styles['List Number'].element.pPr.numPr.numId.val
    abstract = numbering.num_having_numId(style_num).abstractNumId.val
    num = numbering.add_num(abstract)
    num.add_lvlOverride(ilvl=0).add_startOverride(1)
    for it in items:
        p = runs(doc.add_paragraph(style='List Number'), it)
        numpr = p._p.get_or_add_pPr().get_or_add_numPr()
        numpr.get_or_add_ilvl().val = 0
        numpr.get_or_add_numId().val = num.numId


def NOTE(text):
    return runs(doc.add_paragraph(style='Note'), text)


def CODE(text):
    lines = text.strip('\n').split('\n')
    for i, ln in enumerate(lines):
        p = doc.add_paragraph(style='Code')
        p.add_run(ln if ln else ' ')
        pf = p.paragraph_format
        pf.keep_together = True
        if i < len(lines) - 1:
            pf.keep_with_next = True
        if i == 0:
            pf.space_before = Pt(3)
        if i == len(lines) - 1:
            pf.space_after = Pt(8)


def H1(title):
    h_counter[0] += 1
    h_counter[1] = h_counter[2] = 0
    CHAPTERS.append((h_counter[0], title))
    doc.add_heading('%d  %s' % (h_counter[0], title), level=1)


def H2(title):
    h_counter[1] += 1
    h_counter[2] = 0
    top = APPX[0] or h_counter[0]
    doc.add_heading('%s.%d  %s' % (top, h_counter[1], title), level=2)


def H3(title):
    h_counter[2] += 1
    doc.add_heading('%d.%d.%d  %s' % (h_counter[0], h_counter[1], h_counter[2], title), level=3)


APPX = [None]


def APPENDIX(letter, title):
    APPX[0] = letter
    h_counter[1] = 0
    CHAPTERS.append((letter, title))
    doc.add_heading('Appendix %s  %s' % (letter, title), level=1)


def cell_shade(cell, fill):
    tcpr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement('w:shd')
    shd.set(qn('w:val'), 'clear')
    shd.set(qn('w:color'), 'auto')
    shd.set(qn('w:fill'), fill)
    tcpr.append(shd)


def table_borders(table, color='B7C4D3'):
    tblpr = table._tbl.tblPr
    borders = OxmlElement('w:tblBorders')
    for edge in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'):
        el = OxmlElement('w:' + edge)
        el.set(qn('w:val'), 'single')
        el.set(qn('w:sz'), '4')
        el.set(qn('w:space'), '0')
        el.set(qn('w:color'), color)
        borders.append(el)
    tblpr.append(borders)


def T(headers, rows, widths, size=9):
    """Table with a shaded, repeated header row. widths in cm (sum about 16)."""
    table = doc.add_table(rows=1 + len(rows), cols=len(headers))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.autofit = False
    table_borders(table)
    for r, row in enumerate([headers] + rows):
        tr = table.rows[r]
        if r == 0:
            trpr = tr._tr.get_or_add_trPr()
            th = OxmlElement('w:tblHeader')
            th.set(qn('w:val'), 'true')
            trpr.append(th)
        cant = OxmlElement('w:cantSplit')
        tr._tr.get_or_add_trPr().append(cant)
        for c, text in enumerate(row):
            cell = tr.cells[c]
            cell.width = Cm(widths[c])
            p = cell.paragraphs[0]
            p.paragraph_format.space_after = Pt(1)
            p.paragraph_format.space_before = Pt(1)
            p.paragraph_format.line_spacing = 1.0
            runs(p, text, size=size, bold=(r == 0), color=ACCENT if r == 0 else None)
            if r == 0:
                cell_shade(cell, 'DCE6F0')
    grid = table._tbl.tblGrid
    for i, gc in enumerate(grid.findall(qn('w:gridCol'))):
        gc.set(qn('w:w'), str(int(widths[i] * 567)))
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return table


def field(p, instr, placeholder=''):
    r = p.add_run()
    fc = OxmlElement('w:fldChar')
    fc.set(qn('w:fldCharType'), 'begin')
    r._r.append(fc)
    r = p.add_run()
    it = OxmlElement('w:instrText')
    it.set(qn('xml:space'), 'preserve')
    it.text = ' %s ' % instr
    r._r.append(it)
    r = p.add_run()
    fc = OxmlElement('w:fldChar')
    fc.set(qn('w:fldCharType'), 'separate')
    r._r.append(fc)
    p.add_run(placeholder)
    r = p.add_run()
    fc = OxmlElement('w:fldChar')
    fc.set(qn('w:fldCharType'), 'end')
    r._r.append(fc)


def page_setup():
    s = doc.sections[0]
    s.page_width, s.page_height = Cm(21.0), Cm(29.7)
    s.left_margin = s.right_margin = Cm(2.3)
    s.top_margin = Cm(2.2)
    s.bottom_margin = Cm(2.2)
    s.different_first_page_header_footer = True
    doc.styles['Footer'].paragraph_format.tab_stops.clear_all()
    fp = s.footer.paragraphs[0]
    fp.paragraph_format.tab_stops.add_tab_stop(Cm(16.4), WD_TAB_ALIGNMENT.RIGHT)
    runs(fp, 'RiscBasic Reference Manual  ·  compiler v%s  ·  MIT License' % VERSION, size=8.5, color=GREY)
    r = fp.add_run('\t')
    r.font.size = Pt(8.5)
    field(fp, 'PAGE', '1')
    for r in fp.runs:
        r.font.size = Pt(8.5)
        r.font.color.rgb = GREY


# ---------------------------------------------------------------- content

setup_styles()
page_setup()

# title page
for _ in range(5):
    doc.add_paragraph()
p = doc.add_paragraph(style='Title')
p.add_run('RiscBasic')
p = doc.add_paragraph()
runs(p, 'Reference Manual', size=22, color=ACCENT)
p = doc.add_paragraph()
p.paragraph_format.space_before = Pt(8)
runs(p, 'A BASIC for RISC-V microcontrollers and FPGA soft cores, compiled directly to machine code',
     size=12.5, color=GREY)
for _ in range(3):
    doc.add_paragraph()
for line in ('Compiler version %s  ·  Language specification Draft %s' % (VERSION, DRAFT),
             DATE,
             '',
             'Language design: Antti Lukats (Identor #1)',
             'Compiler: Claude (Identor #9)'):
    runs(doc.add_paragraph(), line, size=11)
for _ in range(8):
    doc.add_paragraph()
for line in ('License: MIT', 'Copyright 7AG (2026) MicroFPGA UG(h)', 'https://github.com/micro-FPGA/RiscBasic'):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(0)
    runs(p, line, size=9.5, color=GREY)
doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

# contents
p = doc.add_paragraph()
runs(p, 'Contents', size=20, bold=True, color=ACCENT)
p = doc.add_paragraph()
field(p, 'TOC \\o "1-2" \\h \\z \\u', 'Right-click and choose Update Field to build the table of contents.')

# ---- 1 Introduction
H1('Introduction')
P('RiscBasic is a BASIC-like language for RISC-V processors with small code memories: soft cores in FPGAs '
  'and small hard cores, typically with **8 KB of code memory or less**. The compiler translates each '
  'BASIC line directly into RISC-V machine code in a single pass. There is no assembler step, no object '
  'file and no linker.')
P('The language follows one principle: **as easy as BASIC, as efficient as assembler**. Hardware is '
  'accessed through *objects*: CPU registers, bits and fields of registers, variables, memory and '
  'memory-mapped device registers. Every hardware action can be written as one assignment, '
  '`DEST = SOURCE`, and the compiler picks the shortest correct instruction sequence for the pair of '
  'object kinds.')
H2('Design goals')
B('Single-pass, direct translation from BASIC to machine code.',
  'Predictable code: every compile writes a listing that shows each source line with the addresses, '
  'machine code and disassembly generated for it.',
  'Small runtime: helper routines (multiply, divide, print, string handling) are included only when a '
  'program uses them, each at most once. This *smart inclusion* replaces the linker.',
  'Standard output files: raw binary, Intel HEX, `$readmemh` memory files for FPGA block RAM, and ELF '
  'with symbols and DWARF line information for source-level debugging.',
  'The compiler runs on a PC and is written in Python 3, with no dependencies.')
P('**Not part of the language:** floating point, dynamic memory, heap-allocated strings and an operating '
  'system. Text is handled in fixed-size buffers (chapter 11).')
H2('The toolchain')
T(['File', 'Purpose'], [
    ['`riscbasic.py`', 'The compiler. Reads a `.bas` file and writes `.bin`, `.hex`, `.mem`, `.lst` and `.elf`.'],
    ['`rbsim.py`', 'A small RV32I/M/C/Zbb instruction-set simulator, used for testing compiled programs.'],
    ['`mem2elf.py`', 'Converts a `.mem` file into an ELF executable when only the memory image is available.'],
    ['`tests/run_tests.py`', 'The test suite: compiles test programs for several targets and runs them in the simulator.'],
    ['`tests/check_gnu.py`', 'Cross-checks the compiler output against GNU binutils (objdump, objcopy, readelf).'],
    ['`RiscBasic-Spec.md`', 'The language specification. This manual describes the same language for programmers.'],
], [4.2, 12.2])
H2('Conventions in this manual')
B('Keywords are written in upper case (`PRINT`, `DEVICE`), but RiscBasic is **not case sensitive**: '
  '`print`, `Print` and `PRINT` are the same. Names are case-insensitive too.',
  'Square brackets in syntax lines mark optional parts: `FOR var = a TO b [STEP s]`. In program text, '
  '`[ ]` is used for bit fields and memory addresses.',
  'Generated code is shown with RISC-V ABI register names, as the listing shows it.')

# ---- 2 Getting started
H1('Getting started')
H2('Requirements')
P('The compiler needs Python 3 and nothing else. Copy `riscbasic.py` (and optionally `rbsim.py` and '
  '`mem2elf.py`) to any directory. A GNU RISC-V toolchain is not needed to compile; it is useful for '
  'debugging with GDB.')
H2('A first program')
HELLO = HEADER_BAS + '''
DEVICE UART0 AT &H4000_1000
  REG DATA   AT +0 : 8
  REG STATUS AT +4 READONLY
  BIT TXREADY = STATUS.0
END DEVICE

CONSOLE UART0
PRINT "Hello, RISC-V!", 13, 10
'''
CODE(HELLO)
c = compile_check(HELLO, 'hello')
P('The first four lines describe the target: the instruction set, the clock frequency, and where code '
  'and RAM are. The `DEVICE` block describes a UART: its base address, its registers and the bit that '
  'says the transmitter is ready. `CONSOLE` makes it the default device for `PRINT`.')
P('Compile it:')
CODE('python riscbasic.py hello.bas')
P('The compiler reports the code and RAM used:')
CODE('hello.bas: %d bytes code (of 8192), 0 bytes RAM variables' % c.em.nbytes)
P('and writes these files next to the source:')
T(['File', 'Contents'], [
    ['`hello.bin`', 'Raw binary image, starting at the `CODE` address.'],
    ['`hello.hex`', 'Intel HEX.'],
    ['`hello.mem`', 'One 32-bit word per line in hex, for Verilog/VHDL `$readmemh` block-RAM initialisation.'],
    ['`hello.lst`', 'Listing: each source line, followed by the address, machine code and disassembly generated for it, and a size summary.'],
    ['`hello.elf`', 'ELF32 RISC-V executable with symbols and DWARF line information, for GDB, OpenOCD, XSDB and simulators.'],
], [3.0, 13.4])
H2('Command line')
CODE('python riscbasic.py [options] source.bas')
T(['Option', 'Effect'], [
    ['`-o NAME`, `--output NAME`', 'Base name of the output files (default: the source name). `-o build/x` writes `build/x.bin` and so on.'],
    ['`--no-lst`', 'Do not write the `.lst` listing.'],
    ['`--no-elf`', 'Do not write the `.elf` file.'],
    ['`--no-debug`', 'Write the `.elf` without DWARF line information.'],
    ['`-I DIR`', 'Also search `DIR` for `.include` files (after the directory of the including file). May be given more than once.'],
    ['`-D NAME[=VALUE]`', 'Define `CONST NAME` before the first line, for conditional compilation (default value 1). Example: `-D DEBUG -D BOARD=2`.'],
    ['`-h`, `--help`', 'Show the options.'],
], [4.6, 11.8])
P('Errors and warnings name the file and the line: `lib/uart0.bas:18: error: ...`. An error stops the '
  'compile; no output files are written.')
H2('Running a program')
B('**In an FPGA:** use the `.mem` file to initialise the code memory (for example with `$readmemh`), or '
  'load the `.elf` or `.bin` with your debugger or boot loader.',
  '**In the simulator:** `python rbsim.py hello.bin` runs the image and shows the UART output.',
  '**In a debugger:** load `hello.elf` into GDB or XSDB; see section 17.4.')

# ---- 3 Program structure
H1('Program structure and syntax')
H2('Layout of a program')
P('A RiscBasic program is one source file, plus any files it includes. It usually has this order:')
N('Target and memory declarations: `TARGET`, `CLOCK`, `CODE`, `RAM`, optionally `STACK` and `CPI` (chapter 4).',
  'Includes and conditional compilation where needed (chapter 5).',
  'Device declarations, aliases, constants and variables (chapters 6 and 7).',
  'The program statements. `SUB`, `FUNCTION` and `ON IRQ` blocks may be placed anywhere between '
  'statements; the compiler jumps around them.')
P('`RAM` must be declared before the first statement. Declarations (`CONST`, `DIM`, `DEVICE`, `ALIAS`, '
  '`CONSOLE`, ...) can also appear between statements. The program starts at the first statement; '
  'when it reaches its last line or an `END` statement, it stops in an endless loop.')
H2('Lines, statements and comments')
B('One statement per line, or several separated by `:`, as in `s = 0 : FOR i = 1 TO 4 : s = s + i : NEXT`. '
  'Blocks may open and close on the same line.',
  'A `:` inside `( )` or `[ ]`, as in `R10[7:4]`, and the width in `REG DATA AT +0 : 8` are not separators.',
  'Comments start with `\'` or `REM` and run to the end of the line. `REM` after a `:` comments out the rest of the line.',
  '`name:` at the start of a line defines a label for `GOTO`, unless `name` is a `SUB` or `FUNCTION`.',
  '`LET` is optional: `LET x = 1` is the same as `x = 1`.')
H2('Names')
P('Names start with a letter or `_` and continue with letters, digits and `_`. They are not case sensitive '
  '(`Count` and `COUNT` are the same name). Keywords (Appendix B) cannot be used as names. CPU register '
  'names (`R0`–`R31`, `A0`, `SP`, ...) refer to the registers.')
H2('Numbers and characters')
T(['Form', 'Example', 'Value'], [
    ['Decimal', '`123`, `25_000_000`', '`_` may separate digits'],
    ['Hexadecimal', '`&HFF`, `0xFF`, `&H4000_0000`', '255, 255, 1073741824'],
    ['Binary', '`&B1010`, `0b1010`', '10'],
    ['Kilobytes', '`8K`, `2K`', '8192, 2048 (decimal numbers with a `K` suffix)'],
    ['Character', '`"A"`', 'A one-character string used as a value is its code: 65'],
    ['String', '`"say ""hi"""`', 'Text in double quotes; `""` inside stands for one `"`'],
], [3.0, 5.6, 7.8])
P('All values are 32-bit integers. Constant expressions are evaluated at compile time.')

# ---- 4 Target
H1('Target and memory')
CODE('''TARGET RV32IC          ' RV32I | RV32IC | RV32E | RV32EC, optional +M, +Zbb
CLOCK  25_000_000      ' Hz; required for DELAY_US / DELAY_MS
CODE   AT &H0000 SIZE 8K
RAM    AT &H8000 SIZE 2K
STACK  256             ' bytes at the top of RAM (default 256)
CPI    1               ' cycles per instruction, for delay loops (default 1)''')
H2('TARGET')
T(['Target', 'Instruction set'], [
    ['`RV32I`', 'Base integer instruction set, 32 registers.'],
    ['`RV32IC`', 'RV32I with compressed (16-bit) instructions.'],
    ['`RV32E`', 'Embedded base set: only `R0`–`R15`.'],
    ['`RV32EC`', 'RV32E with compressed instructions.'],
    ['`+M`', 'Hardware multiply and divide (`mul`, `div`, `rem`, ...). Without it, `*`, `/` and `MOD` call small runtime routines.'],
    ['`+Zbb`', 'Bit-manipulation extension: `ROL` and `ROR` use `rol`, `ror` and `rori`.'],
], [3.0, 13.4])
P('Example: `TARGET RV32IC+M+Zbb`. On C targets every instruction that has a 16-bit form is emitted '
  'compressed, and branches and jumps are shortened when their targets are in range (section 17.5).')
H2('CLOCK and CPI')
P('`CLOCK` gives the core clock in Hz. It is required when `DELAY_US` or `DELAY_MS` is used; a missing '
  'clock is a compile error, not a silent timing bug. `CPI` gives the average cycles per instruction of '
  'the delay loop for multi-cycle soft cores (chapter 14).')
H2('CODE, RAM and STACK')
B('`CODE AT addr SIZE n`: where the program is placed and how big the code memory is. A program larger '
  'than `SIZE` is a compile error.',
  '`RAM AT addr SIZE n`: the data memory. Variables are placed from the start of RAM.',
  '`STACK n`: bytes reserved at the top of RAM for the stack (default 256). RAM variables plus `STACK` '
  'must fit in `RAM SIZE`.')
H2('Startup')
P('The compiler adds a short startup sequence at the `CODE` address (symbol `__start`). It:')
N('sets `SP` to the top of RAM and `GP` to RAM base + 2048,',
  'clears all RAM variables to 0,',
  'sets `mtvec` to the interrupt dispatcher if the program has `ON IRQ` blocks,',
  'calls `SUB BoardInit` if the program defines one (section 13.4),',
  'starts the BASIC program at the label `main`.')
P('Because `GP` points 2 KB into RAM, every variable in the first 4 KB of RAM is reached with a single '
  '`lw`/`sw` instruction and a 12-bit offset.')

# ---- 5 Preprocessor
H1('Include files and conditional compilation')
H2('.include')
CODE('''.include "lib/uart0.bas"      ' device declarations, CONSOLE, shared SUBs''')
B('The lines of the named file replace the `.include` line, as if they were typed there. The program is '
  'still one source and one compile.',
  'The directive stands alone on its line. A `\'` comment may follow, a `:` separator may not. `.include` '
  'is not case sensitive.',
  'A relative name is searched first in the directory of the file that contains the `.include`, then in '
  'the `-I` directories in order. An absolute path is used as it is.',
  'Each file is included **once**. A second `.include` of the same file is skipped, so a library can '
  'include what it needs. A file that includes itself, directly or through other files, is an error.',
  'Includes nest. Errors, warnings, the listing and the debugger line table name the included file and '
  'its own line numbers.')
H2('#IF, #ELSEIF, #ELSE, #ENDIF')
CODE('''#IF NOT DEFINED(BOARD)          ' default when the command line has no -D BOARD=...
CONST BOARD = 1
#ENDIF

#IF BOARD = 1
  .include "board1.bas"
#ELSEIF BOARD = 2
  .include "board2.bas"
#ELSE
  #ERROR "BOARD must be 1 or 2"
#ENDIF''')
B('Lines in a branch that is off are not compiled at all. They may contain other definitions of the '
  'same names, or text that is not BASIC. An `.include` in such a branch is not read.',
  'The condition is a constant expression: numbers, `CONST` names defined **above** the `#IF` line (also '
  'in included files), operators, and `DEFINED(name)`, which is 1 if a `CONST` of that name exists. A '
  'condition is true when it is not 0.',
  'An unknown name is an error, so a typing error cannot quietly switch code off. Use `DEFINED(name)` '
  'when a `CONST` may be missing.',
  '`#END IF` is the same as `#ENDIF`. Blocks nest; an `#IF` must be closed in the same file.',
  '`#ERROR "text"` stops the compile with that message when its branch is on.',
  'Each directive stands alone on its line; a `\'` comment may follow.',
  'The listing shows lines of a branch that is off with `(off)`.')
NOTE('**NOT on numbers.** `NOT` is logical on a comparison or on `DEFINED()`, but on a number it inverts '
     'all bits: `NOT 1` is `&HFFFFFFFE`, which is true. Write `#IF DEBUG = 0`, not `#IF NOT DEBUG`.')
H2('Defining constants on the command line')
P('`-D NAME` or `-D NAME=value` defines `CONST NAME` before the first line (default value 1). One '
  'source can then build several variants:')
CODE('''python riscbasic.py app.bas -D DEBUG -D BOARD=2''')

# ---- 6 Objects
H1('Objects: registers, variables and constants')
P('Everything that can appear on either side of `=` is an **object** with a kind, a width and an access '
  'method.')
T(['Kind', 'Example', 'Notes'], [
    ['CPU register', '`R10`, `A0`, `SP`', '`R0` / `ZERO` is read-only'],
    ['Register bit', '`R10.0` or `R10[0]`', 'a single bit'],
    ['Register field', '`R10[7:4]`', 'bits 7 down to 4, right-aligned when read'],
    ['Variable', '`count`', 'in RAM, or pinned to a register'],
    ['Array element', '`buf(i)`', 'element of a RAM array'],
    ['Memory', '`MEM[addr]`, `MEM8[addr]`, `MEM16[addr]`', 'any 32-bit address (chapter 8)'],
    ['Device register', '`UART0.DATA`', 'memory-mapped (chapter 7)'],
    ['Device bit or field', '`GPIO.OUT.3`, `TIMER.CTRL[2:0]`', 'read-modify-write, or set/clear registers if declared'],
    ['Alias', '`LED`', 'a name for any object above'],
    ['Constant', '`CONST BAUD = 115200`', 'compile time only'],
], [3.6, 5.6, 7.2])
P('Bits and fields can be read and assigned on registers, variables, array elements, device registers '
  'and memory: `count.0 = 1`, `buf(i)[7:4] = 5`, `MEM[p].31 = 0`. An assignment to a bit or field of a '
  'variable, array element or memory word is a read-modify-write.')
H2('CPU registers')
P('Numeric and ABI names are accepted and fully interchangeable.')
T(['Numeric', 'ABI', 'Numeric', 'ABI'], [
    ['`R0`', '`ZERO`', '`R8`', '`S0` / `FP`'],
    ['`R1`', '`RA`', '`R9`', '`S1`'],
    ['`R2`', '`SP`', '`R10`–`R17`', '`A0`–`A7`'],
    ['`R3`', '`GP`', '`R18`–`R27`', '`S2`–`S11`'],
    ['`R4`', '`TP`', '`R28`–`R31`', '`T3`–`T6`'],
    ['`R5`–`R7`', '`T0`–`T2`', '', ''],
], [3.0, 5.2, 3.0, 5.2])
P('Registers the compiler reserves:')
T(['Register', 'Use', 'In user code'], [
    ['`S0`, `S1`, `A5` (`R8`, `R9`, `R15`)', 'Compiler temporaries. They are in x8–x15, so the compiler\'s own code can use 16-bit instructions.', 'Error'],
    ['`GP` (`R3`)', 'Points into RAM for one-instruction variable access.', 'Writing it is an error'],
    ['`SP` (`R2`)', 'Stack pointer.', 'Writing it gives a warning'],
    ['`RA` (`R1`)', 'Return address; overwritten by calls, `PRINT` and runtime routines.', 'Warning if the program also makes calls'],
    ['`R0` (`ZERO`)', 'Always 0.', 'Read-only'],
    ['`TP` (`R4`)', 'Return address of the interrupt return (section 13.3).', 'Error in programs with `ON IRQ`'],
], [4.6, 7.6, 4.2])
P('`T0`–`T2`, `A0`–`A4`, `A6`, `A7`, `S2`–`S11`, `T3`–`T6`, and `TP` in programs without `ON IRQ`, are free for user variables. On '
  '`RV32E` targets only `R0`–`R15` exist.')
H2('Variables: DIM')
CODE('''DIM count AS WORD IN R10    ' pinned to a register
DIM flags AS BYTE           ' in RAM
DIM temp  AS SHALF          ' signed 16-bit, in RAM
DIM buf(16) AS BYTE         ' array of 16 bytes in RAM: buf(0) to buf(15)
DIM name AS STRING * 20     ' text buffer (chapter 11)''')
T(['Type', 'Width', 'Range', 'Notes'], [
    ['`BIT`', '1 bit', '0, 1', 'one byte in RAM'],
    ['`BYTE`', '8', '0 to 255', ''],
    ['`SBYTE`', '8', '−128 to 127', 'signed'],
    ['`HALF`', '16', '0 to 65535', ''],
    ['`SHALF`', '16', '−32768 to 32767', 'signed'],
    ['`WORD`', '32', '0 to 4294967295', 'default type of parameters and functions'],
    ['`SWORD`', '32', '−2147483648 to 2147483647', 'signed'],
    ['`STRING * n`', 'n bytes', 'text', 'same as `DIM name(n) AS BYTE`, marked as text'],
], [3.0, 2.0, 5.4, 6.0])
B('**RAM variables always live in RAM.** Every read is a load and every write is a store, so the '
  'listing is predictable: `count = count + 1` is load, add, store.',
  '**Register variables** (`IN Rn`) always live in their register. They are the fastest form and the '
  'right choice for loop counters and hot values.',
  'Arrays are in RAM. Elements are numbered from 0; `DIM buf(16)` has `buf(0)` to `buf(15)`. The index '
  'is not checked at run time.',
  'All RAM variables start at 0 (startup clears RAM).',
  'A `DIM` inside a `SUB` or `FUNCTION` creates a variable that only that routine can see. It is a '
  'static RAM variable, not a stack slot (see Appendix C on recursion).')
H2('Constants: CONST')
CODE('''CONST BAUD = 115200
CONST CR = 13
CONST LF = 10
CONST MASK = (1 SHL 5) OR 1      ' constant expressions are evaluated at compile time''')
P('A constant has no storage; it is replaced by its value. A constant may not have the name of a '
  '`SUB` or `FUNCTION`.')
H2('Aliases: ALIAS')
CODE('''ALIAS LED     = GPIO.OUT.3
ALIAS BUTTON  = GPIO.IN.0
ALIAS RESET_N = NOT GPIO.OUT.7   ' NOT makes an active-low alias (section 7.3)''')
P('An alias is a second name for an object. It costs nothing at run time: `LED = BUTTON` compiles '
  'exactly as `GPIO.OUT.3 = GPIO.IN.0`.')

# ---- 7 Devices
H1('Devices')
P('Memory-mapped peripherals are described once with a `DEVICE` block. The program then uses register '
  'and bit names instead of addresses and masks.')
CODE('''DEVICE GPIO AT &H4000_0000
  REG OUT  AT +0
  REG IN   AT +4  READONLY
  REG SET  AT +8  SETS OUT      ' write 1 = set bit in OUT
  REG CLR  AT +12 CLEARS OUT    ' write 1 = clear bit in OUT
END DEVICE

DEVICE UART0 AT &H4000_1000
  REG DATA   AT +0 : 8
  REG STATUS AT +4 READONLY
  REG RXDATA AT +8 : 8 READONLY
  BIT TXREADY = STATUS.0
  BIT RXREADY = STATUS.1
END DEVICE''')
H2('Registers, bits and fields')
T(['Declaration', 'Meaning'], [
    ['`REG name AT +offset`', 'A register at base address + offset. 32 bits wide by default.'],
    ['`... : 8`, `... : 16`', 'Register width. Selects `sb`/`sh`/`sw` and `lbu`/`lhu`/`lw`.'],
    ['`READONLY`', 'The register cannot be written; an assignment to it is an error.'],
    ['`WRITEONLY`', 'The register cannot be read back; the compiler keeps a RAM copy (section 7.4).'],
    ['`SETS reg`', 'Writing 1 to a bit of this register sets that bit in `reg`.'],
    ['`CLEARS reg`', 'Writing 1 to a bit of this register clears that bit in `reg`.'],
    ['`BIT name = REG.n`', 'A name for one bit.'],
    ['`FIELD name = REG[hi:lo]`', 'A name for a group of bits.'],
    ['`NOT` before `REG.n` or `REG[hi:lo]`', 'Active-low bit or field (section 7.3).'],
], [5.6, 10.8])
P('In the program, write `DEVICE.REG`, `DEVICE.BIT` or `DEVICE.REG.n`:')
CODE('''UART0.DATA = "A"             ' store one byte
WAIT UNTIL UART0.TXREADY     ' poll a status bit
GPIO.OUT.3 = 1               ' one store to SET, because SETS OUT is declared
GPIO.OUT[11:8] = 5           ' read-modify-write of a field
x = GPIO.IN AND &HFF''')
H2('Set and clear registers')
P('Many GPIO blocks have separate set and clear registers. When they are declared with `SETS` and '
  '`CLEARS`, single-bit assignments become **one store** instead of a read-modify-write, which is also '
  'safe against interrupts changing other bits of the register:')
T(['Statement', 'Generated code (RV32I)'], [
    ['`GPIO.OUT.3 = 1` (SET declared)', '`lui a5, 0x40000` / `li s0, 8` / `sw s0, 8(a5)`'],
    ['`GPIO.OUT.3 = 1` (no SET)', '`lw` / `ori` / `sw` (read-modify-write)'],
    ['`LED = BUTTON`', 'read the bit from `IN`, then store to `SET` or `CLR` depending on its value'],
], [6.0, 10.4])
H2('Active-low bits and fields (NOT)')
P('`NOT` in a `BIT`, `FIELD` or `ALIAS` declaration makes it active-low. The program always works with '
  'the logical value; the compiler inverts on every read and write.')
CODE('''DEVICE PORT AT &H4000_0000
  REG OUT AT +0
  REG SET AT +8  SETS OUT
  REG CLR AT +12 CLEARS OUT
  BIT  LED_N  = NOT OUT.5        ' the LED lights when the pin is low
  FIELD SEL_N = NOT OUT[11:8]
END DEVICE

PORT.LED_N = 1                   ' writes 0 to the pin: one store to CLR''')
B('Reading `PORT.LED_N` gives 1 when the hardware bit is 0. A field reads as its bitwise inverse within '
  'the field width.',
  'With `SETS`/`CLEARS` declared, the compiler swaps the two registers, so it is still one store.',
  '`IF`, `WHILE`, `WAIT UNTIL` and `LOOP UNTIL` test the hardware bit the other way round at no extra cost.',
  'A device whose `TXREADY` is declared with `NOT` makes `PRINT` wait while the hardware bit is 1.',
  '`NOT` twice cancels: `ALIAS X = NOT PORT.LED_N` is the raw pin. A whole register cannot be inverted.')
H2('Write-only registers (WRITEONLY)')
P('Some output registers cannot be read back: a read returns 0, garbage or the input pins. `WRITEONLY` '
  'makes the compiler keep a copy of the register in RAM, the *shadow*:')
CODE('''DEVICE LEDS AT &H4000_2000
  REG OUT AT +0 WRITEONLY
  BIT LED = OUT.3
END DEVICE

LEDS.LED = 1              ' set bit 3 in the shadow, then write the whole shadow to OUT
PRINT HEX LEDS.OUT        ' reads the shadow, not the hardware''')
B('Every write goes to the shadow and to the register. Every read, including the read-modify-write '
  'behind bit and field assignments, uses the shadow.',
  'Writes through a `SETS` or `CLEARS` register also update the shadow.',
  'The shadow is a RAM variable created only when the register is used. It starts at 0; if the '
  'hardware resets to another value, write the whole register once at the start.',
  'It appears in the ELF symbol table as `DEVICE.REG.shadow`. `MEM[addr]` access does not use it.',
  '`READONLY` and `WRITEONLY` together are an error.')

# ---- 8 MEM
H1('Memory access: MEM')
CODE('''MEM[&H4400_0000] = &H1234_5678     ' write a 32-bit word anywhere in the 4 GB space
v = MEM[&HFFFF_FFFC]               ' read the top word
MEM[base + 4 * i] = v              ' the address can be any expression
MEM8[p] = 13 : x = MEM16[p + 2]    ' byte and halfword access
MEM[p][15:8] = &HAB                ' a field of a memory word (read-modify-write)
IF MEM[p].31 THEN PRINT "set"      ' a bit''')
B('`MEM[addr]` is a 32-bit word. `MEM8`, `MEM16` and `MEM32` give the width explicitly. Reads of '
  '`MEM8` and `MEM16` are zero-extended.',
  'The address is a full 32-bit value: a constant or any expression. Parentheses also work: '
  '`MEM32(addr)` is the same as `MEM32[addr]`.',
  'The first `[ ]` after `MEM` is the address; a second `[hi:lo]` or `.n` selects bits.',
  'The address is not checked for alignment. A misaligned `MEM16` or `MEM` access does whatever the core '
  'does with it (trap or split access).',
  '`MEM`, `MEM8`, `MEM16` and `MEM32` are reserved words.')
H2('Cost of an access')
T(['Address', 'Code'], [
    ['Constant, in the first 4 KB of RAM', 'one `lw`/`sw` relative to `GP`'],
    ['Constant, within 2 KB of address 0 or in the top 2 KB (`&HFFFF_F800`–`&HFFFF_FFFF`)', 'one `lw`/`sw` relative to `zero`'],
    ['Any other constant', '`lui a5, hi` + `lw`/`sw` with the low 12 bits as offset'],
    ['Computed', 'the address expression is evaluated into a register first'],
], [8.4, 8.0])
P('Back-to-back accesses with the same upper 20 address bits share one `lui`. This applies to device '
  'registers too. A label, a call or any other change of `A5` ends the sharing:')
CODE('''; MEM[&H4400_0000] = 1
    c.li    s0, 1
    lui     a5, 0x44000
    c.sw    s0, 0(a5)
; MEM[&H4400_0004] = 2
    c.li    s0, 2
    c.sw    s0, 4(a5)        ; no second lui''')

# ---- 9 Assignment and expressions
H1('Assignment and expressions')
H2('The assignment rule')
P('`DEST = SOURCE` is compiled by looking at the pair (destination kind, source kind). The compiler '
  'chooses the shortest correct sequence:')
T(['Statement', 'Generated code (RV32I)'], [
    ['`R1.0 = 1`', '`ori x1, x1, 1`'],
    ['`R1.0 = 0`', '`andi x1, x1, -2`'],
    ['`R1.12 = 1`', '`lui a5, 1` / `or x1, x1, a5` (the mask does not fit in 12 bits)'],
    ['`R10 = R11.3`', '`srli x10, x11, 3` / `andi x10, x10, 1`'],
    ['`R10[7:4] = 5`', '`andi x10, x10, -241` / `ori x10, x10, 0x50`'],
    ['`count = count + 1` (RAM)', '`lw s0, ofs(gp)` / `addi s0, s0, 1` / `sw s0, ofs(gp)`'],
    ['`UART0.DATA = "A"`', '`li s0, 65` / `sb s0, 0(a5)`'],
    ['`LED = count > 10`', 'the comparison result (0 or 1) is assigned as a bit'],
], [5.4, 11.0])
B('**Width:** a wider source is truncated to the destination width; a narrower source is zero-extended, '
  'or sign-extended for signed types. Truncating a constant gives a warning.',
  '**Bits:** any source assigned to a bit is true if it is not zero.',
  '**Fields:** the value is masked to the field width and shifted into place.',
  '**Read-only destinations** (`R0`, `READONLY` registers, constants, `STRING` parameters) are errors.')
H2('ATOMIC')
CODE('''ATOMIC GPIO.OUT[3:0] = mode''')
P('`ATOMIC` before an assignment disables interrupts (`mstatus.MIE`) around it and restores the previous '
  'state afterwards. Use it for read-modify-write of registers that an interrupt handler also changes.')
H2('Operators')
P('From highest to lowest precedence:')
T(['Precedence', 'Operators', 'Meaning'], [
    ['1', '`-x`, `+x`', 'negation'],
    ['2', '`*`, `/`, `MOD`', 'multiply, divide, remainder'],
    ['3', '`+`, `-`', 'add, subtract'],
    ['4', '`SHL`, `SHR`, `SAR`', 'shift left, shift right logical, shift right arithmetic'],
    ['5', '`=`, `<>`, `<`, `>`, `<=`, `>=`', 'comparison; result 0 or 1'],
    ['6', '`NOT`', 'logical NOT of a comparison, otherwise bitwise inversion'],
    ['7', '`AND`', 'bitwise AND (logical on comparisons)'],
    ['8', '`OR`, `XOR`', 'bitwise OR and XOR'],
], [2.4, 6.0, 8.0])
B('Use parentheses to group: `(a + b) * c`.',
  'Comparisons, `/` and `MOD` are **unsigned** unless one operand is signed (`SBYTE`, `SHALF`, '
  '`SWORD`); then they are signed: `-7 / 2` is `-3`, `-7 MOD 2` is `-1`.',
  '`*`, `/` and `MOD` use M-extension instructions with `+M`. Otherwise the compiler includes a small '
  'runtime routine the first time one is used.',
  'Constant expressions are folded at compile time, so `CONST`s and literal arithmetic cost nothing.',
  'A one-character string is a number: `IF c = "A" THEN` compares `c` with 65.')

# ---- 10 Shift and rotate
H1('Shift and rotate commands')
P('Besides the `SHL`, `SHR` and `SAR` operators, four commands shift or rotate a register or RAM '
  'variable in place:')
CODE('''SHL dest [, count]     ' shift left, zeros in
SHR dest [, count]     ' shift right, zeros in (logical)
ROL dest [, count]     ' rotate left
ROR dest [, count]     ' rotate right''')
B('`dest` is a CPU register, a register variable or a RAM variable. For device registers, use an '
  'assignment instead.',
  '`count` defaults to 1. It is a constant, a CPU register or a register variable. Variable counts are '
  'allowed only for 32-bit destinations.',
  'The operation works on the **width of the destination**: a `BYTE` rotates within 8 bits, a `HALF` '
  'within 16.',
  'RV32I has no rotate instructions, so `ROL`/`ROR` are built from two shifts and an `or`. With `+Zbb` '
  'the compiler uses `rol`, `ror` and `rori`.')
T(['Command', 'Generated code (RV32I)'], [
    ['`SHL R10, 3`', '`slli x10, x10, 3`'],
    ['`SHR R10, R11`', '`srl x10, x10, x11`'],
    ['`SHL count, 2` (RAM `WORD`)', '`lw s0, ofs(gp)` / `slli s0, s0, 2` / `sw s0, ofs(gp)`'],
    ['`ROL R10, 4`', '`slli s0, x10, 4` / `srli x10, x10, 28` / `or x10, x10, s0`'],
    ['`ROL R10, 4` (`+Zbb`)', '`rori x10, x10, 28`'],
    ['`ROR flags` (RAM `BYTE`)', '`lbu s0, ofs(gp)` / `srli s1, s0, 1` / `slli s0, s0, 7` / `or s0, s0, s1` / `sb s0, ofs(gp)`'],
], [5.4, 11.0])

# ---- 11 Strings
H1('Strings')
P('RiscBasic has no heap. Every string is a fixed-size, zero-terminated `BYTE` buffer.')
CODE('''DIM name AS STRING * 20               ' 20 bytes: up to 19 characters and the 0

FUNCTION Hex8(v AS WORD) AS STRING * 12
  RETURN "0x" + HEX$(v)
END FUNCTION

FUNCTION Greet(who AS STRING) AS STRING * 32
  RETURN "Hello, " + who + "!"
END FUNCTION

name = "item " + DEC$(n) + " at " + Hex8(addr) + CHR$(33)
PRINT Greet(name), 13, 10''')
H2('Buffers and string expressions')
B('`DIM name AS STRING * n` is the same as `DIM name(n) AS BYTE`, but marks the array as text. Any '
  '`BYTE` array can be used as a string.',
  'A **string expression** joins terms with `+`. A term is `"text"`, a `STRING` or `BYTE` array, a string '
  '`FUNCTION` call, a `STRING` parameter, `CHR$(x)`, `DEC$(x)`, `HEX$(x)`, or a substring '
  '(`LEFT$`, `RIGHT$`, `MID$`).',
  '`buffer = string expression` builds the text in the buffer. It is cut at the buffer size and always '
  'zero-terminated. `s = "x" + s` works: when the buffer itself is read during the build, the text is '
  'built in a scratch buffer and then copied.',
  'Strings cannot be used as numbers, and numbers cannot be assigned to strings: use `DEC$`, `HEX$`, '
  '`CHR$` and `VAL`.')
H2('String functions')
T(['Function', 'Result'], [
    ['`CHR$(x)`', 'one character with code `x`'],
    ['`DEC$(x)`', '`x` in decimal, as `DEC` in `PRINT` (signed types with `-`)'],
    ['`HEX$(x)`', '`x` in hex digits, as `HEX` in `PRINT`'],
    ['`LEFT$(s, n)`', 'the first `n` characters'],
    ['`RIGHT$(s, n)`', 'the last `n` characters'],
    ['`MID$(s, start [, n])`', '`n` characters from position `start` (from 1); without `n`, to the end'],
    ['`LEN(s)`', 'number of characters (a number)'],
    ['`INSTR([start,] s, find)`', 'position of `find` in `s` from 1; 0 if not found (a number)'],
    ['`VAL(s)`', 's converted to a signed number (a number)'],
], [5.0, 11.4])
B('Out-of-range positions give shorter or empty text, never an error: `MID$(s, 0)` and a `start` past the '
  'end are empty.',
  'The source of `LEFT$`, `RIGHT$`, `MID$`, `INSTR` and `VAL` is a single term; nest them through a '
  'variable. `PRINT` and `LEN` use a substring directly.',
  '`LEN` of joined strings adds the lengths of the terms without building them. `LEN(DEC$(x))` is not '
  'supported.',
  '`VAL` skips leading spaces, accepts `+` or `-`, decimal digits, or hex/binary digits after `&H`, '
  '`0x`, `&B`, `0b`. The first character that is not a digit ends the number: `VAL("42abc")` is 42. '
  'No digits gives 0.',
  'With constant text, `LEN`, `INSTR`, `VAL` and the substring functions are computed at compile time.')
H2('Comparing strings')
CODE('''IF name = "Bob" THEN PRINT "hi Bob", 13, 10
IF LEFT$(cmd, 3) = "SET" THEN v = VAL(MID$(cmd, 5))''')
B('`=`, `<>`, `<`, `>`, `<=`, `>=` compare byte by byte, unsigned. A shorter prefix is less: `"ab" < "abc"`.',
  'Each side is one term. To compare a joined string, assign it to a buffer first.',
  'Only one side can be a substring, and both sides cannot call the same string `FUNCTION` (it has one '
  'result buffer).')
H2('String functions and parameters')
B('`FUNCTION f(...) AS STRING * n` returns text in its own result buffer of `n` bytes. `RETURN '
  'expression` sets it. Ending without `RETURN`, or with `EXIT FUNCTION`, returns empty text.',
  'Each call overwrites the previous result. Using results one after another (`Pad(7) + "/" + Pad(42)`) '
  'and nesting (`Greet(Greet("x"))`) work; a recursive string `FUNCTION` does not.',
  'A `STRING` parameter (`who AS STRING`) passes a pointer to existing text: a literal, a buffer, a string '
  'function result or another `STRING` parameter. It is read-only. The argument is a single term.',
  'Do not build strings in an interrupt handler while the main program may be building one: the build '
  'state is shared.')

# ---- 12 Control flow
H1('Control flow')
H2('IF')
CODE('''IF temp > 80 THEN
  FAN = 1
ELSEIF temp < 60 THEN
  FAN = 0
ELSE
  PRINT "hold", 13, 10
END IF

IF ok THEN PRINT "a" : PRINT "b" ELSE PRINT "c"     ' single-line form''')
B('The block form ends with `END IF`.',
  'The single-line form takes all statements after `THEN` up to its `ELSE`, and all statements after the '
  '`ELSE`. An `END IF` on such a line is an error.',
  'An `ELSE` belongs to the nearest `IF` that has no `ELSE` yet: in `IF a THEN IF b THEN x = 1 ELSE x = 2`, '
  'the `ELSE` runs when `a` is true and `b` is false.',
  'A condition is true when it is not 0. Bit objects (`IF UART0.RXREADY THEN`) are tested with a single '
  'branch.')
H2('Loops')
CODE('''WHILE cond ... WEND
DO ... LOOP                    ' endless
DO ... LOOP UNTIL cond
REPEAT ... UNTIL cond          ' body runs at least once
FOR i = 0 TO 15 [STEP 1] ... NEXT''')
B('`REPEAT ... UNTIL cond` is the same as `DO ... LOOP UNTIL cond`. `UNTIL` on its own line closes the '
  'innermost `REPEAT`.',
  '`EXIT FOR`, `EXIT DO`, `EXIT WHILE` and `EXIT REPEAT` leave the innermost loop of that kind.')
H3('FOR loop counters')
CODE('''FOR i = 0 TO 15 ... NEXT          ' RAM variable: load / add / store each pass
FOR ticks = 0 TO 99 ... NEXT      ' register variable (DIM ticks AS WORD IN A0)
FOR R12 = 0 TO 15 ... NEXT        ' CPU register, by number
FOR S2 = 10 TO 0 STEP -1 ... NEXT ' CPU register, by ABI name''')
B('Register counters are the fastest form: the loop is `addi` plus one compare-and-branch. With '
  'constant bounds, the end value is used as an immediate where it fits.',
  '`STEP` may be negative. The counter is compared signed.',
  '`S0`, `S1`, `A5`, `GP` and `R0` cannot be loop counters.')
H2('SELECT CASE')
CODE('''SELECT CASE key
  CASE "a" TO "z"
    PRINT "lower", 13, 10
  CASE 1, 2, 10 TO 12
    PRINT "control", 13, 10
  CASE IS > 127
    PRINT "high", 13, 10
  CASE ELSE
    PRINT "other", 13, 10
END SELECT''')
B('`CASE` takes constants and constant ranges, separated by commas. A range includes both ends; '
  '`CASE 5 TO 1` (low end last) is an error.',
  '`CASE IS op value` compares with `=`, `<>`, `<`, `>`, `<=` or `>=`.',
  'Comparisons are signed if the `SELECT` expression is signed, otherwise unsigned.',
  'The first matching `CASE` runs; there is no fall-through.')
H2('GOTO, labels and END')
CODE('''retry:
  n = n + 1
  IF n < 3 THEN GOTO retry
END                            ' stop: endless loop at __end''')
P('A label is a name followed by `:` at the start of a line. `END` on its own stops the program; '
  'reaching the last line has the same effect.')

# ---- 13 Subroutines
H1('Subroutines, functions and interrupts')
H2('SUB and FUNCTION')
CODE('''SUB Send(ch AS BYTE)            ' parameters in A0-A4, A6, A7
  WAIT UNTIL UART0.TXREADY
  UART0.DATA = ch
END SUB

FUNCTION Add(a AS WORD, b AS WORD) AS WORD
  RETURN a + b
END FUNCTION

Send 65                         ' call forms: Send 65, Send(65), CALL Send(65)
total = Add(total, 5)''')
B('Parameters have a type (`AS WORD` is the default). A `FUNCTION` has a return type (default `WORD`) '
  'or `AS STRING * n` (chapter 11).',
  '`RETURN value` leaves a `FUNCTION` with a value. `EXIT SUB` and `EXIT FUNCTION` leave early.',
  'A `SUB` or `FUNCTION` may be defined before or after its first call, and anywhere in the program; '
  'the main program jumps around it.',
  'Variables declared with `DIM` inside the routine are local to it (static RAM).')
H2('Calling convention')
B('Arguments go in `A0`–`A4`, `A6`, `A7` (up to 7; on RV32E `A0`–`A4`, up to 5). `A5` is skipped '
  'because it is a compiler temporary. `A0` holds the return value.',
  '**Register variables are global.** A `SUB` or `FUNCTION` sees and may change every register '
  'variable, and the callee saves only `RA`. This differs from the standard RISC-V ABI.',
  'A global register variable that sits in an argument register is saved by the caller around each '
  'call that uses that register. Inside a `SUB`, it is hidden by the parameter in the same register; '
  'using it there is an error.',
  'Recursion is allowed, with the limits in Appendix C.')
H2('Interrupts: ON IRQ')
IRQ = HEADER_BAS + '''
DIM ticks AS WORD

ON IRQ MTIMER                    ' machine timer interrupt (mcause 7)
  ticks = ticks + 1
  ' acknowledge / re-arm the timer here (core specific)
END IRQ

ASM
  li     t0, 128
  csrrs  zero, mie, t0           ' enable the machine timer interrupt
  csrrsi zero, mstatus, 8        ' global interrupt enable
END ASM

DO
LOOP
'''
CODE(IRQ)
compile_check(IRQ, 'irq')
B('`ON IRQ n` takes an interrupt number (0–2047) or one of the names `MSOFT` (3), `MTIMER` (7) and '
  '`MEXT` (11). For other names, define a `CONST`.',
  'The compiler builds one dispatcher at `mtvec`: it saves the caller-saved registers, reads `mcause` '
  'and calls the matching `ON IRQ` block.',
  'Interrupts are not enabled by the compiler. Enable them with inline assembly as above.',
  'The dispatcher returns without `mret`: it restores the registers, loads the return address into '
  '`TP`, sets `mstatus.MIE` and jumps. Some cores execute `mret` as a no-op; the AMD MicroBlaze V '
  'MCS does. `TP` is therefore reserved in programs with `ON IRQ`.',
  'Use `ATOMIC` for read-modify-write of data that a handler also changes.')
H2('Board initialisation: SUB BoardInit')
CODE('''SUB BoardInit
  GPIO.OUT = 0                   ' known output state
  PLL.CTRL = PLL_CONFIG
END SUB''')
P('If the program has a `SUB` named `BoardInit`, the startup code calls it once, after `SP`, `GP` and '
  'RAM clearing are set up and before the `main` label, where the BASIC program starts. A debugger that '
  'stops at `main` therefore sees the board already initialised.')
B('`BoardInit` may be defined anywhere: after the main program, in an included file, or inside an `#IF` '
  'branch, so each board file can bring its own.',
  'It must be a `SUB` without parameters.',
  'Interrupts are not yet enabled when it runs.',
  'A program that also calls it with `CALL` runs it twice.')

# ---- 14 Timing
H1('Timing and waiting')
CODE('''WAIT UNTIL UART0.TXREADY        ' poll until the condition is true
DELAY_US 10                     ' busy-wait 10 microseconds
DELAY_MS 100                    ' busy-wait 100 milliseconds''')
B('`WAIT UNTIL cond` repeats the test until it is true. A bit test is one load and one branch.',
  '`DELAY_US` and `DELAY_MS` are busy-wait loops calculated from `CLOCK`. They require `CLOCK`.',
  'The loop is calculated as 2 instructions per pass, times `CPI`. On a multi-cycle soft core, set `CPI` '
  'to its average cycles per instruction and check the timing on real hardware.',
  'Interrupt handlers that run during a delay make it longer.')

# ---- 15 Console I/O
H1('Console input and output')
H2('CONSOLE')
P('`CONSOLE dev` names the device used by `PRINT`, `INPUT` and `LINE INPUT` without an explicit device. '
  'It applies to the statements after it and may be given again to switch devices.')
P('A device used for output must declare a register `DATA` and a bit `TXREADY`. A device used for input '
  'must declare a bit `RXREADY` and a register `RXDATA` (or receive through `DATA`). `NOT` is allowed on '
  'both bits.')
H2('PRINT')
CODE('''CONST CR = 13
CONST LF = 10
CONSOLE UART0

PRINT "count = ", DEC count, CR, LF
PRINT "addr ", HEX addr, " key ", CHR key, CR, LF
PRINT UART1, "to the other UART", CR, LF
PRINT "Hello, ", name, CR, LF        ' BYTE array: printed as text''')
T(['Item', 'Output'], [
    ['`"text"`', 'the text'],
    ['number or `CONST` (0–255)', 'that one byte, for example `13, 10` for a line end'],
    ['`CHR expr`', 'the low 8 bits of `expr` as one character'],
    ['`HEX expr`', 'hex digits, uppercase, no prefix; `BYTE` 2 digits, `HALF` 4, `WORD` and registers 8, with leading zeros'],
    ['`DEC expr`', 'decimal without leading zeros; signed types print `-` when negative'],
    ['`BYTE` array name', 'the array as zero-terminated text, never past its end'],
    ['string expression', 'any string expression from chapter 11'],
], [5.0, 11.4])
B('Items are separated by `,` (or `;`). **No line end is added**: write `13, 10` where a line ends.',
  'A device name directly after `PRINT`, followed by `,` or the end of the line, selects the device.',
  'A variable on its own is an error: write `CHR`, `DEC` or `HEX`.',
  'Adjacent text and character constants are merged at compile time: `"done", 13, 10` is stored once as '
  'one string.',
  '`DEC` uses a table of powers of ten, so it needs no multiply or divide and works on plain RV32I.')
H2('INPUT')
CODE('''INPUT "Value? ", DEC v          ' prompt, then a decimal number
INPUT CHR c                     ' wait for one character (no echo)
INPUT UART1, HEX addr           ' explicit device, hexadecimal number
IF UART0.RXREADY THEN INPUT CHR c   ' only if a character is waiting''')
B('Items: `"prompt"`, `CHR target`, `DEC target`, `HEX target`. The target is anything assignable.',
  '`DEC` reads an optional `-` and decimal digits; `HEX` reads `0`–`9`, `A`–`F`, `a`–`f`. Both echo the '
  'characters they accept. The first other character ends the number and is not echoed. No digits gives 0.',
  '`INPUT` waits for a character. To check without waiting, test `RXREADY`.')
H2('LINE INPUT')
CODE('''DIM name(20) AS BYTE
DIM n AS WORD
LINE INPUT "Name? ", name, n     ' text into name(), length into n
PRINT "Hello, ", name, 13, 10''')
B('Form: `LINE INPUT [dev,] ["prompt",] buffer [, length]`. `buffer` is a `BYTE` array or `STRING`.',
  'The text is zero-terminated: a buffer of N bytes holds up to N−1 characters.',
  'Enter (CR) ends the line and is not stored. LF is ignored, so terminals that send CR LF work.',
  'Backspace (8) and DEL (127) remove the last character and erase it on the terminal. Other control '
  'characters are ignored. When the buffer is full, further characters are ignored until Enter.')

# ---- 16 Inline assembly
H1('Inline assembly')
CODE('''ASM
  csrrsi zero, mstatus, 8        ' global interrupt enable
  li     t0, 128
  csrrs  zero, mie, t0
END ASM''')
B('`ASM` ... `END ASM` contains RISC-V instructions, one per line, with ABI or numeric register names.',
  'Variables and aliases can be used by name inside `ASM` blocks.',
  'The assembler keeps exactly the instructions written; the compiler does not optimise them.',
  'Do not change `GP`, `SP` or the compiler temporaries `S0`, `S1`, `A5` in a way the surrounding code '
  'does not expect.')

# ---- 17 Compiler output
H1('Compiler output and debugging')
H2('Output files')
T(['File', 'Contents'], [
    ['`.bin`', 'Raw image from the `CODE` address.'],
    ['`.hex`', 'Intel HEX.'],
    ['`.mem`', 'One 32-bit word per line, for `$readmemh`.'],
    ['`.lst`', 'Listing (section 17.2). Written by default; `--no-lst` turns it off.'],
    ['`.elf`', 'ELF32 RISC-V executable (section 17.3). `--no-elf` turns it off.'],
], [2.6, 13.8])
P('The code must fit in `CODE SIZE`, otherwise the compile fails. The size of every `SUB`, `FUNCTION` '
  'and runtime routine is reported in the listing.')
H2('The listing file')
B('The file starts with the license header and the target summary.',
  'Each source line appears as a comment with its line number, directly above the instructions generated '
  'for it. Lines from an included file carry the file name: `; lib/uart0.bas:0018  PRINT CR, LF`.',
  'Each instruction line shows the address, the machine code in hex (4 digits for compressed '
  'instructions) and the disassembly.',
  'Startup code, runtime routines and string constants are listed under their own headings.',
  'A summary at the end gives code size per routine, total code used, and RAM used.')
CODE('''; ---- program ----
                  main:
; 0048  MEM[&H4400_0000] = 1
00000024      4405   c.li    s0, 1
00000026  440007B7   lui     a5, 0x44000
0000002A      C380   c.sw    s0, 0(a5)''')
H2('ELF and DWARF')
P('The `.elf` file loads directly into GDB, OpenOCD, Spike, Renode or a simulator testbench, and '
  '`objdump -d` disassembles it with all names. It contains:')
B('one loadable segment with code and constant data at the `CODE` address, entry point `__start`;',
  '`.bss` describing the RAM variables (cleared by the startup code);',
  'a symbol table: `__start`, `main`, every `SUB`, `FUNCTION` and runtime routine with its size, IRQ '
  'handlers, labels, strings, and RAM variables with their sizes (`SUB.local` for locals);',
  'DWARF 3 line information that maps every instruction to its `.bas` line, including included files;',
  'the ISA string in `.riscv.attributes`, for example `rv32i2p1_m2p0_c2p0_zicsr2p0`.')
H2('Source-level debugging')
P('With the DWARF information, debuggers show the `.bas` file and step one BASIC line at a time. Keep the '
  '`.bas` files where they were compiled, or tell the debugger where they are.')
P('**GDB** (for example `riscv-none-elf-gdb program.elf`, connected to the target):')
CODE('''break main
break program.bas:34
break lib/uart0.bas:18
next
list
x/wd &PRESSES''')
P('**XSDB** (AMD Vitis), after `connect`, `target` and `dow program.elf`:')
CODE('''bpadd -file program.bas -line 34
con
nxt''')
P('Variable names are upper case in the ELF. Variables are available as addresses (`&PRESSES`); the '
  'DWARF information covers source lines, not types.')
H2('Compressed instructions and code size')
P('On `RV32IC` and `RV32EC`, every instruction with a 16-bit form is emitted compressed. Code is '
  'generated in one pass; at the end, branches and jumps are shortened to 16-bit forms when their targets '
  'are in range, and conditional branches beyond ±4 KB become an inverted branch over a `jal`.')
P('Compiler temporaries are `s0`, `s1` and `a5`, all in x8–x15, so the compiler\'s own code can use the '
  '16-bit forms. Runtime routines are called with `c.jal` where possible.')

# ---- 18 Example
H1('Complete example')
EXAMPLE = HEADER_BAS + '''
DEVICE GPIO AT &H4000_0000
  REG OUT  AT +0
  REG IN   AT +4  READONLY
  REG SET  AT +8  SETS OUT
  REG CLR  AT +12 CLEARS OUT
END DEVICE

DEVICE UART0 AT &H4000_1000
  REG DATA   AT +0 : 8
  REG STATUS AT +4 READONLY
  BIT TXREADY = STATUS.0
END DEVICE

ALIAS LED    = GPIO.OUT.3
ALIAS BUTTON = GPIO.IN.0

DIM presses AS WORD          ' RAM
DIM ticks   AS WORD IN A0    ' register

CONSOLE UART0
PRINT "RiscBasic ready", 13, 10

DO
  LED = BUTTON
  IF BUTTON THEN presses = presses + 1
  ticks = ticks + 1
  PRINT "presses ", DEC presses, " ticks ", HEX ticks, 13, 10
  DELAY_MS 100
LOOP
'''
CODE(EXAMPLE)
c = compile_check(EXAMPLE, 'example')
P('The LED follows the button, button presses are counted in a RAM variable, a loop counter runs in '
  'register `A0`, and a status line is printed every 100 ms. Compiled for RV32IC, the program is %d bytes '
  'of code, including the print routines.' % c.em.nbytes)

# ---- Appendix A: keyword reference
APPENDIX('A', 'Keyword reference')
KW = [
    ['`.include "file"`', 'Directive', 'Insert another source file', '5.1'],
    ['`#IF` `#ELSEIF` `#ELSE` `#ENDIF`', 'Directive', 'Conditional compilation', '5.2'],
    ['`#ERROR "text"`', 'Directive', 'Stop the compile with a message', '5.2'],
    ['`ALIAS`', 'Declaration', 'Second name for an object', '6.4'],
    ['`AND` `OR` `XOR` `NOT`', 'Operator', 'Bitwise / logical operators', '9.3'],
    ['`ASM` ... `END ASM`', 'Statement', 'Inline assembly', '16'],
    ['`ATOMIC`', 'Statement prefix', 'Assignment with interrupts disabled', '9.2'],
    ['`BIT` `FIELD`', 'Device', 'Named bit or field of a device register', '7.1'],
    ['`CALL`', 'Statement', 'Call a SUB', '13.1'],
    ['`CASE` `IS` `SELECT`', 'Statement', 'Multi-way branch', '12.3'],
    ['`CHR` `DEC` `HEX`', 'PRINT / INPUT item', 'Character, decimal, hex formatting', '15'],
    ['`CHR$` `DEC$` `HEX$`', 'String function', 'Number to text', '11.2'],
    ['`CLOCK` `CPI`', 'Declaration', 'Core clock and cycles per instruction', '4.2'],
    ['`CODE` `RAM` `STACK`', 'Declaration', 'Memory layout', '4.3'],
    ['`CONSOLE`', 'Declaration', 'Default device for PRINT and INPUT', '15.1'],
    ['`CONST`', 'Declaration', 'Named constant', '6.3'],
    ['`DELAY_US` `DELAY_MS`', 'Statement', 'Busy-wait delay', '14'],
    ['`DEVICE` ... `END DEVICE`', 'Declaration', 'Memory-mapped peripheral', '7'],
    ['`DIM` ... `AS` ... `IN`', 'Declaration', 'Variable or array', '6.2'],
    ['`DO` ... `LOOP [UNTIL]`', 'Statement', 'Loop', '12.2'],
    ['`END`', 'Statement', 'Stop the program; also ends blocks', '12.4'],
    ['`EXIT`', 'Statement', 'Leave FOR, DO, WHILE, REPEAT, SUB, FUNCTION', '12.2'],
    ['`FOR` `TO` `STEP` `NEXT`', 'Statement', 'Counted loop', '12.2'],
    ['`FUNCTION` ... `END FUNCTION`', 'Declaration', 'Function with a return value', '13.1'],
    ['`GOTO`', 'Statement', 'Jump to a label', '12.4'],
    ['`IF` `THEN` `ELSEIF` `ELSE` `END IF`', 'Statement', 'Conditional', '12.1'],
    ['`INPUT`', 'Statement', 'Read a character or number', '15.3'],
    ['`INSTR` `LEN` `VAL`', 'Function', 'Search, length, text to number', '11.2'],
    ['`LEFT$` `RIGHT$` `MID$`', 'String function', 'Substrings', '11.2'],
    ['`LET`', 'Statement', 'Optional assignment keyword', '3.2'],
    ['`LINE INPUT`', 'Statement', 'Read a line of text', '15.4'],
    ['`MEM` `MEM8` `MEM16` `MEM32`', 'Object', 'Memory at any 32-bit address', '8'],
    ['`MOD`', 'Operator', 'Remainder', '9.3'],
    ['`ON IRQ` ... `END IRQ`', 'Declaration', 'Interrupt handler', '13.3'],
    ['`PRINT`', 'Statement', 'Output to a device', '15.2'],
    ['`READONLY` `WRITEONLY`', 'Device', 'Register access restrictions', '7.1'],
    ['`REG` ... `AT`', 'Device', 'Device register', '7.1'],
    ['`REM`', 'Comment', 'Comment to the end of the line', '3.2'],
    ['`REPEAT` ... `UNTIL`', 'Statement', 'Loop, tested at the end', '12.2'],
    ['`RETURN`', 'Statement', 'Return from a FUNCTION (with value) or SUB', '13.1'],
    ['`SETS` `CLEARS`', 'Device', 'Set / clear registers', '7.2'],
    ['`SHL` `SHR` `SAR`', 'Operator', 'Shifts', '9.3'],
    ['`SHL` `SHR` `ROL` `ROR`', 'Statement', 'Shift / rotate in place', '10'],
    ['`STRING * n`', 'Type', 'Text buffer', '11.1'],
    ['`SUB` ... `END SUB`', 'Declaration', 'Subroutine', '13.1'],
    ['`TARGET`', 'Declaration', 'Instruction set', '4.1'],
    ['`WAIT UNTIL`', 'Statement', 'Poll until a condition is true', '14'],
    ['`WHILE` ... `WEND`', 'Statement', 'Loop, tested at the start', '12.2'],
]
T(['Keyword', 'Kind', 'Purpose', 'Section'], KW, [5.6, 3.0, 6.0, 1.8], size=8.5)

# ---- Appendix B: reserved words
APPENDIX('B', 'Reserved words')
P('These words cannot be used as names of variables, constants, aliases, labels, SUBs or FUNCTIONs:')
words = sorted(riscbasic.KEYWORDS)
p = doc.add_paragraph()
runs(p, ', '.join('`%s`' % w for w in words))
P('Also reserved: the register names `R0`–`R31` and the ABI names `ZERO`, `RA`, `SP`, `GP`, `TP`, '
  '`T0`–`T6`, `S0`–`S11`, `FP`, `A0`–`A7`.')

# ---- Appendix C: implementation notes
APPENDIX('C', 'Implementation notes and limits')
B('**Recursion.** Parameters and local `DIM` variables are not saved across a recursive call (locals '
  'are static RAM). Recursion works only if the routine does not need its parameters after the '
  'recursive call.',
  '**Stack checking.** RAM variables plus `STACK` must fit in `RAM SIZE`. The call depth is not analysed.',
  '**FOR counters** are compared signed, so a loop must not cross from `&H7FFF_FFFF` to `&H8000_0000`.',
  '**DELAY_US / DELAY_MS** assume 2 instructions per loop pass times `CPI`. Calibrate `CPI` on real '
  'hardware.',
  '**IRQ names.** `ON IRQ` knows `MSOFT`, `MTIMER` and `MEXT`. Other sources need a number or a `CONST`.',
  '**Array indexes** are not checked at run time.',
  '**MEM alignment** is not checked.',
  '**INPUT** has no line editing; use `LINE INPUT` for editable text.')
H2('Runtime routines')
P('These routines are emitted once, after the program, only if used. They are called with `jal ra` '
  '(`c.jal` on C targets), take arguments in `s0`/`s1`, and save every other register they use.')
T(['Routine', 'Used for'], [
    ['`__mul`, `__divu`, `__div`', '`*`, `/`, `MOD` without `+M`'],
    ['`__putc_dev`, `__puts_dev`, `__putbuf_dev`', 'PRINT of characters, text and BYTE arrays'],
    ['`__puthex_dev`, `__putdec_dev`', 'PRINT HEX and DEC'],
    ['`__getc_dev`, `__getdec_dev`, `__gethex_dev`, `__getline_dev`', 'INPUT and LINE INPUT'],
    ['`__sbegin`, `__sapp_chr`, `__sapp_str`, `__sapp_dec`, `__sapp_hex`', 'building strings'],
    ['`__strlen`, `__strcmp`, `__sapp_mid`, `__lenmid`, `__putmid_dev`, `__instr`, `__val`', 'string functions'],
], [9.0, 7.4])

doc.save(OUT)
print('saved', OUT)


# ---------------------------------------------------------------- PDF through Word

def word_pids():
    out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq WINWORD.EXE', '/FO', 'CSV', '/NH'],
                         capture_output=True, text=True).stdout
    return {int(line.split('","')[1]) for line in out.splitlines() if line.startswith('"WINWORD')}


def export_pdf(docx, pdf):
    """Update the TOC and export a PDF in a Word process of our own. Word documents the user
    has open stay untouched: if no new Word process appears, nothing is done."""
    import win32com.client
    before = word_pids()
    w = win32com.client.DispatchEx('Word.Application')
    time.sleep(1.5)
    new = word_pids() - before
    if len(new) != 1 or w.Documents.Count != 0:
        raise SystemExit('could not start a separate Word process; .docx written, no .pdf')
    w.Visible = False
    w.DisplayAlerts = 0
    d = None
    try:
        d = w.Documents.Open(docx, False, False)
        d.TablesOfContents(1).Update()
        d.Fields.Update()
        d.TablesOfContents(1).Update()
        d.Save()
        d.ExportAsFixedFormat(pdf, 17, False, 0, 0, 0, 0, 0, True, True, 1)    # 17: PDF
        print('saved %s (%d pages)' % (pdf, d.ComputeStatistics(2)))
    finally:
        if d is not None:
            d.Close(0)
        w.Quit(0)


if ARGS.pdf:
    export_pdf(OUT, os.path.splitext(OUT)[0] + '.pdf')
