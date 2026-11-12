#!/usr/bin/env python3
"""RiscBasic test suite: compile each program, run it in rbsim, check the UART output."""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
INC = os.path.join(HERE, 'inc')      # .include fixtures: programs that include files compile from here
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

DEVICE WO AT &H4000_3000
  REG OUT AT +0 WRITEONLY
  REG SET AT +4 SETS OUT
  REG CLR AT +8 CLEARS OUT
  BIT LED = OUT.2
  BIT LEDN = NOT OUT.9
  FIELD F = OUT[7:4]
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

test('writeonly with shadow', '''
CONSOLE UART0
WO.OUT = &H100
WO.LED = 1
WO.F = 9
PRINT HEX WO.OUT, " ", DEC WO.LED, " ", DEC WO.F, " ", DEC WO.LEDN, 13, 10
WO.LEDN = 0
IF WO.LEDN THEN PRINT "on", 13, 10 ELSE PRINT "off", 13, 10
WO.SET = &H1000
WO.CLR = &H4
PRINT HEX WO.OUT, 13, 10
R10 = 3
WO.F = R10
ATOMIC WO.LED = 1
WAIT UNTIL WO.LED
PRINT HEX WO.OUT, 13, 10
INPUT HEX WO.OUT
PRINT " ", HEX WO.OUT, 13, 10
''', '00000194 1 9 1\r\noff\r\n00001390\r\n00001334\r\n7e ' '0000007E\r\n',
     check=lambda s: s.wout_log == [0x100, 0x104, 0x194, 0x394, 0x1394, 0x1390, 0x1330, 0x1334, 0x7E])
RX['writeonly with shadow'] = {0: '7e\r'}

test('repeat until', '''
CONSOLE UART0
DIM i AS WORD
DIM n AS WORD
i = 0
REPEAT
  i = i + 1
  PRINT DEC i
UNTIL i >= 5
PRINT 13, 10
i = 100
REPEAT
  PRINT "once"
UNTIL 1
PRINT 13, 10
n = 0
REPEAT
  i = 0
  REPEAT
    i = i + 1
    IF i = 3 THEN EXIT REPEAT
  UNTIL 0
  n = n + i
  DO
    n = n + 1
  LOOP UNTIL n MOD 2 = 0
UNTIL n > 10
PRINT DEC n, 13, 10
REPEAT
UNTIL UART0.TXREADY
PRINT "ok", 13, 10
''', '12345\r\nonce\r\n12\r\nok\r\n')

test('case ranges', '''
CONSOLE UART0
DIM i AS SWORD
DIM c AS BYTE
DIM w AS WORD
FOR i = -3 TO 12
  SELECT CASE i
  CASE -3 TO -1
    PRINT "n"
  CASE 0
    PRINT "z"
  CASE 1 TO 3, 7
    PRINT "a"
  CASE 4 TO 6
    PRINT "b"
  CASE 8, 10 TO 11
    PRINT "c"
  CASE ELSE
    PRINT "?"
  END SELECT
NEXT
PRINT 13, 10
c = "q"
SELECT CASE c
CASE "0" TO "9"
  PRINT "digit"
CASE "a" TO "z", "A" TO "Z"
  PRINT "letter"
END SELECT
w = &H90000000
SELECT CASE w
CASE 0 TO &H7FFFFFFF
  PRINT " low"
CASE &H80000000 TO &HFFFFFFFF
  PRINT " high"
END SELECT
SELECT CASE w
CASE 5000 TO 100000
  PRINT " mid"
CASE ELSE
  PRINT " other"
END SELECT
PRINT 13, 10
''', 'nnnzaaabbbac?cc?\r\nletter high other\r\n')

test('case is', '''
CONSOLE UART0
DIM i AS SWORD
DIM w AS WORD
FOR i = -2 TO 6
  SELECT CASE i
  CASE IS < 0
    PRINT "n"
  CASE 0, IS = 2
    PRINT "z"
  CASE IS <= 3, 5
    PRINT "s"
  CASE IS >= 6
    PRINT "L"
  CASE IS <> 99
    PRINT "x"
  END SELECT
NEXT
w = &HFFFFFFF0
SELECT CASE w
CASE IS < 100
  PRINT " small"
CASE IS > 100
  PRINT " big"
END SELECT
i = -16
SELECT CASE i
CASE IS < 100
  PRINT " neg"
CASE ELSE
  PRINT " ?"
END SELECT
SELECT CASE w
CASE IS < 0, 1 TO 9, IS > &HFFFFFF00
  PRINT " mixed"
END SELECT
PRINT 13, 10
''', 'nnzszsxsL big neg mixed\r\n')

test('single-line if else', '''
CONSOLE UART0
DIM a AS WORD
DIM b AS WORD
FOR a = 0 TO 1
  IF a THEN PRINT "T" ELSE PRINT "F"
  IF a = 1 THEN R12 = 5 ELSE R12 = 7
  PRINT DEC R12
NEXT
PRINT 13, 10
FOR a = 0 TO 1
  FOR b = 0 TO 1
    IF a THEN IF b THEN PRINT "11 " ELSE PRINT "10 "
  NEXT
NEXT
PRINT 13, 10
FOR a = 0 TO 1
  FOR b = 0 TO 1
    IF a THEN IF b THEN PRINT "11 " ELSE PRINT "10 " ELSE PRINT "0x "
  NEXT
NEXT
PRINT 13, 10
IF 0 THEN PRINT "no" ELSE IF 1 THEN PRINT "else-if" ELSE PRINT "no"
PRINT 13, 10
''', 'F7T5\r\n10 11 \r\n0x 0x 10 11 \r\nelse-if\r\n')

test('colon separator', '''
CONSOLE UART0
DIM i AS WORD : DIM s AS WORD
SUB Hi : PRINT "hi" : END SUB
s = 0 : FOR i = 1 TO 4 : s = s + i : NEXT : PRINT DEC s, 13, 10
R10 = 0 : R10[7:4] = 5 : PRINT HEX R10, 13, 10
i = 0 : DO : i = i + 1 : LOOP UNTIL i = 3 : PRINT DEC i, 13, 10
IF i = 3 THEN PRINT "a" : PRINT "b" ELSE PRINT "c" : PRINT "d"
PRINT 13, 10
IF i = 4 THEN PRINT "a" : PRINT "b" ELSE PRINT "c" : PRINT "d"
PRINT 13, 10
IF i = 3 THEN IF s = 10 THEN PRINT "x" : PRINT "y" ELSE PRINT "z"
PRINT 13, 10
IF i = 3 THEN IF s = 11 THEN PRINT "x" : PRINT "y" ELSE PRINT "z" : PRINT "w"
PRINT 13, 10
done: PRINT "label" : PRINT 13, 10
SELECT CASE i : CASE 3 : PRINT "three" : CASE ELSE : PRINT "?" : END SELECT : PRINT 13, 10
PRINT "rem" : REM a comment : PRINT "no"
PRINT 13, 10
Hi : Hi : PRINT 13, 10
IF s = 10 THEN : PRINT "then-colon"
PRINT 13, 10
''', '10\r\n00000050\r\n3\r\nab\r\ncd\r\nxy\r\nzw\r\nlabel\r\nthree\r\nrem\r\nhihi\r\nthen-colon\r\n')

test('strings', '''
CONSOLE UART0
CONST CR = 13
CONST LF = 10
DIM name AS STRING * 12
DIM s AS STRING * 8
DIM n AS SWORD
FUNCTION Hex8(v AS WORD) AS STRING * 12
  RETURN "0x" + HEX$(v)
END FUNCTION
FUNCTION Greet(who AS STRING) AS STRING * 16
  RETURN "Hi " + who + "!"
END FUNCTION
FUNCTION Pad(v AS WORD) AS STRING * 6
  IF v < 10 THEN RETURN "00" + DEC$(v)
  IF v < 100 THEN RETURN "0" + DEC$(v)
  RETURN DEC$(v)
END FUNCTION
FUNCTION Empty() AS STRING * 4
END FUNCTION
FUNCTION Twice(x AS WORD) AS WORD
  RETURN x * 2
END FUNCTION
SUB Show(t AS STRING)
  PRINT "[", t, "]", CR, LF
END SUB
FUNCTION Maybe(x AS WORD) AS STRING * 8
  IF x THEN RETURN "yes"
  EXIT FUNCTION
END FUNCTION
PRINT "[", Maybe(1), "|", Maybe(0), "]", CR, LF
PRINT Hex8(255), CR, LF
name = "Bob"
PRINT Greet(name), CR, LF
PRINT Greet("Ann") + "?", CR, LF
s = "abcdefghijk"
PRINT s, "|", CR, LF
n = -42 : name = "n=" + DEC$(n) + CHR$(33)
PRINT name, CR, LF
name = "<" + name + ">"
PRINT name, CR, LF
name = Pad(7) + "/" + Pad(42) + "/" + Pad(123)
PRINT name, CR, LF
PRINT Greet(Hex8(16)), CR, LF
PRINT Greet(Greet("X")), CR, LF
PRINT "[", Empty(), "]", CR, LF
name = "a" + Pad(5) + "b"
PRINT name, CR, LF
s = DEC$(Twice(21))
PRINT s, CR, LF
n = 65 : s = CHR$(n) + CHR$(n + 1)
PRINT s, CR, LF
Show "lit"
Show name
Show Hex8(1)
LINE INPUT "Name? ", name
PRINT CR, LF, Greet(name), CR, LF
''', '[yes|]\r\n0x000000FF\r\nHi Bob!\r\nHi Ann!?\r\nabcdefg|\r\nn=-42!\r\n<n=-42!>\r\n007/042/123\r\n'
     'Hi 0x00000010!\r\nHi Hi X!!\r\n[]\r\na005b\r\n42\r\nAB\r\n[lit]\r\n[a005b]\r\n[0x00000001]\r\n'
     'Name? Zed\r\nHi Zed!\r\n')
RX['strings'] = {0: 'Zed\r'}

test('len and string comparison', '''
CONSOLE UART0
CONST CR = 13
CONST LF = 10
DIM a AS STRING * 10
DIM b AS STRING * 10
DIM c AS BYTE
DIM r(4) AS BYTE
FUNCTION Hex(v AS WORD) AS STRING * 10
  RETURN HEX$(v)
END FUNCTION
FUNCTION L2(t AS STRING) AS WORD
  RETURN LEN(t)
END FUNCTION
SUB Check(t AS STRING)
  IF t = "yes" THEN PRINT "Y" ELSE PRINT "N"
END SUB
a = "apple" : b = "apricot"
PRINT DEC LEN(a), " ", DEC LEN(b), " ", DEC LEN("xyz"), " ", DEC LEN(a + b + "!"), CR, LF
PRINT DEC (a = "apple"), DEC (a <> "apple"), DEC (a < b), DEC (a > b), DEC (a <= a), DEC (b >= a), CR, LF
IF a = "apple" THEN PRINT "eq" ELSE PRINT "ne"
IF b < a THEN PRINT " lt" ELSE PRINT " ge"
PRINT CR, LF
a = ""
PRINT DEC LEN(a), " ", DEC (a = ""), " ", DEC (a < "a"), CR, LF
b = "ab" : a = "abc"
PRINT DEC (b < a), DEC (a > b), DEC NOT (a = b), CR, LF
IF Hex(10) = "0000000A" THEN PRINT "hex ok"
c = 65
IF c = "A" THEN PRINT " num ok"
IF "A" = c THEN PRINT " rev ok"
PRINT CR, LF
r(0) = 65 : r(1) = 66 : r(2) = 67 : r(3) = 68
PRINT DEC LEN(r), " ", DEC (r = "ABCD"), " ", DEC (r = "ABC"), " ", DEC (r < "ABCDE"), CR, LF
a = CHR$(200) : b = "z"
PRINT DEC (a > b), CR, LF
Check "yes" : Check "no" : a = "yes" : Check a
PRINT " ", DEC L2("four"), " ", DEC L2(Hex(1)), CR, LF
b = "ab"
a = DEC$(LEN(b)) + "!" + DEC$(LEN(b) + LEN("cd"))
PRINT a, CR, LF
WHILE LEN(a) < 8
  a = a + "."
WEND
PRINT a, " ", DEC LEN(CHR$(c) + HEX$(c)), CR, LF
''', '5 7 3 13\r\n101011\r\neq ge\r\n0 1 1\r\n111\r\nhex ok num ok rev ok\r\n4 1 0 1\r\n1\r\nYNY 4 8\r\n2!4\r\n2!4..... 3\r\n')

test('left right mid instr', '''
CONSOLE UART0
CONST CR = 13 : CONST LF = 10
DIM s AS STRING * 16
DIM t AS STRING * 16
DIM n AS WORD
FUNCTION Word2(x AS STRING) AS STRING * 16
  RETURN MID$(x, 7)
END FUNCTION
FUNCTION Tag(x AS STRING) AS STRING * 16
  RETURN "<" + x + ">"
END FUNCTION
FUNCTION Twice(x AS STRING) AS STRING * 16
  RETURN x + x
END FUNCTION
s = "Hello, World"
PRINT LEFT$(s, 5), "|", RIGHT$(s, 5), "|", MID$(s, 8, 3), "|", MID$(s, 8), CR, LF
n = 3
t = LEFT$(s, n) + "-" + RIGHT$(s, n + 1) + "-" + MID$(s, n, n)
PRINT t, CR, LF
PRINT DEC LEN(MID$(s, 8, 3)), " ", DEC LEN(MID$(s, 20)), " ", DEC LEN(LEFT$(s, 100)), " ", DEC LEN(RIGHT$(s, 0)), CR, LF
PRINT "[", MID$(s, 0), "][", MID$(s, 13), "][", RIGHT$(s, 50), "]", CR, LF
IF LEFT$(s, 5) = "Hello" THEN PRINT "left ok"
IF MID$(s, 8, 5) <> "World" THEN PRINT " bad" ELSE PRINT " mid ok"
PRINT CR, LF
PRINT DEC INSTR(s, "World"), " ", DEC INSTR(s, "o"), " ", DEC INSTR(6, s, "o"), " ", DEC INSTR(s, "xyz"), " "
PRINT DEC INSTR(s, ""), " ", DEC INSTR(20, s, "o"), " ", DEC INSTR(n * 2, s, "o"), CR, LF
PRINT DEC INSTR("abcabc", "ca"), " ", DEC INSTR(LEFT$(s, 4), "l"), " ", DEC INSTR(s, MID$(s, 8, 2)), CR, LF
PRINT Word2(s), CR, LF
t = "abc" : t = MID$(t, 2) + t
PRINT t, CR, LF
PRINT LEFT$("abcdef", 3), RIGHT$("abcdef", 2), MID$("abcdef", 2, 3), DEC LEN(MID$("abcdef", 5)), CR, LF
PRINT Tag(LEFT$(s, 4)), Tag(MID$(Word2(s), 2)), CR, LF
t = "bcabc" : t = "x" + t + Twice("ab")
PRINT t, CR, LF
''', 'Hello|World|Wor|World\r\nHel-orld-llo\r\n3 0 12 0\r\n[][][Hello, World]\r\nleft ok mid ok\r\n'
     '8 5 9 0 1 0 9\r\n3 3 8\r\n World\r\nbcabc\r\nabcefbcd2\r\n<Hell><World>\r\nxbcabcabab\r\n')

def val_signed(text):
    from riscbasic import val_text
    v = val_text(text)
    return v - (1 << 32) if v >> 31 else v


VAL_CASES = ['123', '  -42abc', '+7', '&HFF', '0x1f', '&b101', '0B11', 'abc', '', '&', '0', '09', '0x',
             '2147483647', '4294967295', '-0', '  &hDeadBeef', '12 34', '-&H10', '7a']
test('val', '''
CONSOLE UART0
DIM s AS STRING * 16
DIM n AS SWORD
FUNCTION Num(x AS STRING) AS SWORD
  RETURN VAL(x) + 1
END FUNCTION
''' + ''.join('s = "%s" : PRINT DEC VAL(s), " ", DEC VAL("%s"), 13, 10\n' % (c, c) for c in VAL_CASES) + '''
s = "ab12cd"
PRINT DEC VAL(MID$(s, 3, 2)), " ", DEC VAL("55") + 1, " ", DEC Num("41"), 13, 10
n = VAL("-5")
IF n < 0 THEN PRINT "neg", 13, 10
LINE INPUT "> ", s
n = VAL(MID$(s, 3)) * 2
PRINT 13, 10, DEC n, 13, 10
''', ''.join('%d %d\r\n' % (val_signed(c), val_signed(c)) for c in VAL_CASES) +
     '12 56 42\r\nneg\r\n> x=300\r\n600\r\n')
RX['val'] = {0: 'x=300\r'}

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

test('MEM[addr], full 32-bit addresses', '''
DIM a AS WORD
DIM v AS WORD
MEM[&H8100] = &H11223344
v = MEM[&H8100]
PRINT UART0, HEX v, " ", HEX MEM8[&H8101], " ", HEX MEM16[&H8102], " ", HEX MEM32(&H8100), 13, 10
MEM[&HFFFF_FFFC] = &HCAFE0001               ' top word of the address space: lw/sw off zero
MEM[&HFFFF_F000] = 2                        ' lowest address reachable with a 12-bit offset
MEM[&H7FFF_FFFC] = 3                        ' LUI 0x80000 + negative offset
MEM[&H8000_0000] = 4
MEM[&H7FFF_F800] = 5
PRINT UART0, HEX MEM[&HFFFF_FFFC], " ", DEC MEM[&HFFFF_F000], " ", DEC MEM[&H7FFF_FFFC]
PRINT UART0, " ", DEC MEM[&H8000_0000], " ", DEC MEM[&H7FFF_F800], 13, 10
a = &HFFFF_FFFC                             ' address in a variable
MEM[a] = MEM[a] + 1
MEM[a - 4] = 7
PRINT UART0, HEX MEM[a], " ", DEC MEM[a - 4], " ", DEC MEM[-8], 13, 10
MEM[a][15:8] = &HAB                         ' field and bit of a memory word
MEM[a].31 = 0
PRINT UART0, HEX MEM[a], " ", HEX MEM[a][15:8], " ", DEC MEM[a].0, 13, 10
FOR a = &H8000_0000 TO &H8000_000C STEP 4  ' word loop over far memory (signed FOR: stays negative)
  MEM[a] = a
NEXT
PRINT UART0, HEX MEM[&H8000_0008], 13, 10
''', '11223344 33 1122 11223344\r\nCAFE0001 2 3 4 5\r\nCAFE0002 7 7\r\n4AFEAB02 AB 0\r\n80000008\r\n')

test('MEM[addr] fields with computed addresses', '''
DIM p AS WORD IN R13
DIM v AS WORD
p = &H8200
MEM[p] = 0
MEM[p + 4] = &HFFFFFFFF
v = &H12345
MEM[p][23:4] = v                            ' wide field: the mask needs a register
MEM8[p + 3].7 = 1
MEM[p + 4][11:8] = v + 1                    ' computed value and address
MEM16[p + 6][3:0] = 0
IF MEM[p].31 AND NOT MEM[p].0 THEN PRINT UART0, "bits "
PRINT UART0, HEX MEM[p], " ", HEX MEM[p + 4], " ", HEX p, 13, 10
''', 'bits 80123450 FFF0F6FF 00008200\r\n')

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

test('include files', '''
.include "util.bas"
.include "consts.bas"          ' included already by util.bas: skipped
.include "sub/deep.bas"
Say "inc"
PRINT UART0, DEC Twice(21), " ", DEC Plus1(9), CR, LF
''', 'inc\r\n42 10\r\n')


test('conditional compilation', '''
CONST DEBUG = 1
CONST LEVEL = 2 : CONST QUIET = 0       ' CONSTs on a ':' line are seen by #IF too
.include "board.bas"                    ' defines BOARD = 2
#IF DEBUG
PRINT UART0, "d"
#ELSE
PRINT UART0, "x"
#ENDIF
#IF BOARD = 1
.include "does_not_exist.bas"           ' not included: the branch is off
FUNCTION Pick() AS WORD
  RETURN 1
END FUNCTION
#ELSEIF BOARD = 2 AND LEVEL >= 2
FUNCTION Pick() AS WORD                 ' same name as above: only one is compiled
  RETURN 2
END FUNCTION
#ELSEIF BOARD = 2
this is not BASIC, and it is never compiled
#ELSE
FUNCTION Pick() AS WORD
  RETURN 3
END FUNCTION
#END IF
PRINT UART0, DEC Pick()
#IF NOT DEFINED(BAUD)                   ' default value pattern
CONST BAUD = 9600
#ENDIF
#IF DEFINED(BAUD) AND NOT DEFINED(NOPE)
PRINT UART0, " ", DEC BAUD
#ENDIF
#IF QUIET
PRINT UART0, " quiet"
#ELSE
  #IF LEVEL > 1                         ' nested
PRINT UART0, " L", DEC LEVEL
  #ELSE
PRINT UART0, " l"
  #ENDIF
#ENDIF
#IF 0
  #ERROR "not reached"
  #IF UNKNOWN_NAME                      ' not evaluated inside an off branch
  #ELSE
  #ENDIF
#ENDIF
#if debug = 1 and not (level = 2)       ' lower case; NOT of a comparison is logical
PRINT UART0, " wrong"
#endif
PRINT UART0, 13, 10
''', 'd2 9600 L2\r\n')

test('constant NOT of a comparison', '''
CONST A = 1
IF NOT (A = 1) THEN
  PRINT UART0, "wrong"
ELSE
  PRINT UART0, "right"
END IF
R10 = NOT (A = 2)
R11 = NOT A
PRINT UART0, " ", DEC R10, " ", HEX R11, 13, 10
''', 'right 1 FFFFFFFE\r\n')


def make_compiler(name, body):
    c = Compiler(name)
    if '.include' in body.lower():
        c.src_dir = INC
    return c


ERRORS = [
    ('.include "nope.bas"', 'not found'),
    ('#ENDIF', '#ENDIF without #IF'),
    ('MEM[&H8100 = 1', "']' expected"),
    ('MEM(&H8100] = 1', "')' expected"),
    ('DIM mem AS WORD', 'reserved'),
    ('#IF 1' + chr(10) + '#ERROR "stop here"' + chr(10) + '#ENDIF', '#ERROR: stop here'),
    ('#ERROR stop', 'in quotes'),
    ('#ELSE', '#ELSE without #IF'),
    ('#IF 1', '#IF without #ENDIF'),
    ('#IF', 'needs a condition'),
    ('#IF NOPE' + chr(10) + '#ENDIF', 'unknown name'),
    ('#IF R10' + chr(10) + '#ENDIF', 'constant condition'),
    ('#IF 1' + chr(10) + '#ELSE' + chr(10) + '#ELSE' + chr(10) + '#ENDIF', 'second #ELSE'),
    ('#IF 1' + chr(10) + '#ELSE' + chr(10) + '#ELSEIF 1' + chr(10) + '#ENDIF', '#ELSEIF after #ELSE'),
    ('#IF 1' + chr(10) + '#ENDIF 1', "unexpected '1'"),
    ('.include "open_if.bas"' + chr(10) + '#ENDIF', '#IF without #ENDIF'),
    ('CONST K = 1' + chr(10) + 'FUNCTION K() AS WORD' + chr(10) + 'RETURN 1' + chr(10) + 'END FUNCTION',
     'already defined'),
    ('.include nope.bas', 'in quotes'),
    ('.include "loop_a.bas"', 'circular'),
    ('.include "bad.bas"', 'nothing_here'),
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
    ('UNTIL 1', 'no matching REPEAT'),
    ('SELECT CASE R10' + chr(10) + 'CASE 5 TO 1' + chr(10) + 'END SELECT', 'empty'),
    ('SELECT CASE R10' + chr(10) + 'CASE IS 5' + chr(10) + 'END SELECT', 'comparison'),
    ('SELECT CASE R10' + chr(10) + 'CASE IS < R11' + chr(10) + 'END SELECT', 'constant'),
    ('DIM is AS WORD', 'reserved'),
    ('IF 1 THEN : R10 = 1 : END IF', 'no matching IF'),
    ('DIM t AS STRING * 8' + chr(10) + 'R10 = t', 'is a STRING'),
    ('FUNCTION F() AS STRING' + chr(10) + 'END FUNCTION', 'needs a size'),
    ('DIM t AS STRING * 8' + chr(10) + 't = 5', 'text expected'),
    ('SUB S(t AS STRING)' + chr(10) + 't = "x"' + chr(10) + 'END SUB', 'read-only'),
    ('FUNCTION G(t AS STRING) AS STRING * 8' + chr(10) + 'END FUNCTION' + chr(10) + 'CONSOLE UART0' + chr(10) + 'PRINT G(5)', 'text expected'),
    ('FUNCTION H() AS STRING * 8' + chr(10) + 'END FUNCTION' + chr(10) + 'R10 = H()', 'returns a STRING'),
    ('DIM t AS STRING * 1', 'room for at least one'),
    ('DIM t AS STRING * 8' + chr(10) + 'IF t + "x" = "y" THEN R10 = 1', 'single strings'),
    ('FUNCTION F(v AS WORD) AS STRING * 8' + chr(10) + 'END FUNCTION' + chr(10) + 'IF F(1) = F(2) THEN R10 = 1', 'one result buffer'),
    ('R10 = LEN(DEC$(R11))', 'not supported'),
    ('R10 = VAL(DEC$(R11))', 'VAL: build'),
    ('DIM val AS WORD', 'reserved'),
    ('DIM t AS STRING * 8' + chr(10) + 'IF LEFT$(t, 1) = RIGHT$(t, 1) THEN R10 = 1', 'only one side'),
    ('DIM t AS STRING * 8' + chr(10) + 't = LEFT$(MID$(t, 2, 3), 1)', 'nested'),
    ('SUB S(x AS STRING)' + chr(10) + 'IF LEFT$(x, 1) = "a" THEN R10 = 1' + chr(10) + 'END SUB', 'STRING parameter'),
    ('DIM t AS STRING * 8' + chr(10) + 'IF t = DEC$(R11) THEN R10 = 1', 'build CHR$'),
    ('SELECT CASE R10' + chr(10) + 'CASE R11 TO 3' + chr(10) + 'END SELECT', 'constant'),
    ('REPEAT', 'REPEAT block not closed'),
    ('DO' + chr(10) + 'UNTIL 1', 'no matching REPEAT'),
    ('DIM w(4) AS WORD' + chr(10) + 'PRINT UART0, w', 'BYTE array'),
    ('DEVICE D AT 0' + chr(10) + 'REG R AT +0 READONLY WRITEONLY' + chr(10) + 'END DEVICE', 'both'),
    ('DIM CHR AS WORD', 'reserved'),
    ('CONSOLE GPIO', 'TXREADY'),
    ('CONSOLE NOPE', 'not a device'),
    ('CONSOLE UART0' + chr(10) + 'PRINT', 'at least one item'),
    ('CONSOLE UART0' + chr(10) + 'PRINT "a" "b"', 'unexpected'),
]


def run_one(name, body, expect, target, irq_at, check):
    src = PRELUDE.format(target=target) + body
    c = make_compiler(name, body)
    c.compile(src)
    s = Sim(c.binary(), c.em.base, c.ram_base, c.ram_size)
    s.gpio_in = 1
    for uart, text in RX.get(name, {}).items():
        s.rx[uart] = list(text)
    done = s.run(irq_at=irq_at)
    out = s.output()
    ok = done and out == expect and (check is None or check(s))
    return ok, out, done, c


def check_conditionals():
    """-D defines, the listing of #IF lines, and the position of an error in an open #IF."""
    failed = 0
    body = PRELUDE.format(target='RV32I') + '''
#IF DEBUG
PRINT UART0, "debug ", DEC BAUD
#ELSE
PRINT UART0, "release"
#ENDIF
'''
    outs = []
    for defines in ({}, {'DEBUG': '1', 'BAUD': '&H10'}):
        c = Compiler('d.bas')
        c.defines = defines
        try:
            c.compile(body.replace('#IF DEBUG', '#IF DEFINED(DEBUG)'))
        except RBError as e:
            outs.append('error %s' % e.msg)
            continue
        s = Sim(c.binary(), c.em.base, c.ram_base, c.ram_size)
        s.run()
        outs.append(s.output())
    ok = outs == ['release', 'debug 16']
    print('%s -D defines: %s' % ('ok  ' if ok else 'FAIL', outs))
    failed += not ok
    c = Compiler('d.bas')
    c.compile(body.replace('#IF DEBUG', '#IF DEFINED(DEBUG)'))
    lst = c.listing()
    ok = (re.search(r'^; \d{4}  \(off\) PRINT UART0, "debug ", DEC BAUD$', lst, re.M) is not None and
          re.search(r'^; \d{4}  #ELSE$', lst, re.M) is not None and '"release"' in lst)
    print('%s #IF listing: off lines marked' % ('ok  ' if ok else 'FAIL'))
    failed += not ok
    try:
        Compiler('d.bas').compile(PRELUDE.format(target='RV32I') + '#IF 1\nR10 = 1\n#IF 0\n#ENDIF\n')
        where = 'no error'
    except RBError as e:
        where = '%s %s' % (e.line, e.msg)
    ok = where == '%d #IF without #ENDIF' % (PRELUDE.count(chr(10)) + 1)
    print('%s open #IF reported at its line: %s' % ('ok  ' if ok else 'FAIL', where))
    failed += not ok
    return failed


def check_include_positions():
    """Errors in included files name that file and its line; the listing and DWARF rows
    point into the included files."""
    failed = 0
    try:
        make_compiler('err', '.include').compile(PRELUDE.format(target='RV32I') + '.include "bad.bas"')
        where = 'no error'
    except RBError as e:
        where = '%s:%s' % (e.file, e.line)
    ok = where.replace(os.sep, '/').endswith('inc/bad.bas:3')
    print('%s include error position: %s' % ('ok  ' if ok else 'FAIL', where))
    failed += not ok
    body = PRELUDE.format(target='RV32IC') + '.include "util.bas"\nSay "x"\n'
    c = make_compiler('main.bas', body)
    c.compile(body)
    names = [f['name'] for f in c.files]
    files_in_rows = sorted(set(names[f] for _, f, ln in c.line_rows() if ln))
    lst = c.listing()
    ok = (names == ['main.bas', 'util.bas', 'consts.bas'] and files_in_rows == ['main.bas', 'util.bas']
          and '; util.bas:0009  PRINT UART0, t, CR, LF' in lst)
    print('%s include listing and line rows: files %s, rows in %s' % ('ok  ' if ok else 'FAIL', names, files_in_rows))
    failed += not ok
    return failed


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
            make_compiler('err', body).compile(PRELUDE.format(target=target) + body)
            print('FAIL error expected: %s' % body)
            failed += 1
        except RBError as e:
            if frag and frag not in e.msg:
                print('FAIL %-32s wrong message: %s' % (body, e.msg))
                failed += 1
            else:
                print('ok   error: %-26s -> %s' % (body, e.msg))
    failed += check_include_positions()
    failed += check_conditionals()
    print('\n%s' % ('all tests passed' if not failed else '%d test(s) failed' % failed))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
