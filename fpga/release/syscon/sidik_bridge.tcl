# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

# System Console bridge: serves the SIDIK window seen by a JTAG to Avalon
# master on a local TCP port, with the line protocol of sw/sidik_sim.py, so
# that the verifiers run on the host PC over the USB-Blaster II:
#
#   system-console -cli --script=sidik_bridge.tcl [port [base [master_index]]]
#   python3 sw/verifier.py --jtag 127.0.0.1:2540 clone-demo
#   sw/sidik_verifier -j 127.0.0.1:2540 auth -i A -d a.db
#
#   port          TCP port on 127.0.0.1 (default 2540; 0 = any free port)
#   base          address of the window for this master (default 0x0: A at
#                 0x000, B at 0x100 in release_sys and in add_to_ghrd)
#   master_index  index into [get_service_paths master]; by default the
#                 first master that reads the SIDIK MAGIC at base
#
# Protocol (one request per line, hex): R <off> -> <value>; W <off> <value>
# -> OK; anything else -> ERR. Offsets are bytes inside the window. Only
# 127.0.0.1 is served: the bridge gives full register access to both
# instances.

set MAGIC 0x53444B31

set port 2540
set base 0x0
set master_index ""
if {[llength $argv] > 0} { set port [lindex $argv 0] }
if {[llength $argv] > 1} { set base [lindex $argv 1] }
if {[llength $argv] > 2} { set master_index [lindex $argv 2] }

proc rd32 {m addr} {
    return [expr {[lindex [master_read_32 $m $addr 1] 0] & 0xFFFFFFFF}]
}

set paths [get_service_paths master]
if {[llength $paths] == 0} {
    puts stderr "sidik_bridge: no master service (FPGA programmed? USB-Blaster connected?)"
    exit 1
}
set m ""
if {$master_index ne ""} {
    set m [claim_service master [lindex $paths $master_index] sidik_bridge]
} else {
    foreach p $paths {
        set c [claim_service master $p sidik_bridge]
        if {[rd32 $c $base] == $MAGIC} {
            set m $c
            break
        }
        close_service master $c
    }
}
if {$m eq ""} {
    puts stderr "sidik_bridge: no master reads the SIDIK MAGIC at $base"
    exit 1
}

# One request -> one reply line. Any malformed request or master error
# answers ERR; the connection stays usable.
proc is_word {x} {
    return [expr {[string is xdigit -strict $x] && [string length $x] <= 8}]
}

proc request {line} {
    set f [split [string trim $line]]
    foreach x [lrange $f 1 end] {
        if {![is_word $x]} { return ERR }
    }
    if {[lindex $f 0] eq "R" && [llength $f] == 2} {
        scan [lindex $f 1] %x off
        return [format %x [rd32 $::m [expr {$::base + $off}]]]
    }
    if {[lindex $f 0] eq "W" && [llength $f] == 3} {
        scan [lindex $f 1] %x off
        scan [lindex $f 2] %x value
        master_write_32 $::m [expr {$::base + $off}] [format 0x%08x $value]
        return OK
    }
    return ERR
}

proc serve {chan} {
    if {[gets $chan line] < 0} {
        if {[eof $chan]} { close $chan }
        return
    }
    if {[catch {request $line} reply]} {
        puts stderr "sidik_bridge: $reply"
        set reply ERR
    }
    puts $chan $reply
    flush $chan
}

proc accept {chan addr p} {
    fconfigure $chan -buffering line -translation lf
    fileevent $chan readable [list serve $chan]
}

set srv [socket -server accept -myaddr 127.0.0.1 $port]
puts "sidik_bridge: listening on 127.0.0.1:[lindex [fconfigure $srv -sockname] 2], base $base"
flush stdout
vwait forever
