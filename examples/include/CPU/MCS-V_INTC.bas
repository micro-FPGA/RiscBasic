'
' AMD MicroBlaze V MCS: interrupt controller (INTC) of the IO Module
'
' Register offsets and interrupt numbers from the IO Module driver
' (embeddedsw iomodule_v2_19, xiomodule_l.h, Vitis 2025.2).
' Include after include/CPU/MCS-V.bas.
'

CONST IOM_BASE = &H8000_0000

DEVICE IOM AT &H8000_0000
  REG MODE    AT +&H0C WRITEONLY    ' IRQ_MODE: per interrupt 1 = fast (vectored), 0 = normal
  REG STATUS  AT +&H30 READONLY     ' IRQ_STATUS: latched interrupt requests
  REG PENDING AT +&H34 READONLY     ' IRQ_PENDING: STATUS AND ENABLE
  REG ENABLE  AT +&H38 WRITEONLY    ' IRQ_ENABLE: cannot be read back, RiscBasic keeps a shadow
  REG ACK     AT +&H3C              ' IRQ_ACK: write a mask of 1s to clear STATUS bits.
                                    ' Write whole masks only: ACK cannot be read, so a bit
                                    ' assignment (read-modify-write) would ack random bits.
  BIT FIT1_PENDING = PENDING.7
  BIT FIT1_ENABLE  = ENABLE.7
  BIT PIT1_PENDING = PENDING.3
  BIT PIT1_ENABLE  = ENABLE.3
END DEVICE

' interrupt numbers (bit positions in STATUS, PENDING, ENABLE, ACK, MODE)
CONST IRQ_UART_ERR = 0
CONST IRQ_UART_TX  = 1
CONST IRQ_UART_RX  = 2
CONST IRQ_PIT1     = 3              ' PIT2..PIT4 = 4..6
CONST IRQ_FIT1     = 7              ' FIT2..FIT4 = 8..10
CONST IRQ_GPI1     = 11             ' GPI2..GPI4 = 12..14
CONST IRQ_EXT0     = 16             ' INTC_Interrupt inputs from bit 16 up

CONST FIT1_MASK = 1 SHL IRQ_FIT1

'
' Put the INTC into a known state: all interrupts off and in normal mode, everything
' acknowledged, and the interrupt vectors pointing at the RiscBasic interrupt dispatcher.
'
' Vectors (IRQ_VECTOR, one word per interrupt from offset &H80): the MCS-V builds the IO
' Module with fast interrupt support and runs MicroBlaze V with low-latency interrupts, where
' the IO Module supplies a handler address with each interrupt. Every vector gets the address
' of __trap, the dispatcher that runs the ON IRQ blocks; the startup code sets mtvec to it
' too, so the interrupt reaches __trap whichever of the two the core uses.
' On this core mret is a no-op; the RiscBasic dispatcher returns without it (spec section 7).
'
SUB IntcInit
  IOM.ENABLE = 0
  IOM.MODE = 0
  IOM.ACK = &HFFFF_FFFF
  ASM
    la   t0, __trap
    lui  t1, 0x80000
    sw   t0, 0x80(t1)
    sw   t0, 0x84(t1)
    sw   t0, 0x88(t1)
    sw   t0, 0x8C(t1)
    sw   t0, 0x90(t1)
    sw   t0, 0x94(t1)
    sw   t0, 0x98(t1)
    sw   t0, 0x9C(t1)
    sw   t0, 0xA0(t1)
    sw   t0, 0xA4(t1)
    sw   t0, 0xA8(t1)
    sw   t0, 0xAC(t1)
    sw   t0, 0xB0(t1)
    sw   t0, 0xB4(t1)
    sw   t0, 0xB8(t1)
    sw   t0, 0xBC(t1)
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
