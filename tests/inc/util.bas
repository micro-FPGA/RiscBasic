' helpers for the include tests
.include "consts.bas"

FUNCTION Twice(x AS WORD) AS WORD
  RETURN x * 2
END FUNCTION

SUB Say(t AS STRING)
  PRINT UART0, t, CR, LF
END SUB
