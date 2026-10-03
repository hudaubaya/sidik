# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

# System Console script: measure every RO pair of the SIDIK RO-PUF N times
# through the JTAG to Avalon master and save the raw counter values as CSV.
#
#   system-console -cli --script=measure_pairs.tcl out=board3_25C.csv \
#       board=3 temp_c=25 reps=20 [vdd_v=1.10] [pairs=512] [timeout=1048576] \
#       [master=<substring of the service path>] [base=0x0] [mode=pairs|freq]
#       [source=hardware]
#
# Inside an interactive System Console session instead:
#   set argv {out=board3_25C.csv board=3 temp_c=25 reps=20}
#   source measure_pairs.tcl
#
# mode=pairs (default) writes one row per race:
#   board,temp_c,vdd_v,rep,pair,count_a,count_b,status
# count_a / count_b are the COUNT_A / COUNT_B registers (RO cycles of RO
# 2*pair and 2*pair+1), status is the CTRL register after the race
# (2 = done, 6 = done with timeout: counts not valid). Lines starting with
# '#' carry metadata. Every repetition sweeps all pairs in order, and the
# file is flushed after each sweep, so an interrupted run keeps the complete
# sweeps. sw/analyze.py reads this format.
#
# mode=freq: rough RO frequency estimate for the SDC (RO_PERIOD_NS). It
# stops each race after `timeout` 50 MHz cycles (default 50 = 1 us) and
# writes board,temp_c,vdd_v,rep,pair,count_a,count_b,status,window_ns;
# f ~= count / window. Diagnostic only: counts captured on a timeout are
# not guaranteed exact (rtl/ropuf/README.md).
#
# Register map: rtl/ropuf/README.md.

namespace eval sidik {
    variable REG_ID      0x00
    variable REG_PARAMS  0x04
    variable REG_CTRL    0x08
    variable REG_PAIR    0x0C
    variable REG_COUNT_A 0x10
    variable REG_COUNT_B 0x14
    variable REG_DELTA   0x18
    variable REG_TIMEOUT 0x1C
    variable ID_PUF1     0x50554631
    variable CLK_NS      20.0
    variable MAX_POLLS   1000
}

proc sidik::parse_args {argv} {
    set opt [dict create out "" board 0 temp_c nan vdd_v nan reps 20 pairs 0 \
                 timeout "" master "" base 0x0 mode pairs source hardware]
    foreach a $argv {
        if {![regexp {^([a-z_]+)=(.*)$} $a -> k v]} {
            error "argument '$a' is not key=value"
        }
        if {![dict exists $opt $k]} {
            error "unknown argument '$k' (known: [dict keys $opt])"
        }
        dict set opt $k $v
    }
    if {[dict get $opt out] eq ""} { error "out=<file.csv> is required" }
    if {[dict get $opt mode] ni {pairs freq}} { error "mode must be pairs or freq" }
    if {[dict get $opt timeout] eq ""} {
        dict set opt timeout [expr {[dict get $opt mode] eq "freq" ? 50 : 1048576}]
    }
    return $opt
}

# --- Avalon access (System Console master service) --------------------------

proc sidik::rd {m base off {n 1}} {
    set vals {}
    foreach v [master_read_32 $m [expr {$base + $off}] $n] {
        lappend vals [expr {$v & 0xFFFFFFFF}]
    }
    return $vals
}

proc sidik::wr {m base off value} {
    master_write_32 $m [expr {$base + $off}] [format 0x%08X $value]
}

proc sidik::open_master {want base} {
    variable REG_ID
    variable ID_PUF1
    set paths [get_service_paths master]
    if {[llength $paths] == 0} { error "no master service: is the FPGA programmed?" }
    foreach p $paths {
        if {$want ne "" && [string first $want $p] < 0} { continue }
        # The HPS debug access port also shows up as a master; skip it unless
        # it was asked for explicitly.
        if {$want eq "" && [string match -nocase *hps* $p]} { continue }
        set m [claim_service master $p ""]
        if {[catch {lindex [rd $m $base $REG_ID] 0} id] == 0 && $id == $ID_PUF1} {
            return [list $m $p]
        }
        close_service master $m
    }
    error "no master reads ID 0x[format %08X $ID_PUF1] at base $base (paths: $paths)"
}

proc sidik::sign32 {v} {
    expr {$v >= 0x80000000 ? $v - 0x100000000 : $v}
}

# One race on `pair`; returns {count_a count_b status}.
proc sidik::race {m base pair} {
    variable REG_CTRL
    variable REG_PAIR
    variable REG_COUNT_A
    variable REG_COUNT_B
    variable REG_DELTA
    variable MAX_POLLS
    wr $m $base $REG_PAIR $pair
    wr $m $base $REG_CTRL 1
    # One burst read: CTRL, PAIR, COUNT_A, COUNT_B, DELTA. A race takes
    # ~70 us, usually less than one JTAG transaction, so the first read
    # normally already sees BUSY = 0.
    for {set i 0} {$i < $MAX_POLLS} {incr i} {
        lassign [rd $m $base $REG_CTRL 5] ctrl rpair ca cb delta
        if {($ctrl & 1) == 0} { break }
    }
    if {$ctrl & 1} { error "pair $pair: still busy after $MAX_POLLS polls" }
    if {($ctrl & 2) == 0} { error "pair $pair: DONE not set (CTRL=$ctrl)" }
    if {$rpair != $pair} { error "pair $pair: PAIR reads back $rpair" }
    if {[sign32 $delta] != $ca - $cb} {
        error "pair $pair: DELTA [sign32 $delta] != COUNT_A - COUNT_B ($ca - $cb)"
    }
    return [list $ca $cb $ctrl]
}

proc sidik::run {argv} {
    variable REG_PARAMS
    variable REG_TIMEOUT
    variable CLK_NS
    set opt [parse_args $argv]
    set base [expr {[dict get $opt base]}]
    lassign [open_master [dict get $opt master] $base] m path
    set fh ""
    set code [catch {
        set params [lindex [rd $m $base $REG_PARAMS] 0]
        set n_pairs [expr {$params & 0xFFFF}]
        set log2n   [expr {($params >> 16) & 0xFF}]
        set stages  [expr {($params >> 24) & 0xFF}]
        set pairs [dict get $opt pairs]
        if {$pairs <= 0 || $pairs > $n_pairs} { set pairs $n_pairs }
        set timeout [dict get $opt timeout]
        wr $m $base $REG_TIMEOUT $timeout
        set mode [dict get $opt mode]

        set out [dict get $opt out]
        set fh [open $out w]
        puts $fh "# sidik-char raw v1"
        puts $fh "# source=[dict get $opt source]"
        puts $fh "# mode=$mode"
        puts $fh "# date=[clock format [clock seconds] -format %Y-%m-%dT%H:%M:%S%z]"
        puts $fh "# service_path=$path"
        puts $fh "# n_pairs=$n_pairs"
        puts $fh "# log2n=$log2n"
        puts $fh "# ro_stages=$stages"
        puts $fh "# timeout_cycles=$timeout"
        set head "board,temp_c,vdd_v,rep,pair,count_a,count_b,status"
        if {$mode eq "freq"} { append head ",window_ns" }
        puts $fh $head
        flush $fh

        set board [dict get $opt board]
        set temp  [dict get $opt temp_c]
        set vdd   [dict get $opt vdd_v]
        set reps  [dict get $opt reps]
        set window [expr {($timeout + 1) * $CLK_NS}]
        set t0 [clock milliseconds]
        set n_timeout 0
        for {set rep 0} {$rep < $reps} {incr rep} {
            set rows {}
            for {set pair 0} {$pair < $pairs} {incr pair} {
                lassign [race $m $base $pair] ca cb st
                if {$st & 4} { incr n_timeout }
                set row "$board,$temp,$vdd,$rep,$pair,$ca,$cb,$st"
                if {$mode eq "freq"} { append row ",$window" }
                lappend rows $row
            }
            puts $fh [join $rows "\n"]
            flush $fh
            set dt [expr {([clock milliseconds] - $t0) / 1000.0}]
            puts [format "rep %d/%d done (%d pairs, %.1f s elapsed)" \
                      [expr {$rep + 1}] $reps $pairs $dt]
        }
        if {$mode eq "pairs" && $n_timeout > 0} {
            puts "WARNING: $n_timeout races timed out; their counts are not valid"
        }
        puts "wrote $out"
    } msg opts]
    if {$fh ne ""} { close $fh }
    close_service master $m
    return -options $opts $msg
}

if {![info exists ::sidik_no_main]} {
    sidik::run $argv
}
