'
' LiteX SoC with the VexRiscv CPU: interrupts
'
' All interrupts of a LiteX SoC arrive as the machine external interrupt:
' one handler, ON IRQ MEXT, that tests the EV_PENDING register of each module.
' VexRiscv has one enable bit per SoC interrupt in its own CSR 0xBC0; the
' numbers are the CONST IRQ_<module> of include/CPU/LITEX.bas.
'
'   TIMER0.EV_ENABLE = 1         ' the module raises its interrupt
'   IrqEnable IRQ_TIMER0         ' VexRiscv lets it through
'   IrqOn                        ' interrupts on
'

SUB IrqEnable(n AS WORD)         ' enable SoC interrupt n
  ASM
    li    t0, 1
    sll   t0, t0, a0
    csrrs zero, 0xBC0, t0
  END ASM
END SUB

SUB IrqDisable(n AS WORD)        ' disable SoC interrupt n
  ASM
    li    t0, 1
    sll   t0, t0, a0
    csrrc zero, 0xBC0, t0
  END ASM
END SUB

'
' Machine external interrupt on (mie.MEIE, bit 11), then global interrupts on (mstatus.MIE).
'
SUB IrqOn
  ASM
    li     t0, 0x800
    csrrs  zero, mie, t0
    csrrsi zero, mstatus, 8
  END ASM
END SUB

SUB IrqOff
  ASM
    csrrci zero, mstatus, 8
  END ASM
END SUB
