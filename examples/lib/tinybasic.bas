'
' Tiny BASIC interpreter, written in RiscBasic
'
' Include this file after a CPU file that sets up the console (CONSOLE with Put and Get, or
' DATA / TXREADY / RXDATA / RXREADY) and defines FUNCTION KeyHit AS WORD, which returns
' non-zero when a character is waiting. Optional before the include:
'   CONST PSIZE = 4096        ' bytes for program text and the immediate line
'   CONST ASIZE = 64          ' elements of the array @()
'
' The language:
'   Lines with a number are stored, lines without are run at once. A number alone deletes the line.
'   Variables A-Z and the array @(0) .. @(ASIZE-1), all 32-bit signed integers.
'   Statements, several per line with ':'
'     [LET] v = expr            PRINT / ?  items  ("text", expr; ';' joins, ',' tab)
'     INPUT ["prompt";] v, ...  IF expr [THEN] statements | IF expr THEN line
'     GOTO expr   GOSUB expr   RETURN   FOR v = a TO b [STEP s] ... NEXT [v]
'     POKE addr, value (32-bit store)   DELAY ms   REM / '   END   STOP
'     RUN   LIST [from[-to]]   NEW
'   Operators by precedence: OR, AND, NOT, = <> < > <= >=, + -, * / MOD, unary -
'     comparisons give 1 or 0, NOT gives 1 for 0 and 0 otherwise, AND / OR work on bits
'   Functions: ABS(x)  RND(n) (1..n)  PEEK(addr) (32-bit load)  KEY (key code or 0)  FREE
'   Numbers: decimal or &H hex. Ctrl-C stops a running program or a listing.
'

#IF NOT DEFINED(PSIZE)
CONST PSIZE = 4096
#ENDIF
#IF NOT DEFINED(ASIZE)
CONST ASIZE = 64
#ENDIF

CONST LMAX  = 72                    ' longest input line
CONST PTOP  = PSIZE - LMAX - 4      ' program text ends below this; the immediate line goes above
CONST STMAX = 16                    ' expression stacks
CONST GMAX  = 8                     ' GOSUB depth
CONST FMAX  = 8                     ' FOR depth

' Keywords: the numbers are the positions in kwt
CONST K_LET = 1
CONST K_PRINT = 2
CONST K_IF = 3
CONST K_GOTO = 4
CONST K_GOSUB = 5
CONST K_RETURN = 6
CONST K_FOR = 7
CONST K_NEXT = 8
CONST K_INPUT = 9
CONST K_REM = 10
CONST K_END = 11
CONST K_STOP = 12
CONST K_LIST = 13
CONST K_RUN = 14
CONST K_NEW = 15
CONST K_POKE = 16
CONST K_DELAY = 17
CONST K_THEN = 18
CONST K_TO = 19
CONST K_STEP = 20
CONST K_AND = 21
CONST K_OR = 22
CONST K_MOD = 23
CONST K_NOT = 24
CONST K_ABS = 25
CONST K_RND = 26
CONST K_PEEK = 27
CONST K_KEY = 28
CONST K_FREE = 29

CONST kwt() AS BYTE = "L", "E", "T", 0, "P", "R", "I", "N", "T", 0, "I", "F", 0, "G", "O", "T", "O", 0,
                      "G", "O", "S", "U", "B", 0, "R", "E", "T", "U", "R", "N", 0, "F", "O", "R", 0,
                      "N", "E", "X", "T", 0, "I", "N", "P", "U", "T", 0, "R", "E", "M", 0,
                      "E", "N", "D", 0, "S", "T", "O", "P", 0, "L", "I", "S", "T", 0,
                      "R", "U", "N", 0, "N", "E", "W", 0, "P", "O", "K", "E", 0, "D", "E", "L", "A", "Y", 0,
                      "T", "H", "E", "N", 0, "T", "O", 0, "S", "T", "E", "P", 0, "A", "N", "D", 0,
                      "O", "R", 0, "M", "O", "D", 0, "N", "O", "T", 0, "A", "B", "S", 0,
                      "R", "N", "D", 0, "P", "E", "E", "K", 0, "K", "E", "Y", 0, "F", "R", "E", "E", 0,
                      0

' Expression operators:
'  1 (   2 OR   3 AND   4 =   5 <>   6 <   7 >   8 <=   9 >=   10 +   11 -   12 *   13 /   14 MOD
' 15 unary -   16 NOT   17 ABS   18 RND   19 PEEK   20 @
CONST prec() AS BYTE = 0, 0, 1, 2, 4, 4, 4, 4, 4, 4, 5, 5, 6, 6, 6, 7, 3, 8, 8, 8, 8

' Small variables first: they are reached from GP with one instruction
DIM SavedSP AS WORD                 ' stack pointer of the command loop, for errors
DIM tptr    AS WORD IN S2           ' text pointer into P
DIM curl    AS WORD IN S3           ' current line record in P
DIM pend    AS WORD IN S4           ' end of the program text
DIM kwe     AS WORD                 ' end of the keyword found by Kw()
DIM kbuf    AS WORD                 ' key read by CheckBreak, for KEY
DIM rseed   AS WORD                 ' RND state
DIM gsp     AS WORD                 ' GOSUB stack pointer
DIM fsp     AS WORD                 ' FOR stack pointer
DIM vsp     AS WORD IN S5           ' value stack pointer
DIM osp     AS WORD IN S6           ' operator stack pointer
DIM jmp     AS BYTE                 ' the statement moved tptr: no end-of-statement check
DIM done    AS BYTE                 ' END, NEW: leave Exec
DIM ocol    AS BYTE                 ' PRINT left the cursor inside a line
DIM c       AS BYTE

' Variables of the SUBs and FUNCTIONs (static, so also kept here in reach of GP)
DIM kk AS WORD
DIM kj AS WORD
DIM kn AS WORD
DIM gv AS WORD
DIM gc AS BYTE
DIM bc AS BYTE
DIM kc AS BYTE
DIM op AS WORD IN S9
DIM ax AS SWORD IN S7
DIM ay AS SWORD IN S8
DIM aw AS WORD
DIM ec AS BYTE IN S10
DIM est AS BYTE                   ' 0: operand expected, 1: operator expected
DIM eop AS WORD
DIM ek AS WORD IN S11
DIM epar AS WORD                  ' open parentheses
DIM vc AS BYTE
DIM ri AS SWORD
DIM gi AS WORD
DIM lr AS WORD
DIM pnl AS BYTE
DIM pv AS SWORD
DIM pc AS BYTE
DIM ir AS WORD
DIM iv AS SWORD
DIM fv AS WORD
DIM fl AS SWORD
DIM fs AS SWORD
DIM fi AS WORD
DIM nf AS WORD
DIM nv AS WORD
DIM nx AS SWORD
DIM ngo AS BYTE
DIM la AS WORD
DIM lfr AS WORD
DIM lt AS WORD
DIM ln AS WORD
DIM li AS WORD
DIM rn AS WORD
DIM dms AS SWORD
DIM pa AS WORD
DIM sk AS WORD
DIM sc AS BYTE
DIM xc AS BYTE
DIM ea AS WORD
DIM ed AS WORD
DIM el AS WORD
DIM ei AS WORD

DIM V(26)       AS SWORD            ' A-Z
DIM vs(STMAX)   AS SWORD            ' expression values
DIM os(STMAX)   AS BYTE             ' expression operators
DIM gtp(GMAX)   AS WORD             ' GOSUB return: text pointer and line
DIM gcl(GMAX)   AS WORD
DIM fvar(FMAX)  AS BYTE             ' FOR: variable, limit, step, loop start
DIM flim(FMAX)  AS SWORD
DIM fstp(FMAX)  AS SWORD
DIM ftp(FMAX)   AS WORD
DIM fcl(FMAX)   AS WORD
DIM lbuf(LMAX + 1) AS BYTE          ' line input
DIM AR(ASIZE)   AS SWORD            ' @()
DIM P(PSIZE)    AS BYTE             ' records: line number (2 bytes), record length, text, 0

'
' Error: print the message and go back to the command loop with the stack reset
'
SUB Fail(ec AS WORD)
  IF ocol THEN PRINT 13, 10
  ocol = 0
  PRINT "? "
  SELECT CASE ec
  CASE 1
    PRINT "Syntax error"
  CASE 2
    PRINT "No such line"
  CASE 3
    PRINT "Division by zero"
  CASE 4
    PRINT "Out of memory"
  CASE 5
    PRINT "RETURN without GOSUB"
  CASE 6
    PRINT "NEXT without FOR"
  CASE 7
    PRINT "Too complex"
  CASE 8
    PRINT "Bad index"
  CASE ELSE
    PRINT "Break"
  END SELECT
  IF curl < pend THEN PRINT " in line ", DEC P(curl) OR (P(curl + 1) SHL 8)
  PRINT 13, 10
  ASM
    lw sp, SavedSP
    j L_WARM
  END ASM
END SUB

SUB SkipSp
  WHILE P(tptr) = 32
    tptr = tptr + 1
  WEND
END SUB

SUB SkipLine
  WHILE P(tptr) <> 0
    tptr = tptr + 1
  WEND
END SUB

FUNCTION LineNo(la AS WORD) AS WORD
  RETURN P(la) OR (P(la + 1) SHL 8)
END FUNCTION

'
' Keyword at tptr: its number, and kwe = the position after it; 0 if there is none.
' tptr is not moved, so the caller decides whether to take it.
'
FUNCTION Kw AS WORD
  kk = 0
  kn = 1
  DO
    IF kwt(kk) = 0 THEN RETURN 0
    kj = tptr
    WHILE kwt(kk) <> 0
      IF kwt(kk) <> P(kj) THEN EXIT WHILE
      kk = kk + 1
      kj = kj + 1
    WEND
    IF kwt(kk) = 0 THEN
      kwe = kj
      RETURN kn
    END IF
    WHILE kwt(kk) <> 0
      kk = kk + 1
    WEND
    kk = kk + 1
    kn = kn + 1
  LOOP
END FUNCTION

FUNCTION IsDigit(ic AS BYTE) AS WORD
  IF ic >= "0" AND ic <= "9" THEN RETURN 1
  RETURN 0
END FUNCTION

' Number at tptr: decimal digits, or &H and hex digits
FUNCTION GetNum AS WORD
  gv = 0
  IF P(tptr) = "&" THEN
    tptr = tptr + 1
    IF P(tptr) <> "H" THEN Fail 1
    tptr = tptr + 1
    DO
      gc = P(tptr)
      IF gc >= "0" AND gc <= "9" THEN
        gc = gc - 48
      ELSEIF gc >= "A" AND gc <= "F" THEN
        gc = gc - 55
      ELSE
        EXIT DO
      END IF
      gv = (gv SHL 4) OR gc
      tptr = tptr + 1
    LOOP
  ELSE
    WHILE IsDigit(P(tptr))
      gv = gv * 10 + P(tptr) - 48
      tptr = tptr + 1
    WEND
  END IF
  RETURN gv
END FUNCTION

' Ctrl-C stops the program; any other key is kept for KEY
SUB CheckBreak
  IF KeyHit() THEN
    INPUT CHR bc
    IF bc = 3 THEN Fail 9
    kbuf = bc
  END IF
END SUB

FUNCTION GetKey AS WORD
  IF kbuf <> 0 THEN
    kc = kbuf AND 255
    kbuf = 0
    RETURN kc
  END IF
  IF KeyHit() THEN
    INPUT CHR kc
    RETURN kc
  END IF
  RETURN 0
END FUNCTION

' ---------------------------------------------------------------- expressions

SUB PushV(pv AS SWORD)
  IF vsp >= STMAX THEN Fail 7
  vs(vsp) = pv
  vsp = vsp + 1
END SUB

SUB PushO(po AS WORD)
  IF osp >= STMAX THEN Fail 7
  os(osp) = po
  osp = osp + 1
END SUB

' Apply the top operator to the value stack
SUB Reduce
  osp = osp - 1
  op = os(osp)
  IF op >= 15 THEN                  ' one operand
    ax = vs(vsp - 1)
    SELECT CASE op
    CASE 15
      ax = 0 - ax
    CASE 16
      IF ax = 0 THEN ax = 1 ELSE ax = 0
    CASE 17
      IF ax < 0 THEN ax = 0 - ax
    CASE 18
      IF ax <= 0 THEN Fail 8
      rseed = rseed XOR (rseed SHL 13)
      rseed = rseed XOR (rseed SHR 17)
      rseed = rseed XOR (rseed SHL 5)
      aw = ax
      ax = rseed MOD aw + 1
    CASE 19
      ax = MEM[ax]
    CASE 20
      IF ax < 0 OR ax >= ASIZE THEN Fail 8
      ax = AR(ax)
    END SELECT
    vs(vsp - 1) = ax
    EXIT SUB
  END IF
  vsp = vsp - 1
  ay = vs(vsp)
  ax = vs(vsp - 1)
  SELECT CASE op
  CASE 2
    ax = ax OR ay
  CASE 3
    ax = ax AND ay
  CASE 4 TO 9
    aw = 0
    SELECT CASE op
    CASE 4
      IF ax = ay THEN aw = 1
    CASE 5
      IF ax <> ay THEN aw = 1
    CASE 6
      IF ax < ay THEN aw = 1
    CASE 7
      IF ax > ay THEN aw = 1
    CASE 8
      IF ax <= ay THEN aw = 1
    CASE 9
      IF ax >= ay THEN aw = 1
    END SELECT
    ax = aw
  CASE 10
    ax = ax + ay
  CASE 11
    ax = ax - ay
  CASE 12
    ax = ax * ay
  CASE 13
    IF ay = 0 THEN Fail 3
    ax = ax / ay
  CASE 14
    IF ay = 0 THEN Fail 3
    ax = ax MOD ay
  END SELECT
  vs(vsp - 1) = ax
END SUB

' Function or @: needs '(' next; the function is applied when its ')' closes
SUB OpenFn(fo AS WORD)
  SkipSp
  IF P(tptr) <> "(" THEN Fail 1
  tptr = tptr + 1
  PushO fo
  PushO 1
END SUB

'
' Evaluate the expression at tptr. It ends at the first character that cannot continue it
' (',', ';', ':', THEN, TO, STEP, end of line, a ')' without its '(').
' Operator precedence with two stacks, no recursion: RiscBasic locals are static.
'
FUNCTION Expr AS SWORD
  vsp = 0
  osp = 0
  est = 0
  epar = 0
  DO
    SkipSp
    ec = P(tptr)
    IF est = 0 THEN
      IF IsDigit(ec) OR ec = "&" THEN
        PushV GetNum()
        est = 1
      ELSEIF ec = "(" THEN
        PushO 1
        epar = epar + 1
        tptr = tptr + 1
      ELSEIF ec = "-" THEN
        PushO 15
        tptr = tptr + 1
      ELSEIF ec = "+" THEN
        tptr = tptr + 1
      ELSEIF ec = "@" THEN
        tptr = tptr + 1
        OpenFn 20
        epar = epar + 1
      ELSEIF ec >= "A" AND ec <= "Z" THEN
        ek = Kw()
        IF ek = 0 THEN
          PushV V(ec - 65)
          tptr = tptr + 1
          est = 1
        ELSE
          tptr = kwe
          SELECT CASE ek
          CASE K_NOT
            PushO 16
          CASE K_ABS
            OpenFn 17
            epar = epar + 1
          CASE K_RND
            OpenFn 18
            epar = epar + 1
          CASE K_PEEK
            OpenFn 19
            epar = epar + 1
          CASE K_KEY
            PushV GetKey()
            est = 1
          CASE K_FREE
            PushV PTOP - pend
            est = 1
          CASE ELSE
            Fail 1
          END SELECT
        END IF
      ELSE
        Fail 1
      END IF
    ELSE
      eop = 0
      SELECT CASE ec
      CASE "+"
        eop = 10
      CASE "-"
        eop = 11
      CASE "*"
        eop = 12
      CASE "/"
        eop = 13
      CASE "="
        eop = 4
      CASE "<"
        eop = 6
        IF P(tptr + 1) = "=" THEN eop = 8 : tptr = tptr + 1
        IF P(tptr + 1) = ">" THEN eop = 5 : tptr = tptr + 1
      CASE ">"
        eop = 7
        IF P(tptr + 1) = "=" THEN eop = 9 : tptr = tptr + 1
      CASE ")"
        IF epar = 0 THEN EXIT DO
        WHILE os(osp - 1) <> 1
          Reduce
        WEND
        osp = osp - 1               ' drop the '('
        epar = epar - 1
        IF osp > 0 THEN
          IF os(osp - 1) >= 17 THEN Reduce
        END IF
        eop = 255
      CASE "A", "O", "M"
        ek = Kw()
        IF ek = K_AND THEN eop = 3
        IF ek = K_OR THEN eop = 2
        IF ek = K_MOD THEN eop = 14
        IF eop <> 0 THEN tptr = kwe - 1
      END SELECT
      IF eop = 0 THEN EXIT DO
      tptr = tptr + 1
      IF eop <> 255 THEN
        WHILE osp > 0
          ek = os(osp - 1)
          IF ek = 1 THEN EXIT WHILE
          IF prec(ek) < prec(eop) THEN EXIT WHILE
          Reduce
        WEND
        PushO eop
        est = 0
      END IF
    END IF
  LOOP
  IF est = 0 OR epar <> 0 THEN Fail 1
  WHILE osp > 0
    Reduce
  WEND
  RETURN vs(0)
END FUNCTION

' ---------------------------------------------------------------- statements

SUB Expect(xc AS BYTE)
  SkipSp
  IF P(tptr) <> xc THEN Fail 1
  tptr = tptr + 1
END SUB

FUNCTION GetVar AS WORD
  SkipSp
  vc = P(tptr)
  IF vc < "A" OR vc > "Z" THEN Fail 1
  tptr = tptr + 1
  RETURN vc - 65
END FUNCTION

' Target of LET / INPUT: 0..25 for A-Z, 100 + index for @(index)
FUNCTION GetRef AS WORD
  SkipSp
  IF P(tptr) <> "@" THEN RETURN GetVar()
  tptr = tptr + 1
  Expect "("
  ri = Expr()
  Expect ")"
  IF ri < 0 OR ri >= ASIZE THEN Fail 8
  RETURN 100 + ri
END FUNCTION

SUB SetRef(sr AS WORD, sv AS SWORD)
  IF sr >= 100 THEN
    AR(sr - 100) = sv
  ELSE
    V(sr) = sv
  END IF
END SUB

' Jump to line gn; called right after its number was read, so check the end of the statement
SUB GotoLine(gn AS WORD)
  SkipSp
  IF P(tptr) <> 0 AND P(tptr) <> ":" THEN Fail 1
  gi = 0
  WHILE gi < pend
    IF LineNo(gi) = gn THEN
      curl = gi
      tptr = gi + 3
      jmp = 1
      EXIT SUB
    END IF
    gi = gi + P(gi + 2)
  WEND
  Fail 2
END SUB

SUB DoLet
  lr = GetRef()
  Expect "="
  SetRef lr, Expr()
END SUB

SUB DoPrint
  pnl = 1
  DO
    SkipSp
    pc = P(tptr)
    IF pc = 0 OR pc = ":" THEN EXIT DO
    pnl = 1
    IF pc = 34 THEN
      tptr = tptr + 1
      WHILE P(tptr) <> 34
        IF P(tptr) = 0 THEN Fail 1
        PRINT CHR P(tptr)
        tptr = tptr + 1
      WEND
      tptr = tptr + 1
    ELSE
      pv = Expr()
      PRINT DEC pv
    END IF
    SkipSp
    pc = P(tptr)
    IF pc = ";" THEN
      pnl = 0
      tptr = tptr + 1
    ELSEIF pc = "," THEN
      PRINT 9
      pnl = 0
      tptr = tptr + 1
    ELSEIF pc <> 0 AND pc <> ":" THEN
      Fail 1
    END IF
  LOOP
  IF pnl THEN
    PRINT 13, 10
    ocol = 0
  ELSE
    ocol = 1
  END IF
END SUB

SUB DoInput
  DO
    SkipSp
    IF P(tptr) = 34 THEN              ' prompt
      tptr = tptr + 1
      WHILE P(tptr) <> 34
        IF P(tptr) = 0 THEN Fail 1
        PRINT CHR P(tptr)
        tptr = tptr + 1
      WEND
      tptr = tptr + 1
      SkipSp
      IF P(tptr) = ";" OR P(tptr) = "," THEN tptr = tptr + 1
    END IF
    ir = GetRef()
    LINE INPUT "? ", lbuf
    PRINT 13, 10
    ocol = 0
    SetRef ir, VAL(lbuf)
    SkipSp
    IF P(tptr) <> "," THEN EXIT DO
    tptr = tptr + 1
  LOOP
END SUB

SUB DoIf
  iv = Expr()
  SkipSp
  IF Kw() = K_THEN THEN tptr = kwe
  jmp = 1                           ' the rest of the line runs as statements, or is skipped
  IF iv = 0 THEN
    SkipLine
  ELSE
    SkipSp
    IF IsDigit(P(tptr)) THEN GotoLine GetNum()
  END IF
END SUB

SUB DoFor
  fv = GetVar()
  Expect "="
  V(fv) = Expr()
  SkipSp
  IF Kw() <> K_TO THEN Fail 1
  tptr = kwe
  fl = Expr()
  fs = 1
  SkipSp
  IF Kw() = K_STEP THEN
    tptr = kwe
    fs = Expr()
  END IF
  fi = 0                            ' a new FOR on the same variable ends the old loop
  WHILE fi < fsp
    IF fvar(fi) = fv THEN
      fsp = fi
      EXIT WHILE
    END IF
    fi = fi + 1
  WEND
  IF fsp >= FMAX THEN Fail 7
  fvar(fsp) = fv
  flim(fsp) = fl
  fstp(fsp) = fs
  ftp(fsp) = tptr
  fcl(fsp) = curl
  fsp = fsp + 1
END SUB

SUB DoNext
  IF fsp = 0 THEN Fail 6
  SkipSp
  IF P(tptr) >= "A" AND P(tptr) <= "Z" THEN
    nv = GetVar()
    WHILE fvar(fsp - 1) <> nv
      fsp = fsp - 1
      IF fsp = 0 THEN Fail 6
    WEND
  END IF
  nf = fsp - 1
  nv = fvar(nf)
  nx = V(nv) + fstp(nf)
  V(nv) = nx
  ngo = 0
  IF fstp(nf) >= 0 THEN
    IF nx <= flim(nf) THEN ngo = 1
  ELSE
    IF nx >= flim(nf) THEN ngo = 1
  END IF
  IF ngo THEN
    tptr = ftp(nf)
    curl = fcl(nf)
    jmp = 1
  ELSE
    fsp = nf
  END IF
END SUB

SUB DoList
  lfr = 0
  lt = 65535
  SkipSp
  IF IsDigit(P(tptr)) THEN
    lfr = GetNum()
    lt = lfr
  END IF
  SkipSp
  IF P(tptr) = "-" THEN
    tptr = tptr + 1
    SkipSp
    lt = 65535
    IF IsDigit(P(tptr)) THEN lt = GetNum()
  END IF
  la = 0
  WHILE la < pend
    ln = LineNo(la)
    IF ln >= lfr AND ln <= lt THEN
      PRINT DEC ln, " "
      li = la + 3
      WHILE P(li) <> 0
        PRINT CHR P(li)
        li = li + 1
      WEND
      PRINT 13, 10
      CheckBreak
    END IF
    la = la + P(la + 2)
  WEND
END SUB

SUB DoRun
  FOR rn = 0 TO 25
    V(rn) = 0
  NEXT
  FOR rn = 0 TO ASIZE - 1
    AR(rn) = 0
  NEXT
  gsp = 0
  fsp = 0
  kbuf = 0
  jmp = 1
  IF pend = 0 THEN
    done = 1
  ELSE
    curl = 0
    tptr = 3
  END IF
END SUB

SUB DoDelay
  dms = Expr()
  WHILE dms > 0
    DELAY_MS 1
    CheckBreak
    dms = dms - 1
  WEND
END SUB

SUB DoPoke
  pa = Expr()
  Expect ","
  MEM[pa] = Expr()
END SUB

SUB Statement
  jmp = 0
  sc = P(tptr)
  IF sc = "?" THEN
    tptr = tptr + 1
    DoPrint
    EXIT SUB
  END IF
  IF sc = "'" THEN
    SkipLine
    EXIT SUB
  END IF
  sk = Kw()
  IF sk = 0 THEN
    DoLet
    EXIT SUB
  END IF
  tptr = kwe
  SELECT CASE sk
  CASE K_LET
    DoLet
  CASE K_PRINT
    DoPrint
  CASE K_IF
    DoIf
  CASE K_GOTO
    GotoLine Expr()
  CASE K_GOSUB
    IF gsp >= GMAX THEN Fail 7
    sk = Expr()
    gtp(gsp) = tptr
    gcl(gsp) = curl
    gsp = gsp + 1
    GotoLine sk
  CASE K_RETURN
    IF gsp = 0 THEN Fail 5
    gsp = gsp - 1
    tptr = gtp(gsp)
    curl = gcl(gsp)
  CASE K_FOR
    DoFor
  CASE K_NEXT
    DoNext
  CASE K_INPUT
    DoInput
  CASE K_REM
    SkipLine
  CASE K_END
    done = 1
  CASE K_STOP
    Fail 9
  CASE K_LIST
    DoList
  CASE K_RUN
    DoRun
  CASE K_NEW
    pend = 0
    done = 1
  CASE K_POKE
    DoPoke
  CASE K_DELAY
    DoDelay
  CASE ELSE
    Fail 1
  END SELECT
END SUB

' Run statements from tptr in line curl until the end of the program, the end of the
' immediate line, END or NEW
SUB Exec
  done = 0
  DO
    SkipSp
    xc = P(tptr)
    IF xc = 0 THEN
      curl = curl + P(curl + 2)
      IF curl >= pend THEN EXIT DO
      tptr = curl + 3
      CheckBreak
    ELSEIF xc = ":" THEN
      tptr = tptr + 1
    ELSE
      Statement
      IF done THEN EXIT DO
      IF jmp = 0 THEN
        SkipSp
        xc = P(tptr)
        IF xc <> 0 AND xc <> ":" THEN Fail 1
      ELSE
        CheckBreak                  ' GOTO, NEXT, ... : loops within one line can be stopped too
      END IF
    END IF
  LOOP
END SUB

' Store lbuf(es...) as line en; empty text deletes the line
SUB EditLine(en AS WORD, es AS WORD)
  ea = 0
  WHILE ea < pend
    IF LineNo(ea) >= en THEN EXIT WHILE
    ea = ea + P(ea + 2)
  WEND
  IF ea < pend THEN
    IF LineNo(ea) = en THEN         ' remove the old line
      ed = P(ea + 2)
      ei = ea
      WHILE ei + ed < pend
        P(ei) = P(ei + ed)
        ei = ei + 1
      WEND
      pend = pend - ed
    END IF
  END IF
  IF lbuf(es) = 0 THEN EXIT SUB
  el = 4
  ei = es
  WHILE lbuf(ei) <> 0
    el = el + 1
    ei = ei + 1
  WEND
  IF pend + el > PTOP THEN Fail 4
  ei = pend                         ' make room
  WHILE ei > ea
    ei = ei - 1
    P(ei + el) = P(ei)
  WEND
  pend = pend + el
  P(ea) = en AND 255
  P(ea + 1) = en SHR 8
  P(ea + 2) = el
  ea = ea + 3
  WHILE lbuf(es) <> 0
    P(ea) = lbuf(es)
    ea = ea + 1
    es = es + 1
  WEND
  P(ea) = 0
END SUB

' ---------------------------------------------------------------- command loop

DIM mi AS WORD
DIM mn AS WORD
DIM mq AS BYTE

pend = 0                            ' no program: pend is a register, not cleared at start
rseed = &H92D6_8CA2
PRINT 13, 10, "RiscBasic Tiny BASIC, ", DEC PTOP, " bytes free", 13, 10
ASM
  sw sp, SavedSP
END ASM

warm:
DO
  curl = &HFFFF_FFFF                ' no line number in error messages
  ocol = 0
  gsp = 0                           ' a stopped program leaves no FOR or GOSUB behind
  fsp = 0
  LINE INPUT "> ", lbuf
  PRINT 13, 10
  mq = 0                            ' upper case outside of quotes
  mi = 0
  WHILE lbuf(mi) <> 0
    IF lbuf(mi) = 34 THEN mq = mq XOR 1
    IF mq = 0 AND lbuf(mi) >= "a" AND lbuf(mi) <= "z" THEN lbuf(mi) = lbuf(mi) - 32
    mi = mi + 1
  WEND
  mi = 0
  WHILE lbuf(mi) = 32
    mi = mi + 1
  WEND
  IF IsDigit(lbuf(mi)) THEN
    mn = 0
    WHILE IsDigit(lbuf(mi))
      mn = mn * 10 + lbuf(mi) - 48
      mi = mi + 1
    WEND
    IF mn = 0 OR mn > 65535 THEN Fail 1
    WHILE lbuf(mi) = 32
      mi = mi + 1
    WEND
    EditLine mn, mi
  ELSEIF lbuf(mi) <> 0 THEN
    mn = pend + 3                   ' the immediate line goes after the program
    WHILE lbuf(mi) <> 0
      P(mn) = lbuf(mi)
      mn = mn + 1
      mi = mi + 1
    WEND
    P(mn) = 0
    P(pend + 2) = mn + 1 - pend
    curl = pend
    tptr = pend + 3
    Exec
    IF ocol THEN PRINT 13, 10
    PRINT "OK", 13, 10
  END IF
LOOP
