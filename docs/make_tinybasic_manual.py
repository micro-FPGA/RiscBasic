#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright 7AG (2026) MicroFPGA UG(h)
# https://github.com/micro-FPGA/RiscBasic

"""
make_tinybasic_manual: build the Tiny BASIC manual as .docx, and optionally as .pdf.

    python docs/make_tinybasic_manual.py          # docs/TinyBasic-Manual.docx
    python docs/make_tinybasic_manual.py --pdf    # also update the table of contents and write the .pdf

The interpreter (examples/lib/tinybasic.bas) is compiled for the test simulator, and every example
session in the manual is run in rbsim: the transcripts are the interpreter's real output. The build
stops if the interpreter or the Sapphire version does not compile.

Needs python-docx. --pdf needs Microsoft Word on Windows and pywin32: the script starts its own Word
process, checks that it is a new one, and never touches a Word that is already running.
"""

import argparse
import os
import re
import subprocess
import sys
import tempfile
import time

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_BREAK, WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ap = argparse.ArgumentParser(description='Build the Tiny BASIC manual')
ap.add_argument('-o', '--output', default=os.path.join(HERE, 'TinyBasic-Manual.docx'))
ap.add_argument('--pdf', action='store_true', help='also update the TOC and export a .pdf with Word')
ARGS = ap.parse_args()
OUT = os.path.abspath(ARGS.output)

SRC = os.path.join(ROOT, 'source')
EXAMPLES = os.path.join(ROOT, 'examples')
sys.path.insert(0, SRC)
import riscbasic  # noqa: E402
from rbsim import Sim  # noqa: E402

ACCENT = RGBColor(0x1F, 0x4E, 0x79)
GREY = RGBColor(0x59, 0x59, 0x59)
BODY_FONT, CODE_FONT = 'Calibri', 'Consolas'
DATE = '19 November 2026 (7 AG)'

# ---------------------------------------------------------------- build the interpreter

def compile_file(path):
    r = subprocess.run([sys.executable, os.path.join(SRC, 'riscbasic.py'), path],
                       capture_output=True, text=True, cwd=os.path.dirname(path))
    if r.returncode != 0:
        raise SystemExit('%s does not compile:\n%s%s' % (path, r.stdout, r.stderr))
    m = re.search(r'(\d+) bytes code \(of (\d+)\), (\d+) bytes RAM', r.stdout)
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


SAP_CODE, SAP_CODE_MAX, SAP_RAM = compile_file(os.path.join(EXAMPLES, 'tinybasic_sapphire.bas'))
with open(os.path.join(EXAMPLES, 'tinybasic_sapphire.bas')) as f:
    sap_src = f.read()
PSIZE = int(re.search(r'CONST PSIZE\s*=\s*(\d+)', sap_src).group(1))
RAM_SIZE = int(re.search(r'CONST RAM_SIZE\s*=\s*(\d+)', sap_src).group(1))
PTOP = PSIZE - 72 - 4

SIM_WRAPPER = '''' Tiny BASIC in the rbsim test simulator
TARGET RV32I
CLOCK  25_000_000
CODE   AT &H0000 SIZE 16384
RAM    AT &H8000 SIZE 8192

CONST PSIZE = %d                   ' the same program space as on the Sapphire board

DEVICE UART0 AT &H4000_1000
  REG DATA   AT +0 : 8
  REG STATUS AT +4 READONLY
  REG RXDATA AT +8 READONLY
  BIT TXREADY = STATUS.0
  BIT RXREADY = STATUS.1
END DEVICE
CONSOLE UART0

FUNCTION KeyHit AS WORD            ' all input is queued in advance: never report a key early
  RETURN 0
END FUNCTION

.include "%s"
''' % (PSIZE, os.path.join(EXAMPLES, 'lib', 'tinybasic.bas').replace('\\', '/'))

TMP = tempfile.mkdtemp(prefix='tbman_')
with open(os.path.join(TMP, 'tbsim.bas'), 'w') as f:
    f.write(SIM_WRAPPER)
compile_file(os.path.join(TMP, 'tbsim.bas'))
with open(os.path.join(TMP, 'tbsim.bin'), 'rb') as f:
    SIM_IMAGE = f.read()


def run_session(text, banner=False):
    """Type text (lines separated by newline) into a fresh interpreter; return the terminal output."""
    for line in text.split('\n'):
        if len(line) > 72:
            raise SystemExit('example line longer than 72 characters: ' + line)
    s = Sim(SIM_IMAGE, 0, 0x8000, 0x2000)
    s.rx[0] = list(text.strip('\n').replace('\n', '\r') + '\r')
    idle = 0
    seen = 0
    while s.steps < 200_000_000:                # until all input is read and it waits at the prompt
        s.run(s.steps + 50000)
        if s.rx[0] or len(s.out) != seen or s.out[-2:] != ['>', ' ']:
            idle = 0
            seen = len(s.out)
        else:
            idle += 1
            if idle > 2:
                break
    out = s.output().replace('\r', '')
    if not out.endswith('> '):
        raise SystemExit('session did not end at the prompt:\n' + out)
    out = out[:-2].rstrip('\n')
    if not banner:
        out = out.split('\n', 2)[2]          # leading empty line and the banner
    else:
        out = out.lstrip('\n')
    return out


# ---------------------------------------------------------------- document helpers

doc = Document()
h_counter = [0, 0, 0]
APPX = [None]


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
    for name, fill, border in (('Code', 'F3F5F8', '9DB3CC'), ('Terminal', 'EEF5EC', '8DB582')):
        code = st.add_style(name, 1)
        code.base_style = normal
        set_font(code, CODE_FONT)
        code.font.size = Pt(8.5)
        pf = code.paragraph_format
        pf.space_after = Pt(0)
        pf.space_before = Pt(0)
        pf.line_spacing = 1.0
        pf.left_indent = Cm(0.25)
        shade_ppr(code.element.get_or_add_pPr(), fill, left=border)
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
        sp.set(qn('w:before'), '40' if level == 1 else '0')
        sp.set(qn('w:after'), '0')
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


def NOTE(text):
    return runs(doc.add_paragraph(style='Note'), text)


def block(text, style):
    lines = text.strip('\n').split('\n')
    for i, ln in enumerate(lines):
        p = doc.add_paragraph(style=style)
        p.add_run(ln.replace('\t', '        ') if ln else ' ')
        pf = p.paragraph_format
        pf.keep_together = True
        if i < len(lines) - 1:
            pf.keep_with_next = True
        if i == 0:
            pf.space_before = Pt(3)
        if i == len(lines) - 1:
            pf.space_after = Pt(8)


def CODE(text):
    block(text, 'Code')


def SESSION(text, banner=False):
    """Run text in the interpreter and show the terminal: what was typed and what came back."""
    block(run_session(text, banner), 'Terminal')


def H1(title):
    h_counter[0] += 1
    h_counter[1] = h_counter[2] = 0
    doc.add_heading('%d  %s' % (h_counter[0], title), level=1)


def H2(title):
    h_counter[1] += 1
    h_counter[2] = 0
    top = APPX[0] or h_counter[0]
    doc.add_heading('%s.%d  %s' % (top, h_counter[1], title), level=2)


def APPENDIX(letter, title):
    APPX[0] = letter
    h_counter[1] = 0
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
    """Table with a shaded, repeated header row. widths in cm (sum about 16.4)."""
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
        tr._tr.get_or_add_trPr().append(OxmlElement('w:cantSplit'))
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
    for kind in ('begin', None, 'separate'):
        r = p.add_run()
        if kind:
            fc = OxmlElement('w:fldChar')
            fc.set(qn('w:fldCharType'), kind)
            r._r.append(fc)
        else:
            it = OxmlElement('w:instrText')
            it.set(qn('xml:space'), 'preserve')
            it.text = ' %s ' % instr
            r._r.append(it)
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
    runs(fp, 'Tiny BASIC for RiscBasic  ·  User Manual  ·  MIT License', size=8.5, color=GREY)
    r = fp.add_run('\t')
    r.font.size = Pt(8.5)
    field(fp, 'PAGE', '1')
    for r in fp.runs:
        r.font.size = Pt(8.5)
        r.font.color.rgb = GREY


# ---------------------------------------------------------------- content

setup_styles()
page_setup()

for _ in range(5):
    doc.add_paragraph()
p = doc.add_paragraph(style='Title')
p.add_run('Tiny BASIC')
p = doc.add_paragraph()
runs(p, 'User Manual', size=22, color=ACCENT)
p = doc.add_paragraph()
p.paragraph_format.space_before = Pt(8)
runs(p, 'An interactive BASIC interpreter for RISC-V soft cores, written in RiscBasic', size=12.5, color=GREY)
for _ in range(3):
    doc.add_paragraph()
for line in (DATE,
             '',
             'RiscBasic language design: Antti Lukats (Identor #1)',
             'Interpreter: Claude (Identor #9)'):
    runs(doc.add_paragraph(), line, size=11)
for _ in range(9):
    doc.add_paragraph()
for line in ('SPDX-License-Identifier: MIT', 'Copyright 7AG (2026) MicroFPGA UG(h)',
             'https://github.com/micro-FPGA/RiscBasic'):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(0)
    runs(p, line, size=9.5, color=GREY)
doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)

p = doc.add_paragraph()
runs(p, 'Contents', size=20, bold=True, color=ACCENT)
p = doc.add_paragraph()
field(p, 'TOC \\o "1-2" \\h \\z \\u', 'Right-click and choose Update Field to build the table of contents.')

# ---- 1 Introduction
H1('Introduction')
P('Tiny BASIC is a small, interactive BASIC interpreter in the tradition of the Tiny BASICs of the '
  '1970s. You type program lines on a serial terminal, list and change them, and run them at once, '
  'without a PC-side compiler. It runs on a RISC-V soft core with a UART, such as the Efinix Sapphire '
  'SoC.')
P('The interpreter is itself a RiscBasic program: about 950 lines of RiscBasic in '
  '`examples/lib/tinybasic.bas`, compiled by the RiscBasic compiler into %d bytes of RISC-V code. '
  'So there are two BASICs in this manual: **RiscBasic**, the compiled language the interpreter is '
  'written in, and **Tiny BASIC**, the interpreted language you type at the `>` prompt.' % SAP_CODE)
H2('Features')
B('Line editor: numbered lines are stored in order, replaced or deleted; lines without a number run at once.',
  '26 variables `A` to `Z` and one array `@()`, all 32-bit signed integers.',
  'Statements: `LET`, `PRINT`, `INPUT`, `IF … THEN`, `GOTO`, `GOSUB`, `RETURN`, `FOR … TO … STEP`, '
  '`NEXT`, `REM`, `END`, `STOP`, `POKE`, `DELAY`; commands `RUN`, `LIST`, `NEW`.',
  'Expressions with `+ - * / MOD`, comparisons, `AND OR NOT`, brackets, decimal and `&H` hex numbers.',
  'Functions `ABS`, `RND`, `PEEK`, `KEY` and `FREE`.',
  'Direct hardware access with `PEEK` and `POKE` (32-bit memory and device registers).',
  'Error messages with the line number, and Ctrl-C to stop a program.')
H2('Sizes on the Efinix Sapphire SoC')
T(['Item', 'Size'], [
    ['Interpreter code', '%d bytes (of %d reserved for code)' % (SAP_CODE, SAP_CODE_MAX)],
    ['RAM for data', '%d bytes: variables, stacks, program text and the CPU stack' % RAM_SIZE],
    ['Program space', '%d bytes (shown as *bytes free* after start)' % PTOP],
    ['Total', 'fits the 16 KB on-chip RAM of the default Sapphire configuration'],
], [4.4, 12.0])

# ---- 2 Getting started
H1('Getting started')
H2('Build and load')
P('Compile the Sapphire version in the `examples` directory (it is also part of `COMPILE.bat`):')
CODE('python ../source/riscbasic.py tinybasic_sapphire.bas')
P('Load `tinybasic_sapphire.elf` into the SoC RAM and start it at `0xF9000000`, for example with the '
  'OpenOCD of the Efinity RISC-V IDE and the configuration files of your SoC BSP:')
CODE('openocd -s <bsp>/openocd -c "set CPUTAPID 0x00240a79" -f ftdi.cfg -f debug.cfg\n'
     '        -c init -c halt -c "load_image tinybasic_sapphire.elf"\n'
     '        -c "reg pc 0xF9000000" -c resume -c shutdown')
P('To start Tiny BASIC from power-up, put the image into the RAM initialization of the SoC (IP '
  'Manager → Generate) and rebuild the bitstream, as for any other Sapphire program.')
NOTE('`CPUTAPID 0x00240a79` is the JTAG ID of the T20 FPGA on the TEG1015 board. Other devices need '
     'their own ID, or none if the BSP default is right.')
H2('Terminal')
P('Connect a terminal program to the UART of the SoC: **115200 baud, 8 data bits, no parity, 1 stop bit**, '
  'no flow control. The terminal must send Enter as CR (most do; CR LF also works, the LF is ignored). '
  'After the start Tiny BASIC prints its banner and the prompt `>`:')
SESSION('', banner=True)
NOTE('**On Windows, use Tera Term, not PuTTY.** PuTTY is not good for Tiny BASIC, especially for '
     'pasting programs. In Tera Term, paste with a **right click** in the terminal window.')
H2('A first session')
P('Type a line without a number and it runs at once. `OK` says that it has finished:')
SESSION('PRINT "Hello from Tiny BASIC"\nPRINT 6*7')
P('Lines with a number are stored as a program. `LIST` shows it, `RUN` starts it:')
SESSION('10 FOR I = 1 TO 5\n20 PRINT I, I*I, I*I*I\n30 NEXT I\nLIST\nRUN')
P('In the transcripts of this manual, the text after `>` and `?` is what was typed; everything else '
  'comes from Tiny BASIC. All transcripts were produced by running the interpreter.')

# ---- 3 Entering programs
H1('Entering and editing programs')
H2('Program lines and direct mode')
B('A line that starts with a number from 1 to 65535 is a **program line**. It is stored in the program, '
  'sorted by its number. Nothing runs.',
  'A line with the number of an existing line **replaces** that line.',
  'A number alone **deletes** that line.',
  'A line without a number is run at once (**direct mode**), then `OK` is printed. Every statement can '
  'be used in direct mode, also `FOR` loops on one line and `GOTO` into the program.',
  'Several statements can be written on one line, separated by `:`.')
SESSION('20 PRINT "second"\n10 PRINT "first"\n30 PRINT "third"\nLIST\n20 PRINT "new second"\n30\nLIST')
P('Number your lines in steps of 10, so new lines fit in between.')
H2('Typing')
B('Up to 72 characters per line. Backspace (or DEL) deletes the last character.',
  'Keywords and variable names may be typed in upper or lower case. Everything outside of quotes is '
  'converted to upper case when the line is stored, also the text after `REM`. Text in quotes keeps its case.',
  'Spaces are kept as typed and may be left out: `FORI=1TO10` is the same as `FOR I = 1 TO 10`.',
  'A keyword is recognized wherever it starts, so variables can only be one letter.')
H2('Pasting programs')
P('A program can be pasted into the terminal as text, one line per program line, for example the demo '
  '`examples/tinybasic/guess.bas`. On Windows, use Tera Term and paste with a right click in its '
  'window (see 2.2). There is no flow control: if characters are lost, set a delay of 10 to 20 ms '
  'after each line in the terminal program (in Tera Term: Setup → Serial port → Transmit delay, '
  'msec/line).')
H2('Stopping a program')
P('**Ctrl-C** stops a running program with `? Break in line n`. It is checked at every line, at every '
  'jump (`GOTO`, `NEXT`, `IF`, ...), while `DELAY` waits, while `LIST` prints and while `INPUT` waits for its '
  'line. At the `>` prompt Ctrl-C discards the line being typed.')
H2('Memory')
P('Each program line takes its text plus 4 bytes. `PRINT FREE` shows how much is left; when the '
  'program space is full, a new line is refused with `? Out of memory`.')
SESSION('PRINT FREE\n10 REM this line takes 4 + 19 bytes\nPRINT FREE')

# ---- 4 Language
H1('The language')
H2('Numbers')
P('All values are **32-bit signed integers**, from −2147483648 to 2147483647. There is no floating '
  'point. Results wrap around without an error.')
B('Decimal: `123`. A negative number is written with unary minus: `-5`.',
  'Hexadecimal: `&H` and the digits `0`–`9`, `A`–`F`: `&HFF`, `&HF8015004`.')
SESSION('PRINT 2147483647 + 1, &HFF, &H7FFFFFFF')
H2('Variables')
B('`A` to `Z`: 26 variables. `RUN` sets them all to 0; they keep their values between direct-mode lines.',
  '`@(i)`: one array of 64 elements, `@(0)` to `@(63)`. An index outside this range gives `? Bad index`. '
  '`RUN` also clears the array.')
SESSION('A = 5 : B = A * A : PRINT A, B\n@(0) = 10 : @(1) = @(0) + 1 : PRINT @(0) + @(1)')
H2('Operators')
P('From the lowest to the highest precedence:')
T(['Operators', 'Meaning'], [
    ['`OR`', 'bitwise OR'],
    ['`AND`', 'bitwise AND'],
    ['`NOT`', 'logical NOT: 1 if the operand is 0, else 0'],
    ['`=  <>  <  >  <=  >=`', 'comparison, signed: 1 if true, 0 if false'],
    ['`+  -`', 'addition, subtraction'],
    ['`*  /  MOD`', 'multiplication, division (rounds toward 0), remainder (sign of the dividend)'],
    ['`-` (unary)', 'negation'],
    ['`( )`, functions', 'brackets, function calls'],
], [4.4, 12.0])
P('Operators of the same precedence work from left to right. Because comparisons give 1 or 0, `AND` and '
  '`OR` also combine conditions: `IF A > 0 AND A < 10 THEN ...`. `NOT` binds weaker than a comparison, '
  'so `NOT A = B` means `NOT (A = B)`.')
SESSION('PRINT 1 + 2 * 3, (1 + 2) * 3, 10 - 2 - 3\nPRINT -7 / 2, -7 MOD 2, 7 MOD -3\n'
        'PRINT 3 < 5, 3 > 5, NOT 0, NOT 5\nPRINT 12 AND 10, 12 OR 3, NOT 2 = 3')
H2('Functions')
T(['Function', 'Result'], [
    ['`ABS(x)`', 'absolute value of x'],
    ['`RND(n)`', 'a pseudo-random number from 1 to n; n must be at least 1'],
    ['`PEEK(addr)`', 'the 32-bit word at address addr (memory or a device register)'],
    ['`KEY`', 'the code of a key pressed on the terminal, or 0 if none: does not wait. '
              'Ctrl-C is never returned, it stops the program'],
    ['`FREE`', 'bytes left for program lines'],
], [3.4, 13.0])
SESSION('PRINT ABS(-9), ABS(9)\nPRINT RND(6), RND(6), RND(6), RND(100)')
NOTE('`RND` starts with the same sequence after every reset. For different numbers, count while waiting '
     'for a key and mix the count in, as `guess.bas` does: '
     '`40 R=R+1: K=KEY: IF K=0 THEN 40` and then `N=(RND(100)+R) MOD 100+1`.')
H2('Text')
P('Text in double quotes can be printed with `PRINT` and used as an `INPUT` prompt. There are no '
  'string variables, and a quote character cannot be part of a text.')

# ---- 5 Statements
H1('Statements')
P('In the syntax lines, `[ ]` marks optional parts, *expr* is an expression, *var* a variable `A`–`Z` '
  'and *line* a line number. Statements can be used in program lines and in direct mode.')


def STMT(title, syntax, *paras):
    H2(title)
    CODE(syntax)
    for t in paras:
        P(t)


STMT('LET: assignment', '[LET] var = expr\n[LET] @(expr) = expr',
     'Sets a variable or an array element. The word `LET` can be left out.')
SESSION('LET X = 3\nY = X * 2 + 1 : PRINT X; " "; Y\n@(5) = Y : PRINT @(5)')
STMT('PRINT (or ?): output', 'PRINT [item [; item | , item] ...] [; | ,]\n? ...',
     'Prints texts in quotes and the values of expressions. Numbers are printed in decimal with a '
     '`-` when negative and without spaces around them.',
     'A `;` between items prints them directly after each other; a `,` prints a TAB character, '
     'which most terminals show as a jump to the next column of 8. Without `;` or `,` at the end, '
     'PRINT ends the line; with one of them at the end, the next PRINT continues on the same line. '
     '`PRINT` alone prints an empty line. `?` is short for `PRINT`.')
SESSION('PRINT "A=";5;" B=";-6\nPRINT 1, 22, 333\n10 PRINT "no new line ";\n20 PRINT "here"\nRUN\n? "short"')
STMT('INPUT: read numbers', 'INPUT ["prompt";] target [, target ...]',
     'Prints the prompt (if given) and `? `, then reads a line from the terminal and stores its number '
     'in the target, a variable or `@(expr)`. Each target asks with its own `? ` line. The number may '
     'have a sign and may be written in hex with `&H` or `0x`, or in binary with `&B`. A line without a '
     'number gives 0; reading stops at the first character that is not part of the number.')
SESSION('10 INPUT "Width";W\n20 INPUT "Height";H\n30 PRINT "Area: ";W*H\nRUN\n12\n5\nINPUT A, B : PRINT A + B\n&H10\n-1')
STMT('IF: condition', 'IF expr [THEN] statement [: statement ...]\nIF expr THEN line',
     'If the expression is not 0, the statements after `THEN` run, up to the end of the line. If it is '
     '0, the rest of the line is skipped and the program continues with the next line. `THEN` followed '
     'by a number is a jump to that line. `THEN` can be left out before a statement.',
     'There is no `ELSE`: use a second `IF` with the opposite condition, or a jump.')
SESSION('A = 5\nIF A > 3 THEN PRINT "big" : PRINT "really"\nIF A > 9 THEN PRINT "huge" : PRINT "not shown"\nIF A = 5 PRINT "five"')
STMT('GOTO: jump', 'GOTO expr',
     'Continues the program at the line with this number. The number can be computed: `GOTO 100 + 10 * N`. '
     'A number that is not a program line gives `? No such line`. In direct mode, GOTO starts the '
     'program at that line without clearing the variables.')
STMT('GOSUB and RETURN: subroutines', 'GOSUB expr\nRETURN',
     '`GOSUB` jumps like `GOTO` but remembers where it came from; `RETURN` continues after the `GOSUB`. '
     'Subroutines can call other subroutines, up to 8 levels deep.')
SESSION('10 FOR N = 1 TO 3\n20 GOSUB 100\n30 NEXT N\n40 END\n100 PRINT "square of ";N;" is ";N*N\n110 RETURN\nRUN')
STMT('FOR and NEXT: loops', 'FOR var = expr TO expr [STEP expr]\nNEXT [var]',
     'Sets the variable to the first value and runs the statements up to `NEXT`. `NEXT` adds the step '
     '(default 1) and repeats the loop while the variable has not passed the end value: not greater '
     'with a positive step, not smaller with a negative one. The body always runs at least once.',
     'Loops can be nested up to 8 deep. `NEXT` without a variable closes the innermost loop; `NEXT var` '
     'also ends any loops inside the one of var. A new `FOR` with the variable of a running loop '
     'replaces that loop, so leaving a loop with `GOTO` is safe.')
SESSION('FOR I = 10 TO 1 STEP -3 : PRINT I; " "; : NEXT : PRINT\n'
        'FOR I=1 TO 3: FOR J=1 TO 3: PRINT I*J;" ";: NEXT J: PRINT: NEXT I')
STMT('REM: comment', "REM text\n' text",
     'The rest of the line is a comment. `\'` is short for `REM`.')
STMT('END and STOP', 'END\nSTOP',
     '`END` ends the program; a program also ends after its last line. `STOP` ends it with the '
     'message `? Break in line n`, which helps when testing.')
STMT('POKE: write memory', 'POKE address, value',
     'Writes the value as a 32-bit word to the address: RAM or a device register. Use addresses that '
     'are a multiple of 4. `PEEK(address)` reads a word.',
     'On the TEG1015 board the GPIO output register is at `&HF8015004`. Its bits 0, 1 and 2 drive '
     'the red, green and blue parts of the RGB LED, which light when the bit is 0:')
CODE('POKE &HF8015004, 6        \' bit 0 low: red\nPOKE &HF8015004, 7        \' all off\n'
     'PRINT PEEK(&HF8015000)    \' GPIO inputs')
NOTE('POKE writes wherever it is told, also into the program text and the variables of the '
     'interpreter. A wrong address can stop the system; then reset it.')
STMT('DELAY: wait', 'DELAY milliseconds',
     'Waits the given number of milliseconds. Ctrl-C stops the wait.')
CODE('10 FOR I = 0 TO 15\n20 POKE &HF8015004, I : DELAY 250\n30 NEXT I\n40 GOTO 10')
STMT('RUN, LIST and NEW: commands', 'RUN\nLIST [from][-[to]]\nNEW',
     '`RUN` clears all variables and the array, then starts the program at its first line.',
     '`LIST` prints the program: `LIST` all of it, `LIST 100` one line, `LIST 100-` from line 100 on, '
     '`LIST -100` up to line 100, `LIST 100-200` a range.',
     '`NEW` deletes the program. The commands also work inside a program.')
SESSION('10 REM one\n20 REM two\n30 REM three\nLIST 20-\nLIST -10\nNEW\nLIST')

# ---- 6 Errors
H1('Error messages')
P('An error stops the program and prints a message, with the line number when it happened in a '
  'program line. The program and the variables stay as they are, so they can be inspected with `PRINT` '
  'in direct mode. Errors in a direct-mode line show no line number.')
T(['Message', 'Cause'], [
    ['`? Syntax error`', 'The line is not valid Tiny BASIC: an unknown word, a missing bracket or `=`, '
     'text left over after a statement, a variable name with more than one letter.'],
    ['`? No such line`', '`GOTO`, `GOSUB` or `THEN` to a line number that does not exist.'],
    ['`? Division by zero`', '`/` or `MOD` with 0 as divisor.'],
    ['`? Out of memory`', 'The new line does not fit into the program space.'],
    ['`? RETURN without GOSUB`', '`RETURN` with no open `GOSUB`.'],
    ['`? NEXT without FOR`', '`NEXT` with no open `FOR`, or `NEXT var` for a variable without a loop.'],
    ['`? Too complex`', 'More than 8 `GOSUB` levels or `FOR` loops, or an expression with too many '
     'pending operators and brackets.'],
    ['`? Bad index`', '`@()` index outside 0 to 63, or `RND` with a value below 1.'],
    ['`? Break`', 'Ctrl-C or `STOP`.'],
], [4.6, 11.8])
SESSION('10 A = 10\n20 B = 0\n30 PRINT A / B\nRUN\nPRINT A, B\nGOTO 99\nPRINT 1 +\nPRINT @(64)\nNEXT')

# ---- 7 Examples
H1('Example programs')
H2('Prime numbers')
PRIMES = '''10 REM PRIME NUMBERS UP TO 100
20 FOR N = 2 TO 100
30 FOR D = 2 TO N
40 IF D * D > N THEN PRINT N; " "; : GOTO 70
50 IF N MOD D = 0 THEN GOTO 70
60 NEXT D
70 NEXT N
80 PRINT'''
CODE(PRIMES)
P('`GOTO 70` leaves the inner loop early; the next `NEXT N` ends it, because `NEXT var` closes the '
  'loops inside the loop of var.')
SESSION(PRIMES + '\nRUN', banner=False)
H2('Sorting with the array')
SORT = '''10 REM SORT 10 RANDOM NUMBERS
20 FOR I = 0 TO 9 : @(I) = RND(100) : NEXT I
30 GOSUB 200
40 FOR I = 0 TO 8
50 FOR J = 0 TO 8 - I
60 IF @(J) > @(J + 1) THEN T = @(J) : @(J) = @(J + 1) : @(J + 1) = T
70 NEXT J
80 NEXT I
90 GOSUB 200
100 END
200 FOR K = 0 TO 9 : PRINT @(K); " "; : NEXT K : PRINT
210 RETURN'''
CODE(SORT)
SESSION(SORT + '\nRUN')
H2('Fibonacci numbers')
FIB = '''10 A = 0 : B = 1
20 FOR I = 1 TO 20
30 PRINT B,
40 C = A + B : A = B : B = C
50 IF I MOD 5 = 0 THEN PRINT
60 NEXT I'''
CODE(FIB)
SESSION(FIB + '\nRUN')
H2('Guess the number')
P('`examples/tinybasic/guess.bas` is a small game. It waits for a key to mix the timing of the player '
  'into the random number, then answers each guess with *Higher!* or *Lower!*:')
with open(os.path.join(EXAMPLES, 'tinybasic', 'guess.bas')) as f:
    GUESS = f.read()
CODE(GUESS)
GUESS_SIM = '\n'.join(l for l in GUESS.strip().split('\n') if not l.startswith(('30 ', '40 ')))
g = run_session(GUESS_SIM + '\n55 PRINT "SECRET=";N: END\nRUN')    # the same secret, then stop
secret = int(re.search(r'SECRET=(\d+)', g).group(1))
lo, hi, guesses = 1, 100, []
while True:
    x = (lo + hi) // 2
    guesses.append(x)
    if x == secret:
        break
    if x < secret:
        lo = x + 1
    else:
        hi = x - 1
g = run_session(GUESS_SIM + '\nRUN\n' + '\n'.join(map(str, guesses)) + '\n0')
g = g[g.index('> RUN'):]
P('A game (the key press is left out):')
block(g, 'Terminal')
H2('LED counter on the TEG1015 board')
P('Counts on the four GPIO outputs; the RGB LED shows the lower three bits as colors:')
CODE('10 REM COUNT ON THE FOUR GPIO OUTPUTS, CTRL-C STOPS\n20 FOR I = 0 TO 15\n30 POKE &HF8015004, I\n'
     '40 DELAY 300\n50 NEXT I\n60 GOTO 20')
H2('LED rotate on the TEG1015 board')
P('`examples/tinybasic/leds.bas` runs a bit over the four GPIO outputs, either round (1, 2, 4, 8, 1, ...) '
  'or back and forth (1, 2, 4, 8, 4, 2, ...). The keys are read with `KEY` while it runs, so the '
  'program reacts without waiting:')
with open(os.path.join(EXAMPLES, 'tinybasic', 'leds.bas')) as f:
    LEDS = f.read()
CODE(LEDS)
T(['Key', 'Effect'], [
    ['`+`', 'faster: halves the delay (100 ms at the start), down to 6 ms'],
    ['`-`', 'slower: doubles the delay, up to 1600 ms'],
    ['space', 'switches between rotate and bounce'],
    ['Ctrl-C', 'stops the program; the LEDs keep their last pattern'],
], [3.4, 13.0])
P('`N` is the number of LEDs and `T` the bit of the last one, so the program works for other '
  'LED counts too. For a board with the LEDs at another address, change `G` in line 20.')
H2('143: I love you in seven colors')
P('Old pagers sent **143** for *I love you*: the words have 1, 4 and 3 letters. '
  '`examples/tinybasic/love.bas` flashes the code on the RGB LED (one flash, four flashes, three '
  'flashes) in each of the seven colors the three bits can make, and names the color on the terminal. '
  'The flashes take `W` = 200 ms; the whole message takes about 35 seconds.')
with open(os.path.join(EXAMPLES, 'tinybasic', 'love.bas')) as f:
    LOVE = f.read()
CODE(LOVE)
P('`P=1` is for an LED that lights when its bit is 0, as on the TEG1015 board; set `P=0` for one that '
  'lights at 1. The subroutine at line 200 flashes color `C` `N` times, the one at line 300 prints '
  'its name. On the terminal:')
LOVE_SIM = LOVE.replace('&HF8015004', '&H40002000').replace('W=200', 'W=1')   # RAM, no GPIO in rbsim
g = run_session(LOVE_SIM + '\nRUN')
block(g[g.index('> RUN') + 6:], 'Terminal')

# ---- 8 Inside
H1('How it works')
H2('Files')
T(['File', 'Contents'], [
    ['`examples/lib/tinybasic.bas`', 'The interpreter. Independent of the hardware.'],
    ['`examples/tinybasic_sapphire.bas`', 'Memory layout and console for the Efinix Sapphire SoC.'],
    ['`examples/include/CPU/SAPPHIRE.bas`', 'Sapphire target, GPIO and UART devices.'],
    ['`examples/tinybasic/guess.bas`', 'Demo program, to paste into the terminal.'],
    ['`examples/tinybasic/leds.bas`', 'LED rotate demo for the TEG1015 board.'],
    ['`examples/tinybasic/love.bas`', '143 demo for the RGB LED of the TEG1015 board.'],
    ['`docs/make_tinybasic_manual.py`', 'Builds this manual and runs its examples in the simulator.'],
], [6.4, 10.0])
H2('Program storage')
P('The program is kept as text, in one byte array `P()`, sorted by line number. Each line is a record:')
T(['Bytes', 'Contents'], [
    ['2', 'line number, low byte first'],
    ['1', 'length of the whole record (4 + length of the text)'],
    ['n', 'the text of the line, upper case outside quotes'],
    ['1', '0, end of the text'],
], [2.0, 14.4])
P('A direct-mode line is stored as a record right after the end of the program, outside of it, and run '
  'from there with the same code as a program line. When it ends, it has run past the end of the '
  'program, just like a program that has finished.')
H2('Running statements')
P('The interpreter reads the text directly, without converting it to tokens. A text pointer moves along '
  'the line; at each statement the keyword is looked up in a table, and the routine for the statement '
  'reads its operands. Line numbers in jumps are found by searching the program from its start.')
H2('Expressions without recursion')
P('In RiscBasic, the local variables of a SUB or FUNCTION are static: they have one fixed place in '
  'RAM, so a recursive call would overwrite them. The usual recursive-descent expression parser is '
  'therefore replaced by an operator-precedence parser with two explicit stacks, one for values and one '
  'for operators (16 entries each). Brackets and functions are entries on the operator stack.')
H2('Errors')
P('The command loop saves the stack pointer when it starts. An error prints its message, restores the '
  'stack pointer and jumps back to the command loop, with two lines of inline assembly:')
CODE('ASM\n  lw sp, SavedSP\n  j L_WARM\nEND ASM')
P('This leaves all SUBs that were active at once, like `longjmp` in C.')

# ---- 9 Porting
H1('Using Tiny BASIC on other targets')
P('`lib/tinybasic.bas` does not know the hardware. A target file sets up the CPU, the memory and a '
  'console, defines one function, and includes the interpreter:')
B('a `CONSOLE` device that `PRINT`, `INPUT CHR` and `LINE INPUT` can use: registers `DATA`, `TXREADY`, '
  '`RXDATA`, `RXREADY`, or `Put` and `Get` handlers (see the RiscBasic Reference Manual);',
  '`FUNCTION KeyHit AS WORD`, not 0 when a character is waiting. It is used for Ctrl-C and `KEY`;',
  'optionally `CONST PSIZE` (bytes for program text, default 4096) and `CONST ASIZE` (elements of '
  '`@()`, default 64), defined before the include.')
P('The Sapphire version:')
CODE(sap_src)
P('The version used for the examples of this manual, in the RiscBasic test simulator `rbsim`:')
CODE(re.sub(r'\.include ".*"', '.include "lib/tinybasic.bas"', SIM_WRAPPER))
H2('Memory')
P('RAM is needed for about 1 KB of variables and stacks of the interpreter, `PSIZE` bytes of program '
  'text, 4 × `ASIZE` bytes for `@()` and 256 bytes of CPU stack. The program space is `PSIZE` − 76 '
  'bytes; the rest holds the direct-mode line. With a larger SoC RAM, raise `SOC_RAM_SIZE`, `RAM_SIZE` '
  'and `PSIZE` together in `tinybasic_sapphire.bas`.')

# ---- Appendix
APPENDIX('A', 'Quick reference')
T(['Keyword', 'Form', 'Meaning'], [
    ['`ABS`', '`ABS(x)`', 'absolute value'],
    ['`AND`', '`a AND b`', 'bitwise AND'],
    ['`DELAY`', '`DELAY ms`', 'wait'],
    ['`END`', '`END`', 'end the program'],
    ['`FOR`', '`FOR v = a TO b [STEP s]`', 'loop start'],
    ['`FREE`', '`FREE`', 'free program space'],
    ['`GOSUB`', '`GOSUB line`', 'call a subroutine'],
    ['`GOTO`', '`GOTO line`', 'jump'],
    ['`IF`', '`IF x [THEN] statements` / `IF x THEN line`', 'condition'],
    ['`INPUT`', '`INPUT ["prompt";] v, ...`', 'read numbers'],
    ['`KEY`', '`KEY`', 'key code or 0'],
    ['`LET`', '`[LET] v = x`', 'assignment'],
    ['`LIST`', '`LIST [from][-[to]]`', 'show the program'],
    ['`MOD`', '`a MOD b`', 'remainder'],
    ['`NEW`', '`NEW`', 'delete the program'],
    ['`NEXT`', '`NEXT [v]`', 'loop end'],
    ['`NOT`', '`NOT x`', 'logical NOT'],
    ['`OR`', '`a OR b`', 'bitwise OR'],
    ['`PEEK`', '`PEEK(addr)`', 'read a 32-bit word'],
    ['`POKE`', '`POKE addr, value`', 'write a 32-bit word'],
    ['`PRINT`, `?`', '`PRINT items`', 'output'],
    ['`REM`, `\'`', '`REM text`', 'comment'],
    ['`RETURN`', '`RETURN`', 'end a subroutine'],
    ['`RND`', '`RND(n)`', 'random number 1 to n'],
    ['`RUN`', '`RUN`', 'clear variables, start the program'],
    ['`STEP`', 'in `FOR`', 'loop step'],
    ['`STOP`', '`STOP`', 'stop with a Break message'],
    ['`THEN`', 'in `IF`', ''],
    ['`TO`', 'in `FOR`', ''],
], [2.8, 7.4, 6.2], size=8.5)

doc.save(OUT)
print('saved', OUT)


# ---------------------------------------------------------------- PDF through Word

def word_pids():
    out = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq WINWORD.EXE', '/FO', 'CSV', '/NH'],
                         capture_output=True).stdout.decode('oem', errors='replace')
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
