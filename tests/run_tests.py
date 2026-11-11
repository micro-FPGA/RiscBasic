#!/usr/bin/env python3
"""RiscBasic test suite: compile each program, run it in rbsim, check the UART output."""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from riscbasic import Compiler, RBError   # noqa: E402
from rbsim import Sim                     # noqa: E402

PRELUDE = '''
TARGET {target}
CLOCK  25_000_000
CODE   AT &H0000 SIZE 8K
RAM    AT &H8000 SIZE 2K

DEVICE GPIO AT &H4000_0000
  REG OUT  AT +0
  REG IN   AT +4  READONLY
  REG SET  AT +8  SETS OUT
  REG CLR  AT +12 CLEARS OUT
  BIT LEDN = NOT OUT.5
END DEVICE

DEVICE UART0 AT &H4000_1000
  REG DATA   AT +0 : 8
  REG STATUS AT +4 READONLY
  REG RXDATA AT +8 : 8 READONLY
  BIT TXREADY = STATUS.0
  BIT RXREADY = STATUS.1
END DEVICE

DEVICE UART1 AT &H4000_1100
  REG DATA   AT +0 : 8
  REG STATUS AT +4 READONLY
  BIT TXREADY = NOT STATUS.0
  BIT RXREADY = NOT STATUS.1
END DEVICE

DEVICE PLAIN AT &H4000_2000
  REG CTRL AT +0
  REG WIDE AT +4
  FIELD MODE = CTRL[6:4]
  BIT HIGHBIT = CTRL.20
  BIT NB = NOT CTRL.2
  BIT NHIGH = NOT CTRL.20
  FIELD NF = NOT CTRL[11:8]
END DEVICE
'''

TESTS = []
RX = {}       # test name -> {uart number: input characters}


def test(name, body, expect, targets=('RV32I', 'RV32I+M', 'RV32IC', 'RV32IC+M'), irq_at=None, check=None):
    TESTS.append((name, body, expect, targets, irq_at, check))


test('print text, hex, dec', '''
PRINT UART0, "hello", 13, 10
PRINT UART0, "x=", HEX 255, " d=", DEC 1234567890, 13, 10
PRINT UART0, "no newline"
PRINT UART0, "!", 13, 10
''', 'hello\r\nx=000000FF d=1234567890\r\nno newline!\r\n')

test('print character constants', '''
CONST CR = 13
CONST LF = 10
PRINT UART0, "A", 66, "C", CR, LF
PRINT UART0, 0, 1, "xy", 0, "z", CR, LF
PRINT UART0, "v=", DEC 5, "!", 13, 10
PRINT UART0, "ab"; "cd";
PRINT UART0, CR, LF
PRINT UART0, "é", 255, CR, LF
''', 'ABC\r\n\x00\x01xy\x00z\r\nv=5!\r\nabcd\r\n\xe9\xff\r\n')

test('console', '''
CONST CR = 13
CONST LF = 10
CONSOLE UART1
PRINT "one", CR, LF
PRINT CR, LF
PRINT DEC 42, " ", HEX 255, CR, LF
PRINT UART0, "explicit", CR, LF
CONSOLE UART0
PRINT "two"; CR; LF
IF 1 THEN PRINT "if", CR, LF ELSE PRINT "else", CR, LF
''', 'one\r\n\r\n42 000000FF\r\nexplicit\r\ntwo\r\nif\r\n',
     check=lambda s: (''.join(s.uart_out.get(0x40001100, [])) == 'one\r\n\r\n42 000000FF\r\n' and
                      ''.join(s.uart_out.get(0x40001000, [])) == 'explicit\r\ntwo\r\nif\r\n'))

test('print CHR', '''
DEVICE UARTW AT &H4000_1000      ' UART0 again, with a 32-bit DATA register
  REG DATA   AT +0
  REG STATUS AT +4 READONLY
  BIT TXREADY = STATUS.0
END DEVICE
CONSOLE UART0
DIM c AS BYTE
DIM w AS WORD
FOR c = 65 TO 69
  PRINT CHR c
NEXT
PRINT 13, 10
w = &H143
PRINT CHR w, CHR 66, CHR 13, 10
PRINT CHR c - 1, "!", CHR 13 + 0, 10
PRINT UARTW, CHR w, CHR c, CHR R0 + 33, 13, 10
''', 'ABCDE\r\nCB\r\nE!\r\nCF!\r\n', check=lambda s: max(s.uart_raw) <= 255)

test('input', '''
CONSOLE UART0
DIM c AS BYTE
DIM n AS SWORD
DIM h AS WORD
DIM b(4) AS BYTE
INPUT "char? ", CHR c
PRINT " got ", CHR c, " ", DEC c, 13, 10
INPUT "num? ", DEC n
PRINT " n=", DEC n, 13, 10
INPUT DEC n, DEC h
PRINT "n=", DEC n, " h=", DEC h, 13, 10
INPUT "hex? ", HEX h
PRINT " h=", HEX h, 13, 10
INPUT CHR b(2), CHR R12
PRINT CHR b(2), CHR R12, 13, 10
INPUT UART1, DEC h
PRINT "uart1 ", DEC h, 13, 10
INPUT CHR PLAIN.MODE
PRINT DEC PLAIN.MODE, 13, 10
''', 'char?  got Z 90\r\nnum? -123 n=-123\r\n427n=42 h=7\r\nhex? 1aF0 h=00001AF0\r\nxy\r\n'
     '96uart1 96\r\n5\r\n')
RX['input'] = {0: 'Z-123\r42 7\r1aF0\rxy\x05', 1: '96\r'}

test('line input', '''
CONSOLE UART0
DIM name(8) AS BYTE
DIM n AS WORD
DIM k AS WORD
LINE INPUT "Name? ", name, n
PRINT 13, 10, DEC n, ":"
FOR k = 0 TO n - 1
  PRINT CHR name(k)
NEXT
PRINT "|", DEC name(n), 13, 10
LINE INPUT name, n
PRINT " ", DEC n, 13, 10
LINE INPUT name
PRINT CHR name(0), CHR name(1), DEC name(2), 13, 10
LINE INPUT UART1, name, R12
PRINT " ", DEC R12, 13, 10
''', 'Name? Annx\x08 \x08\x08 \x08a\r\n3:Ana|0\r\n1234567 7\r\nabab0\r\nxyz 3\r\n')
RX['line input'] = {0: 'Annx\x08\x7fa\n\r123456789\rab\r', 1: 'xy\x01z\r'}

test('print buffers', '''
CONSOLE UART0
DIM name(8) AS BYTE
DIM raw(4) AS BYTE
LINE INPUT "Name? ", name
PRINT 13, 10, "Hello, ", name, "!", 13, 10
raw(0) = 65
raw(1) = 66
raw(2) = 67
raw(3) = 68
PRINT raw, "|", 13, 10
name(0) = 0
PRINT "[", name, "]", 13, 10
PRINT UART1, name, raw, 13, 10
''', 'Name? Bob\r\nHello, Bob!\r\nABCD|\r\n[]\r\nABCD\r\n',
     check=lambda s: ''.join(s.uart_out.get(0x40001100, [])) == 'ABCD\r\n')
RX['print buffers'] = {0: 'Bob\r'}

test('dec edge values', '''
DIM s AS SWORD
DIM u AS WORD
s = -42
u = &HFFFFFFFF
PRINT UART0, DEC s, " ", DEC u, " ", DEC 0, " ", DEC 7, 13, 10
s = -2147483647 - 1
PRINT UART0, DEC s, 13, 10
''', '-42 4294967295 0 7\r\n-2147483648\r\n')

test('hex widths', '''
DIM b AS BYTE
DIM h AS HALF
b = &HA5
h = &H1234
PRINT UART0, HEX b, " ", HEX h, " ", HEX R0, 13, 10
''', 'A5 1234 00000000\r\n')

test('arithmetic', '''
DIM a AS WORD
DIM b AS WORD
DIM s AS SWORD
DIM t AS SWORD
a = 1000
b = 7
s = -100
t = 7
PRINT UART0, DEC a + b, " ", DEC a - b, " ", DEC a * b, " ", DEC a / b, " ", DEC a MOD b, 13, 10
PRINT UART0, DEC s * t, " ", DEC s / t, " ", DEC s MOD t, 13, 10
PRINT UART0, DEC a * 8, " ", DEC a / 4, " ", DEC a MOD 16, 13, 10
PRINT UART0, DEC (a + b) * (a - b) / 3, 13, 10
''', '1007 993 7000 142 6\r\n-700 -14 -2\r\n8000 250 8\r\n333317\r\n')

test('bits and fields on registers', '''
R10 = 0
R10.0 = 1
R10.12 = 1
R10[7:4] = 5
PRINT UART0, HEX R10, 13, 10
R10.0 = 0
R10[7:4] = 0
R11 = R10.12
PRINT UART0, HEX R10, " ", DEC R11, 13, 10
R1.0 = 1
PRINT UART0, DEC R1.0, 13, 10
''', '00001051\r\n00001000 1\r\n1\r\n')

test('gpio set/clear', '''
ALIAS LED = GPIO.OUT.3
GPIO.OUT = 0
LED = 1
PRINT UART0, HEX GPIO.OUT, 13, 10
GPIO.OUT.0 = 1
LED = 0
PRINT UART0, HEX GPIO.OUT, 13, 10
''', '00000008\r\n00000001\r\n')

test('led = button', '''
ALIAS LED    = GPIO.OUT.3
ALIAS BUTTON = GPIO.IN.0
LED = BUTTON
PRINT UART0, HEX GPIO.OUT, 13, 10
''', '00000008\r\n', check=lambda s: s.gpio_out == 8, irq_at=None)

test('read-modify-write device', '''
PLAIN.CTRL = &HFFFFFFFF
PLAIN.MODE = 2
PRINT UART0, HEX PLAIN.CTRL, 13, 10
PLAIN.HIGHBIT = 0
PRINT UART0, HEX PLAIN.CTRL, 13, 10
DIM v AS WORD
v = 5
PLAIN.MODE = v
PLAIN.HIGHBIT = v
PRINT UART0, HEX PLAIN.CTRL, " ", DEC PLAIN.MODE, 13, 10
''', 'FFFFFFAF\r\nFFEFFFAF\r\nFFFFFFDF 5\r\n')

test('comparisons and logic', '''
DIM a AS WORD
DIM s AS SWORD
a = 10
s = -5
PRINT UART0, DEC a > 5, DEC a < 5, DEC a = 10, DEC a <> 10, DEC a >= 10, DEC a <= 9, 13, 10
PRINT UART0, DEC s < 0, DEC s > -10, DEC (a > 5) AND (s < 0), DEC NOT (a = 10), 13, 10
IF a > 5 AND s < 0 THEN PRINT UART0, "both", 13, 10
IF NOT (a = 3) THEN PRINT UART0, "not3", 13, 10 ELSE PRINT UART0, "is3", 13, 10
''', '101010\r\n1110\r\nboth\r\nnot3\r\n')

test('if elseif else', '''
DIM i AS WORD
FOR i = 1 TO 4
  IF i = 1 THEN
    PRINT UART0, "one"
  ELSEIF i = 2 THEN
    PRINT UART0, "two"
  ELSEIF i = 3 THEN
    PRINT UART0, "three"
  ELSE
    PRINT UART0, "many"
  END IF
NEXT
PRINT UART0, "", 13, 10
''', 'onetwothreemany\r\n')

test('loops', '''
DIM i AS WORD
DIM n AS WORD IN S2
FOR R12 = 0 TO 4
  PRINT UART0, DEC R12
NEXT
PRINT UART0, "", 13, 10
FOR S3 = 10 TO 0 STEP -2
  PRINT UART0, DEC S3, " "
NEXT
PRINT UART0, "", 13, 10
n = 0
WHILE n < 3
  n = n + 1
WEND
i = 0
DO
  i = i + 5
LOOP UNTIL i >= 20
PRINT UART0, DEC n, " ", DEC i, 13, 10
DO
  i = i - 1
  IF i = 15 THEN EXIT DO
LOOP
PRINT UART0, DEC i, 13, 10
''', '01234\r\n10 8 6 4 2 0 \r\n3 20\r\n15\r\n')

test('select case', '''
DIM i AS WORD
FOR i = 0 TO 5
  SELECT CASE i
  CASE 0
    PRINT UART0, "z"
  CASE 1, 2
    PRINT UART0, "s"
  CASE 4
    PRINT UART0, "f"
  CASE ELSE
    PRINT UART0, "?"
  END SELECT
NEXT
PRINT UART0, "", 13, 10
''', 'zss?f?\r\n')

test('sub and function', '''
DIM total AS WORD
DIM g AS WORD IN A0
SUB Show(v AS WORD)
  PRINT UART0, "v=", DEC v, 13, 10
END SUB
FUNCTION Add3(a AS WORD, b AS WORD, c AS WORD) AS WORD
  RETURN a + b + c
END FUNCTION
FUNCTION Fact(n AS WORD) AS WORD
  DIM r AS WORD
  r = 1
  WHILE n > 1
    r = r * n
    n = n - 1
  WEND
  RETURN r
END FUNCTION
g = 77
total = Add3(1, 2, 3) * 10
Show total
Show(Fact(5))
CALL Show(Add3(Fact(3), g, 1))
PRINT UART0, DEC g, 13, 10
''', 'v=60\r\nv=120\r\nv=84\r\n77\r\n')

test('shift and rotate commands', '''
DIM b AS BYTE
DIM w AS WORD
R10 = &H80000001
ROL R10
PRINT UART0, HEX R10, 13, 10
ROR R10, 4
PRINT UART0, HEX R10, 13, 10
b = &H81
ROR b
PRINT UART0, HEX b, 13, 10
ROL b, 3
PRINT UART0, HEX b, 13, 10
w = 3
SHL w, 4
PRINT UART0, DEC w, 13, 10
SHR w
PRINT UART0, DEC w, 13, 10
R11 = 8
R10 = &H12345678
ROL R10, R11
PRINT UART0, HEX R10, 13, 10
ROR R10, R11
PRINT UART0, HEX R10, 13, 10
SHL R10, R11
PRINT UART0, HEX R10, 13, 10
''', '00000003\r\n30000000\r\nC0\r\n06\r\n48\r\n24\r\n34567812\r\n12345678\r\n34567800\r\n',
     targets=('RV32I', 'RV32I+M', 'RV32I+Zbb', 'RV32IC', 'RV32IC+Zbb'))

test('narrow register variables', '''
DIM b AS BYTE IN S4
DIM c AS SBYTE IN S5
b = 250
b = b + 10
c = 120
c = c + 10
PRINT UART0, DEC b, " ", DEC c, 13, 10
c.7 = 0
PRINT UART0, DEC c, 13, 10
''', '4 -126\r\n2\r\n')

test('arrays', '''
DIM buf(8) AS HALF
DIM i AS WORD
DIM sum AS WORD
FOR i = 0 TO 7
  buf(i) = i * 100
NEXT
buf(3) = 7
sum = 0
FOR i = 0 TO 7
  sum = sum + buf(i)
NEXT
PRINT UART0, DEC sum, " ", DEC buf(7), 13, 10
''', '2507 700\r\n')

test('mem access', '''
MEM32(&H8100) = &H11223344
PRINT UART0, HEX MEM8(&H8100), " ", HEX MEM16(&H8102), " ", HEX MEM32(&H8100), 13, 10
R13 = &H8104
MEM32(R13) = 99
PRINT UART0, DEC MEM32(R13), 13, 10
''', '44 1122 11223344\r\n99\r\n')

test('goto, labels, wait, delay', '''
R12 = 0
again:
R12 = R12 + 1
IF R12 < 3 THEN GOTO again
WAIT UNTIL UART0.TXREADY
DELAY_US 10
PRINT UART0, DEC R12, 13, 10
''', '3\r\n')

test('inline asm', '''
CONST K = 40
DIM v AS WORD IN S6
DIM m AS WORD
ASM
  li v, K
  addi v, v, 2
  sw v, m
END ASM
PRINT UART0, DEC v, " ", DEC m, 13, 10
''', '42 42\r\n')

test('atomic', '''
ATOMIC GPIO.OUT = 5
ATOMIC PLAIN.MODE = 3
PRINT UART0, HEX GPIO.OUT, 13, 10
''', '00000005\r\n')

test('interrupt', '''
DIM count AS WORD
ON IRQ MTIMER
  count = count + 1
END IRQ
ASM
  csrsi mstatus, 8
END ASM
DO
LOOP UNTIL count = 2
PRINT UART0, "irq ", DEC count, 13, 10
''', 'irq 2\r\n', irq_at={500: 7, 900: 7})

test('temporaries: irq mid-expression, 7 params, t0/t1 vars', '''
DIM count AS WORD
DIM junk AS WORD
DIM acc AS WORD IN T0
DIM k AS WORD IN T1
ON IRQ MTIMER
  count = count + 1
  junk = (junk * 7 + 3) / 3 + count * 5
END IRQ
SUB Seven(a AS WORD, b AS WORD, c AS WORD, d AS WORD, e AS WORD, f AS WORD, g AS WORD)
  PRINT UART0, DEC a + b + c + d + e + f + g, " ", DEC f, " ", DEC g, 13, 10
END SUB
ASM
  csrsi mstatus, 8
END ASM
acc = 0
FOR k = 1 TO 300
  acc = acc + (k * 3 + 5) * 2 - k
NEXT
PRINT UART0, DEC acc, " ", DEC count, 13, 10
Seven 1, 2, 3, 4, 5, 6, 7
''', '228750 3\r\n28 6 7\r\n', irq_at={200: 7, 600: 7, 1000: 7})

test('rv32e', '''
DIM x AS WORD IN A1
DIM y AS WORD IN T2
x = 6
y = 1
x = x * 7
PRINT UART0, DEC x, 13, 10
''', '42\r\n', targets=('RV32E', 'RV32EC', 'RV32EC+M'))

test('spec example', '''
ALIAS LED    = GPIO.OUT.3
ALIAS BUTTON = GPIO.IN.0
DIM presses AS WORD
DIM ticks   AS WORD IN A0
presses = 0
ticks = 0
PRINT UART0, "RiscBasic ready", 13, 10
DO
  LED = BUTTON
  IF BUTTON THEN presses = presses + 1
  ticks = ticks + 1
  PRINT UART0, "presses ", DEC presses, " ticks ", HEX ticks, 13, 10
  DELAY_MS 1
  IF ticks = 2 THEN EXIT DO
LOOP
''', 'RiscBasic ready\r\npresses 1 ticks 00000001\r\npresses 2 ticks 00000002\r\n')

test('inverted bits and fields (NOT)', '''
PRINT UART1, "active-low ready", 13, 10
PLAIN.CTRL = 0
PRINT UART0, DEC PLAIN.NB, DEC PLAIN.NHIGH, " ", HEX PLAIN.NF, 13, 10
PLAIN.NB = 1
PLAIN.NB = 0
PLAIN.NF = 3
PLAIN.NHIGH = 0
PRINT UART0, HEX PLAIN.CTRL, " ", DEC PLAIN.NB, " ", HEX PLAIN.NF, " ", DEC PLAIN.NHIGH, 13, 10
R10 = 0
PLAIN.NB = R10
R10 = 7
PLAIN.NB = R10
PLAIN.NF = R10
PRINT UART0, HEX PLAIN.CTRL, 13, 10
IF PLAIN.NB THEN PRINT UART0, "nb set", 13, 10
IF NOT PLAIN.NB THEN PRINT UART0, "wrong", 13, 10
WAIT UNTIL PLAIN.NB
GPIO.OUT = 0
GPIO.LEDN = 1
PRINT UART0, HEX GPIO.OUT, 13, 10
GPIO.LEDN = 0
PRINT UART0, HEX GPIO.OUT, 13, 10
GPIO.LEDN = R10
PRINT UART0, HEX GPIO.OUT, " ", DEC GPIO.LEDN, 13, 10
ALIAS LEDX = NOT GPIO.OUT.6
LEDX = 0
PRINT UART0, HEX GPIO.OUT, " ", DEC LEDX, 13, 10
ALIAS BACK = NOT PLAIN.NB
PRINT UART0, DEC BACK, 13, 10
''', 'active-low ready\r\n11 F\r\n00100C04 0 3 0\r\n00100800\r\nnb set\r\n00000000\r\n'
     '00000020\r\n00000000 1\r\n00000040 0\r\n0\r\n')

# One block of more than 4 KB (also on RV32IC): the IF jumps forward over it,
# LOOP UNTIL jumps back over it. The block runs only in the second pass.
LONG = '\n'.join('    R12 = R12 + &H%X' % (0x10001 + k) for k in range(520))
test('far branches (blocks over 4 KB)', '''
R12 = 0
R14 = 0
DO
  IF R14 = 1 THEN
''' + LONG + '''
  END IF
  R14 = R14 + 1
LOOP UNTIL R14 = 2
PRINT UART0, "looped ", DEC R14, " ", HEX R12, 13, 10
''', 'looped 2 %08X\r\n' % (sum(0x10001 + k for k in range(520)) & 0xFFFFFFFF),
     targets=('RV32I', 'RV32IC'))

ERRORS = [
    ('R0 = 1', 'read-only'),
    ('S0 = 1', 'reserved'),
    ('DIM v AS WORD IN A5', 'cannot hold'),
    ('GPIO.IN = 1', 'READONLY'),
    ('DIM x AS WORD IN GP', 'cannot hold'),
    ('R16 = 1', 'RV32E'),
    ('PRINT GPIO, "x"', 'TXREADY'),
    ('IF 1 THEN', 'not closed'),
    ('SHL R10, 40', 'out of range'),
    ('DIM b AS BYTE' + chr(10) + 'ROL b, R10', '32-bit destination'),
    ('CODE AT 0 SIZE 4' + chr(10) + 'R10 = 1', None),
    ('x = 1', 'unknown name'),
    ('ALIAS X = NOT GPIO.OUT', 'bit or field'),
    ('PRINT UART0, 256', 'out of range'),
    ('DIM q AS WORD' + chr(10) + 'PRINT UART0, q', 'only constants'),
    ('PRINT UART0', 'at least one item'),
    ('PRINT "x"', 'CONSOLE declaration'),
    ('PRINT UART0, CHR 300', 'out of range'),
    ('INPUT GPIO, CHR R10', 'RXREADY'),
    ('CONSOLE UART0' + chr(10) + 'INPUT DEC 5', 'name expected'),
    ('CONSOLE UART0' + chr(10) + 'INPUT DEC GPIO.IN', 'READONLY'),
    ('INPUT CHR R10', 'CONSOLE declaration'),
    ('CONSOLE UART0' + chr(10) + 'DIM n AS WORD' + chr(10) + 'LINE INPUT n', 'BYTE array'),
    ('CONSOLE UART0' + chr(10) + 'DIM w(4) AS WORD' + chr(10) + 'LINE INPUT w', 'BYTE array'),
    ('CONSOLE UART0' + chr(10) + 'DIM b(4) AS BYTE' + chr(10) + 'LINE b', 'INPUT expected'),
    ('DIM line AS WORD', 'reserved'),
    ('DIM w(4) AS WORD' + chr(10) + 'PRINT UART0, w', 'BYTE array'),
    ('DIM CHR AS WORD', 'reserved'),
    ('CONSOLE GPIO', 'TXREADY'),
    ('CONSOLE NOPE', 'not a device'),
    ('CONSOLE UART0' + chr(10) + 'PRINT', 'at least one item'),
    ('CONSOLE UART0' + chr(10) + 'PRINT "a" "b"', 'unexpected'),
]


def run_one(name, body, expect, target, irq_at, check):
    src = PRELUDE.format(target=target) + body
    c = Compiler(name)
    c.compile(src)
    s = Sim(c.binary(), c.em.base, c.ram_base, c.ram_size)
    s.gpio_in = 1
    for uart, text in RX.get(name, {}).items():
        s.rx[uart] = list(text)
    done = s.run(irq_at=irq_at)
    out = s.output()
    ok = done and out == expect and (check is None or check(s))
    return ok, out, done, c


def fuzz_rvc(n=4000, seed=7):
    """Compress random instructions with the compiler, expand them with the simulator,
    execute both forms from the same random state and compare the results."""
    import random
    from riscbasic import Emitter, asm_line, compress, ABI
    from rbsim import expand
    rnd = random.Random(seed)
    regs = ABI[1:16]
    small = ABI[8:16]

    def r(pool=regs):
        return rnd.choice(pool)

    gens = [
        lambda: 'addi %s, %s, %d' % ((lambda x: (x, x))(r()) + (rnd.randint(-40, 40),)),
        lambda: 'addi %s, zero, %d' % (r(), rnd.randint(-40, 40)),
        lambda: 'addi %s, %s, 0' % (r(), r()),
        lambda: 'addi sp, sp, %d' % rnd.choice([-512, -64, -48, -16, 16, 32, 496, -4, 8]),
        lambda: 'addi %s, sp, %d' % (r(small), rnd.choice([4, 8, 64, 1020, 2, 1024])),
        lambda: 'slli %s, %s, %d' % ((lambda x: (x, x))(r()) + (rnd.randint(0, 31),)),
        lambda: 'srli %s, %s, %d' % ((lambda x: (x, x))(r(small)) + (rnd.randint(0, 31),)),
        lambda: 'srai %s, %s, %d' % ((lambda x: (x, x))(r(small)) + (rnd.randint(0, 31),)),
        lambda: 'andi %s, %s, %d' % ((lambda x: (x, x))(r(small)) + (rnd.randint(-40, 40),)),
        lambda: '%s %s, %s, %s' % (rnd.choice(['add', 'sub', 'xor', 'or', 'and']),
                                   *(lambda x: (x, x, r(small)) if rnd.random() < .5 else (x, r(small), x))(r(small))),
        lambda: 'add %s, %s, %s' % ((lambda x: (x, x, r()))(r())),
        lambda: 'add %s, zero, %s' % (r(), r()),
        lambda: 'lui %s, 0x%X' % (r(), rnd.choice([1, 5, 31, 32, 0xFFFFF, 0xFFFE0, 0xFFFDF, 0x12345])),
        lambda: 'lw %s, %d(sp)' % (r(), rnd.choice([0, 4, 128, 252, 256])),
        lambda: 'sw %s, %d(sp)' % (r(), rnd.choice([0, 4, 128, 252, 256])),
        lambda: 'lw %s, %d(%s)' % (r(small), rnd.choice([0, 4, 60, 124, 128]), r(small)),
        lambda: 'sw %s, %d(%s)' % (r(small), rnd.choice([0, 4, 60, 124, 128]), r(small)),
        lambda: 'jalr %s, 0(%s)' % (rnd.choice(['zero', 'ra']), r()),
        # offset 2 is left out (for a 16-bit jump it equals the fall-through address),
        # and jal offset 0 is the simulator's halt instruction
        lambda: ('jal %s, T' % rnd.choice(['zero', 'ra']), rnd.choice([-2048, -100, 6, 2046, 2048])),
        lambda: ('%s %s, T' % (rnd.choice(['beqz', 'bnez']), r(small)), rnd.choice([-256, -10, 0, 6, 254, 256])),
    ]
    tested = mismatches = 0
    for _ in range(n):
        g = rnd.choice(gens)()
        target = None
        if isinstance(g, tuple):
            g, target = g
        e = Emitter()
        e.base = 0x1000
        try:
            asm_line(e, g)
            e.finalize({'T': 0x1000 + (target or 0)})
        except Exception:
            continue
        w = e.code[0]
        h = compress(w)
        if h is None:
            continue
        w2 = expand(h)
        state = [rnd.getrandbits(32) for _ in range(32)]
        state[0] = 0
        results = []
        for word, size in ((w, 4), (h, 2)):
            s = Sim(b'', 0, 0x8000, 0x2000)
            s.x = list(state)
            for k in range(1, 32):           # keep address registers inside RAM
                s.x[k] = 0x8800 + (state[k] & 0x3FC)
            for k in range(0x8000, 0xA000, 4):
                s.store(k, 4, k * 2654435761)
            s.pc = 0x1000
            s.code[0x1000:0x1000 + size] = word.to_bytes(size, 'little')
            s.step()
            regs_after = list(s.x)
            if w & 0x7F in (0x6F, 0x67):     # link register holds pc + size
                rdl = (w >> 7) & 31
                if rdl:
                    regs_after[rdl] -= size
            taken = s.pc != 0x1000 + size
            results.append((regs_after, s.pc if taken else None, bytes(s.ram)))
        tested += 1
        if results[0] != results[1]:
            mismatches += 1
            if mismatches <= 5:
                print('     RVC mismatch: %-28s %08X -> %04X -> %08X' % (g, w, h, w2))
    return tested, mismatches


def main():
    failed = 0
    tested, bad = fuzz_rvc()
    print('%s RVC fuzz: %d compressed instructions checked, %d mismatches'
          % ('ok  ' if bad == 0 and tested > 1000 else 'FAIL', tested, bad))
    failed += 1 if bad or tested <= 1000 else 0
    for name, body, expect, targets, irq_at, check in TESTS:
        for target in targets:
            try:
                ok, out, done, c = run_one(name, body, expect, target, irq_at, check)
            except (RBError, RuntimeError) as e:
                ok, out, done = False, 'EXCEPTION: %s (line %s)' % (e, getattr(e, 'line', '?')), False
            status = 'ok  ' if ok else 'FAIL'
            print('%s %-32s %s' % (status, name, target))
            if not ok:
                failed += 1
                print('     expected %r' % expect)
                print('     got      %r%s' % (out, '' if done else '  (did not halt)'))
    for body, frag in ERRORS:
        target = 'RV32E' if 'R16' in body else 'RV32I'
        try:
            Compiler('err').compile(PRELUDE.format(target=target) + body)
            print('FAIL error expected: %s' % body)
            failed += 1
        except RBError as e:
            if frag and frag not in e.msg:
                print('FAIL %-32s wrong message: %s' % (body, e.msg))
                failed += 1
            else:
                print('ok   error: %-26s -> %s' % (body, e.msg))
    print('\n%s' % ('all tests passed' if not failed else '%d test(s) failed' % failed))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
