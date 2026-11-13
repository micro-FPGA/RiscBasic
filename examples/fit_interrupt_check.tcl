# License: MIT
# Copyright 7AG (2026) MicroFPGA UG(h)
# https://github.com/micro-FPGA/RiscBasic
#
# XSDB check for examples/fit_interrupt.bas on the MicroBlaze V MCS: where does the FIT1
# interrupt get lost? Run fit_interrupt.elf first, then in XSDB:
#
#   connect
#   targets                      ;# select the MicroBlaze V hart: targets <n>
#   source fit_interrupt_check.tcl
#   fit_run fit_interrupt.elf    ;# reset, download, start (see below)
#   fit_check fit_interrupt.lst  ;# the listing of the same build (label and variable addresses)
#
# Seen on the MicroBlaze V MCS with XSDB 2025.2: after a single step (stpi, nxt, or the step
# the debugger makes to resume from a breakpoint that is still set, such as a breakpoint at
# main) the core takes no interrupts until the next plain halt and resume (stop, con). A
# program that runs without the debugger is not affected. fit_run therefore ends with one
# plain stop and con. Also reset before every download (rst -system): after dow alone the
# CSRs and the IO Module keep the old program's state, including its interrupt vectors.
#
# The check stops and continues the CPU, acknowledges FIT1 once from the debugger, and sets
# and removes one software breakpoint. Nothing else is changed.

set IOM 0x80000000

proc fc_hex {v} { return [format 0x%08X $v] }

proc fc_rd {addr} { return [lindex [mrd -value $addr] 0] }

# a CSR value, or "" if this XSDB names it differently (then: rrd, and look for it)
proc fc_csr {name} {
    foreach form [list $name "csr $name"] {
        if {![catch {set r [rrd -no-bits {*}$form]}] && [regexp -nocase {:\s*([0-9a-f]{8})} $r -> v]} {
            return [expr 0x$v]
        }
    }
    return ""
}

proc fc_show {name v bits} {
    if {$v eq ""} {
        puts [format "  %-12s (could not read, see 'rrd')" $name]
    } else {
        puts [format "  %-12s %s  %s" $name [fc_hex $v] $bits]
    }
}

# label address from the RiscBasic listing: the line after "   name:"
proc fc_label {lst name} {
    set f [open $lst]
    set lines [split [read $f] "\n"]
    close $f
    set i [lsearch -regexp $lines "^\\s+${name}:\\s*\$"]
    if {$i < 0} { error "label $name not found in $lst" }
    foreach l [lrange $lines [expr $i + 1] end] {
        if {[regexp {^([0-9A-F]{8})\s} $l -> a]} { return [expr 0x$a] }
    }
    error "no address after $name in $lst"
}

proc fit_check {lst {ticks_addr 0x3808}} {   ;# ticks: RAM address in this build (ELF symbol TICKS)
    global IOM
    set trap [fc_label $lst __trap]
    set irq [fc_label $lst __irq11]
    puts "__trap = [fc_hex $trap], ON IRQ MEXT handler = [fc_hex $irq], ticks at [fc_hex $ticks_addr]"

    # ---- 1. snapshot
    if {[string match -nocase "*Stopped*" [state]]} {
        puts "NOTE: the CPU was already stopped. Start the program with 'con' before fit_check."
    }
    catch {stop}
    after 100
    set pc [lindex [regexp -inline -nocase {[0-9a-f]{8}} [rrd pc]] 0]
    set st [fc_rd [expr $IOM + 0x30]]
    set pe [fc_rd [expr $IOM + 0x34]]
    set ms [fc_csr mstatus]
    set ie [fc_csr mie]
    set ip [fc_csr mip]
    set tv [fc_csr mtvec]
    set t1 [fc_rd $ticks_addr]
    puts "\n1. Snapshot (CPU stopped at pc 0x$pc)"
    fc_show IRQ_STATUS $st "bit 7 = FIT1 request latched: [expr ($st >> 7) & 1]"
    fc_show IRQ_PENDING $pe "bit 7 = FIT1 enabled and pending: [expr ($pe >> 7) & 1]"
    fc_show mstatus $ms [expr {$ms eq "" ? "" : "MIE (bit 3) = [expr ($ms >> 3) & 1], MPIE (bit 7) = [expr ($ms >> 7) & 1]"}]
    fc_show mie $ie [expr {$ie eq "" ? "" : "MEIE (bit 11) = [expr ($ie >> 11) & 1]"}]
    fc_show mip $ip [expr {$ip eq "" ? "" : "MEIP (bit 11) = [expr ($ip >> 11) & 1]  (the CPU sees the INTC line)"}]
    fc_show mtvec $tv [expr {$tv eq "" ? "" : ($tv == $trap ? "= __trap, ok" : "NOT __trap")}]
    fc_show ticks $t1 "interrupts counted by the handler"

    # ---- 2. does the FIT set IRQ_STATUS on its own? (the CPU is stopped, the FIT keeps running)
    mwr [expr $IOM + 0x3C] 0x80
    set a [fc_rd [expr $IOM + 0x30]]
    after 50
    set b [fc_rd [expr $IOM + 0x30]]
    puts "\n2. FIT1 hardware: IRQ_STATUS bit 7 after ack = [expr ($a >> 7) & 1], 50 ms later = [expr ($b >> 7) & 1]"
    if {!(($b >> 7) & 1)} {
        puts "   -> FIT1 does not raise its interrupt: in the MCS-V customization check FIT1 'Use FIT'"
        puts "      and 'Generate Interrupt' (FIT1_INTERRUPT), rebuild the bitstream."
        con
        return
    }
    puts "   -> FIT1 works and the INTC latches it."
    if {!(($pe >> 7) & 1)} {
        puts "   -> but IRQ_PENDING bit 7 is 0: IRQ_ENABLE bit 7 was not written. Did the program reach"
        puts "      'IOM.FIT1_ENABLE = 1'? (pc above, UART banner)"
    }
    if {$ip ne "" && !(($ip >> 11) & 1) && (($pe >> 7) & 1)} {
        puts "   -> IRQ_PENDING is set but mip.MEIP = 0: the IO Module does not drive its interrupt line."
        puts "      Its interrupt handshake waits for the CPU to return (mret) from an interrupt it took"
        puts "      earlier, typically after a crash and 'rst -processor'. Reset the whole MCS with"
        puts "      'rst -system' (or the board reset), then dow and con again."
    }

    # ---- 3. does an interrupt arrive at __trap? (IntcInit points the IO Module vectors there)
    set bp [bpadd -addr $trap -type sw]      ;# the MCS-V has one hw breakpoint, often taken
    con
    after 500
    set s [state]
    puts "\n3. Breakpoint on __trap, 0.5 s: $s"
    if {[string match -nocase "*Stopped*" $s]} {
        set mc [fc_csr mcause]
        set me [fc_csr mepc]
        fc_show mcause $mc [expr {$mc eq "" ? "" : ($mc == 0x8000000B ? "machine external interrupt, ok" : "NOT 0x8000000B: the ON IRQ MEXT dispatcher will not run the handler")}]
        fc_show mepc $me "interrupted instruction"
        bpremove $bp
        if {$mc ne "" && $mc == 0x8000000B} {
            puts "   -> Interrupts arrive at __trap with the expected cause. If ticks stays 0, single-step"
            puts "      from here ('nxt' / 'stpi') into the handler at [fc_hex $irq]."
        }
        con
        return
    }
    stop
    bpremove $bp
    set pc2 [lindex [regexp -inline -nocase {[0-9a-f]{8}} [rrd pc]] 0]
    puts "   -> No interrupt reached __trap. CPU at pc 0x$pc2. Please send this output."

    # ---- 4. do the ticks move?
    set t2 [fc_rd $ticks_addr]
    con
    after 500
    stop
    set t3 [fc_rd $ticks_addr]
    puts "\n4. ticks $t2 -> $t3 in 0.5 s"
    con
}

# Live state of the running program, one line each; run it twice a few seconds apart.
# ticks, seconds and ms are three consecutive words in RAM (0x3808 in this build).
proc fit_status {{ticks_addr 0x3808}} {
    global IOM
    catch {stop}
    after 100
    set pc [lindex [regexp -inline -nocase {[0-9a-f]{8}} [rrd pc]] 0]
    set v [mrd -value $ticks_addr 3]
    set st [fc_rd [expr $IOM + 0x30]]
    set pe [fc_rd [expr $IOM + 0x34]]
    set ms [fc_csr mstatus]
    set ip [fc_csr mip]
    set mc [fc_csr mcause]
    set me [fc_csr mepc]
    puts [format "pc 0x%s  ticks %d  seconds %d  ms %d" $pc [lindex $v 0] [lindex $v 1] [lindex $v 2]]
    puts [format "IRQ_STATUS %s  IRQ_PENDING %s  mstatus %s  mip %s  mcause %s  mepc %s" \
              [fc_hex $st] [fc_hex $pe] [fc_opt $ms] [fc_opt $ip] [fc_opt $mc] [fc_opt $me]]
    con
}

# Trace one interrupt: stop at __trap, then single-step up to n instructions and print each pc
# with its line from the listing. Stops early when the pc is back in the interrupted code or
# lands in the startup code (below main).
proc fit_trace {lst {n 150}} {
    set trap [fc_label $lst __trap]
    set main [fc_label $lst main]
    set f [open $lst]
    set code [dict create]
    foreach l [split [read $f] "\n"] {
        if {[regexp {^([0-9A-F]{8})\s+[0-9A-F]{4,8}\s+(.*)$} $l -> a ins]} {
            dict set code [expr 0x$a] [string trim $ins]
        }
    }
    close $f
    catch {stop}
    set bp [bpadd -addr $trap -type sw]
    con
    set stopped 0
    for {set i 0} {$i < 40 && !$stopped} {incr i} {
        after 50
        set stopped [string match -nocase "*Stopped*" [state]]
    }
    bpremove $bp
    if {!$stopped} {
        puts "No interrupt reached __trap within 2 s."
        return
    }
    set me [fc_csr mepc]
    puts "At __trap: mcause [fc_opt [fc_csr mcause]]  mepc [fc_opt $me]  mstatus [fc_opt [fc_csr mstatus]]"
    set trace {}
    for {set i 0} {$i < $n} {incr i} {
        set pc [expr 0x[lindex [regexp -inline -nocase {[0-9a-f]{8}} [rrd pc]] 0]]
        set ins [expr {[dict exists $code $pc] ? [dict get $code $pc] : "?"}]
        lappend trace [format "%3d  %s  %s" $i [fc_hex $pc] $ins]
        if {$i > 0 && $pc < $main} {
            lappend trace "     -> in the startup code: the program restarts here"
            break
        }
        if {$i > 0 && $me ne "" && $pc == $me} {
            lappend trace "     -> back at mepc: the interrupt returned normally"
            break
        }
        stpi
    }
    puts "\n[join $trace "\n"]"
    puts "mcause [fc_opt [fc_csr mcause]]  mepc [fc_opt [fc_csr mepc]]  mstatus [fc_opt [fc_csr mstatus]]  mip [fc_opt [fc_csr mip]]"
}

# Reset the MCS (CPU and IO Module), download, start; continue past breakpoints, then one
# plain stop + con, which re-enables interrupts after any single step.
proc fit_run {elf} {
    catch {stop}
    catch {rst -system}
    after 300
    catch {rst -processor}
    after 200
    catch {stop}
    dow $elf
    for {set k 0} {$k < 4} {incr k} {
        con
        after 300
        if {![string match -nocase "*Stopped*" [state]]} break
        puts "stopped at [rrd pc], continuing"
    }
    catch {stop}
    after 20
    con
    puts "running: $elf"
}

proc fc_opt {v} {
    if {$v eq ""} { return "?" }
    return [fc_hex $v]
}

puts "fit_check loaded. Start:    fit_run fit_interrupt.elf     (reset, download, start)"
puts "                  Check:    fit_check fit_interrupt.lst   (the listing of the loaded build)"
puts "                        or:  fit_status                    (live counters, run it twice)"
puts "                        or:  fit_trace fit_interrupt.lst   (single-step one interrupt)"
