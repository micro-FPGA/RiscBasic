ALIAS LED  = GPIOA.OUT.0
ALIAS BUTTON = GPIOA.IN.1

'
' Add board init here, all register setup!
'
SUB BoardInit
  MEM[&H4100_0004] = 321
  MEM[&H4100_0008] = 123
END SUB

