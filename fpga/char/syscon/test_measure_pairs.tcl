# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

# Runs measure_pairs.tcl under plain tclsh against a mock of the System
# Console master service that emulates the RO-PUF register map
# (rtl/ropuf/README.md). Checks the register protocol and the CSV format,
# not the hardware.
#
#   tclsh test_measure_pairs.tcl <out-dir>
# Writes <out-dir>/mock_pairs.csv and <out-dir>/mock_freq.csv; exits 1 on
# failure. fpga/char/test_quartus.py runs it and parses the CSVs with
# sw/analyze.py.

set here [file dirname [file normalize [info script]]]
set outdir [lindex $argv 0]
if {$outdir eq ""} { set outdir [pwd] }

namespace eval mock {
    variable regs
    variable busy_reads 0
    variable log {}
    variable N_PAIRS 6
    variable LOG2N 14
    variable claimed 0
}

proc mock::reset {} {
    variable regs
    array set regs {pair 0 timeout 1048576 ctrl 0 ca 0 cb 0}
}

# Deterministic counts: winner at 2^14, loser short by a pair/rep dependent
# amount; pair 2 is a tie (dead zone); odd pairs have RO b faster.
proc mock::counts {pair timeout} {
    variable LOG2N
    variable regs
    set n [expr {1 << $LOG2N}]
    if {$timeout < 1000} {
        # timed-out race: both counts proportional to the window
        set c [expr {($timeout + 1) * 5}]
        return [list $c [expr {$c - 2}] 6]
    }
    set d [expr {(13 * $pair + 7) % 40 + 2 * ($regs(rep) % 3)}]
    if {$pair == 2} { set d 0 }
    if {$pair % 2} { return [list [expr {$n - $d}] $n 2] }
    return [list $n [expr {$n - $d}] 2]
}

proc get_service_paths {type} {
    return [list "/devices/SOCVHPS@1#USB-1/hps/master" \
                "/devices/5CSEBA6@2#USB-1/(link)/JTAG/(110:132 v1 #0)/phy_0/master"]
}

proc claim_service {type path lib} {
    if {[string match *hps* $path]} { error "test: HPS master must not be claimed" }
    incr mock::claimed
    return "claim0"
}

proc close_service {type m} {
    incr mock::claimed -1
}

proc master_write_32 {m addr value} {
    variable mock::regs
    lappend mock::log [list W $addr $value]
    switch -- [expr {$addr}] {
        12 { set mock::regs(pair) [expr {$value}] }
        28 { set mock::regs(timeout) [expr {$value}] }
        8 {
            if {[expr {$value}] & 1} {
                lassign [mock::counts $mock::regs(pair) $mock::regs(timeout)] \
                    mock::regs(ca) mock::regs(cb) mock::regs(ctrl)
                # report BUSY for the next two reads
                set mock::busy_reads 2
            }
        }
    }
}

proc master_read_32 {m addr n} {
    set out {}
    for {set i 0} {$i < $n} {incr i} {
        set a [expr {$addr + 4 * $i}]
        switch -- $a {
            0 { set v 0x50554631 }
            4 { set v [expr {(5 << 24) | ($mock::LOG2N << 16) | $mock::N_PAIRS}] }
            8 {
                if {$mock::busy_reads > 0} {
                    incr mock::busy_reads -1
                    set v 1
                } else {
                    set v $mock::regs(ctrl)
                }
            }
            12 { set v $mock::regs(pair) }
            16 { set v $mock::regs(ca) }
            20 { set v $mock::regs(cb) }
            24 { set v [expr {($mock::regs(ca) - $mock::regs(cb)) & 0xFFFFFFFF}] }
            28 { set v $mock::regs(timeout) }
            default { error "read outside the register map: $a" }
        }
        lappend out [format 0x%08X $v]
    }
    return $out
}

proc fail {msg} {
    puts stderr "FAIL: $msg"
    exit 1
}

set ::sidik_no_main 1
source [file join $here measure_pairs.tcl]

# Track the repetition for the mock's counts.
rename sidik::race sidik::race_orig
proc sidik::race {m base pair} {
    if {$pair == 0} { incr mock::regs(rep) }
    return [sidik::race_orig $m $base $pair]
}

# --- pairs mode -----------------------------------------------------------
mock::reset
set mock::regs(rep) -1
set csv [file join $outdir mock_pairs.csv]
sidik::run [list out=$csv board=7 temp_c=25 vdd_v=1.1 reps=4 source=mock]
if {$mock::claimed != 0} { fail "service not released" }

set fh [open $csv]
set lines [split [string trim [read $fh]] "\n"]
close $fh
set meta [lsearch -all -inline $lines {#*}]
set data [lsearch -all -inline -not $lines {#*}]
if {[lindex $data 0] ne "board,temp_c,vdd_v,rep,pair,count_a,count_b,status"} {
    fail "header: [lindex $data 0]"
}
if {[llength $data] != 1 + 4 * $mock::N_PAIRS} { fail "rows: [llength $data]" }
if {[lsearch $meta "# source=mock"] < 0} { fail "meta: $meta" }
if {[lsearch $meta "# n_pairs=$mock::N_PAIRS"] < 0} { fail "meta n_pairs" }
set row [split [lindex $data [expr {1 + $mock::N_PAIRS + 3}]] ,]
if {$row ne {7 25 1.1 1 3 16376 16384 2}} { fail "row rep 1 pair 3: $row" }

# Every race writes PAIR then CTRL=1 for pairs 0..N-1 in order.
set writes {}
foreach e $mock::log {
    lassign $e op addr value
    if {$addr == 0x0C} { lappend writes [expr {$value}] }
}
set want {}
for {set r 0} {$r < 4} {incr r} {
    for {set p 0} {$p < $mock::N_PAIRS} {incr p} { lappend want $p }
}
if {$writes ne $want} { fail "pair order: $writes" }

# --- freq mode ------------------------------------------------------------
mock::reset
set mock::regs(rep) -1
set mock::log {}
set csv [file join $outdir mock_freq.csv]
sidik::run [list out=$csv board=7 temp_c=25 reps=1 mode=freq source=mock]
set fh [open $csv]
set data [lsearch -all -inline -not [split [string trim [read $fh]] "\n"] {#*}]
close $fh
if {[lindex $data 0] ne "board,temp_c,vdd_v,rep,pair,count_a,count_b,status,window_ns"} {
    fail "freq header"
}
if {[lindex $data 1] ne "7,25,nan,0,0,255,253,6,1020.0"} { fail "freq row: [lindex $data 1]" }

# --- argument errors --------------------------------------------------------
if {![catch {sidik::parse_args {reps=3}}]} { fail "missing out= accepted" }
if {![catch {sidik::parse_args {out=x.csv bogus=1}}]} { fail "unknown key accepted" }

puts "OK measure_pairs.tcl against the register mock"
