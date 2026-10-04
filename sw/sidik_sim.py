# SPDX-FileCopyrightText: 2026 Universitas Sriwijaya
# SPDX-License-Identifier: GPL-3.0-or-later

"""Register-level stand-in for rtl/sidik_avmm.v, built on model/ro_puf.py.

Used to run sw/verifier.py and sw/sidik_verifier.c without hardware:

    python3 sw/sidik_sim.py --socket /tmp/sidik.sock     # serve A and B

Each SimDevice is one virtual chip (model.Chip, its own seed) behind the
release register map (sw/sidik_regs.py). Commands complete instantly (BUSY
is never seen). Behaviour follows the RTL:
  - ENROLL: model enrollment at TAU; HELPER = pair mask, syndromes, KCV;
    ID = HMAC(K, "SIDIK-ID"); K kept (K_READY).
  - RECONSTRUCT: model reconstruction from the HELPER registers (majority
    of 3, SECDED, re-measurement, KCV); failure -> RECON_FAIL, no K.
  - AUTH: RESP = HMAC(K, CHAL), ERR without K.
  - CLEAR / tamper: every register to 0 (tamper: TAMPERED stays, commands
    are ignored).
This is a model of the device, not the device: the RTL itself is checked
against the same verifier in tb/sidik_system.

Socket protocol (UNIX stream socket, one request per line, hex numbers):
    R <offset>            -> <value>
    W <offset> <value>    -> OK
    T <offset>            -> OK      (tamper the instance at <offset>)
<offset> is a byte offset in the SIDIK window: instance A at 0x000, B at 0x100.
"""

import argparse
import hashlib
import hmac
import os
import socket
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "model"))
import ro_puf as rp  # noqa: E402

import sidik_regs as R  # noqa: E402


def words_from_bytes(b):
    return [int.from_bytes(b[i:i + 4], "big") for i in range(0, len(b), 4)]


def bytes_from_words(ws):
    return b"".join(w.to_bytes(4, "big") for w in ws)


class SimDevice:
    """One SIDIK instance (one virtual chip)."""

    def __init__(self, seed, temp_c=25.0, params=None):
        self.params = params or rp.PufParams()
        self.chip = rp.Chip(self.params, np.random.default_rng([seed, 0]))
        self.rng = np.random.default_rng([seed, 1])
        self.temp_c = temp_c
        self.tampered = False
        self.reset()

    def reset(self):
        """Power-on / rst: also clears TAMPERED."""
        self.tampered = False
        self._erase()

    def _erase(self):
        self.tau = 0
        self.helper = [0] * R.N_HELPER_WORDS
        self.chal = [0] * 8
        self.resp = [0] * 8
        self.id = [0] * 8
        self.k = None
        self.done = self.err = self.rfail = False

    def tamper(self):
        self.tampered = True
        self._erase()

    # ---- bus ----
    def read(self, word):
        if word == R.W_MAGIC:
            return R.MAGIC_VALUE
        if word == R.W_STATUS:
            return (R.DONE * self.done | R.ERR * self.err | R.K_READY * (self.k is not None)
                    | R.RECON_FAIL * self.rfail | R.TAMPERED * self.tampered)
        if word == R.W_TAU:
            return self.tau
        if R.W_HELPER <= word < R.W_HELPER + R.N_HELPER_WORDS:
            return self.helper[word - R.W_HELPER]
        for base, regs in ((R.W_CHAL, self.chal), (R.W_RESP, self.resp), (R.W_ID, self.id)):
            if base <= word < base + 8:
                return regs[word - base]
        return 0

    def write(self, word, value):
        if self.tampered:
            return
        value &= 0xFFFFFFFF
        if word == R.W_TAU:
            self.tau = value & 0xFFFF
        elif R.W_HELPER <= word < R.W_HELPER + R.N_HELPER_WORDS:
            self.helper[word - R.W_HELPER] = value
        elif R.W_CHAL <= word < R.W_CHAL + 8:
            self.chal[word - R.W_CHAL] = value
        elif word == R.W_CTRL:
            if value & R.CLEAR:
                self._erase()
            elif value & R.ENROLL:
                self._enroll()
            elif value & R.RECONSTRUCT:
                self._reconstruct()
            elif value & R.AUTH:
                self._auth()

    # ---- commands ----
    def _keygen(self):
        return rp.KeyGenParams(tau=float(self.tau))

    def _set_key(self, key_bits):
        self.k = rp.derive_key(key_bits)
        self.id = words_from_bytes(rp.device_id(self.k))

    def _enroll(self):
        self.done = self.err = self.rfail = False
        self.k, self.id, self.resp = None, [0] * 8, [0] * 8
        self.helper[17] = 0
        e = rp.enroll(self.chip, self._keygen(), self.rng)
        if not e.ok:
            self.err = self.done = True
            return
        mask = 0
        for p in e.helper.pairs:
            mask |= 1 << int(p)
        self.helper = ([(mask >> (32 * i)) & 0xFFFFFFFF for i in range(16)]
                       + [sum(int(s) << (8 * b) for b, s in enumerate(e.helper.syndromes)),
                          int.from_bytes(e.helper.kcv, "big")])
        self._set_key(e.key_bits)
        self.done = True

    def _reconstruct(self):
        self.done = self.err = self.rfail = False
        self.k, self.id, self.resp = None, [0] * 8, [0] * 8
        mask = sum(w << (32 * i) for i, w in enumerate(self.helper[:16]))
        pairs = [p for p in range(512) if mask >> p & 1]
        kp = self._keygen()
        if len(pairs) != kp.n_key_bits:
            self.rfail = self.done = True
            return
        syn = np.array([(self.helper[16] >> (8 * b)) & 0xFF for b in range(kp.n_blocks)])
        helper = rp.HelperData(np.array(pairs), syn, self.helper[17].to_bytes(4, "big"))
        r = rp.reconstruct(self.chip, helper, [self.temp_c], kp, self.rng)
        if r.failed[0]:
            self.rfail = True
        else:
            self._set_key(r.key_bits[0])
        self.done = True

    def _auth(self):
        self.done, self.err = True, self.k is None
        self.resp = ([0] * 8 if self.k is None else
                     words_from_bytes(hmac.new(self.k, bytes_from_words(self.chal),
                                               hashlib.sha256).digest()))


class SimBus:
    """The SIDIK window of fpga/release: instance A at 0x000, B at 0x100."""

    def __init__(self, seed_a=1, seed_b=2):
        self.devices = {R.INSTANCE_OFFSET["A"]: SimDevice(seed_a),
                        R.INSTANCE_OFFSET["B"]: SimDevice(seed_b)}

    def _dev(self, offset):
        base = offset & ~(R.WINDOW_BYTES - 1)
        return self.devices.get(base), (offset - base) // 4

    def read32(self, offset):
        dev, word = self._dev(offset)
        return dev.read(word) if dev else 0

    def write32(self, offset, value):
        dev, word = self._dev(offset)
        if dev:
            dev.write(word, value)

    def tamper(self, offset):
        dev, _ = self._dev(offset)
        dev.tamper()

    def pause(self):
        pass


def serve(bus, path, connections=None, ready=None):
    """Serve `bus` on a UNIX socket until a client sends Q, or after
    `connections` clients. `ready()` is called once the socket listens.
    `bus` needs read32 / write32 (tamper is optional), so any transport of
    sw/verifier.py can be served, e.g. the RTL in tb/sidik_system."""
    if os.path.exists(path):
        os.unlink(path)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    srv.listen(4)
    if ready:
        ready()
    else:
        print(f"sidik_sim: serving A at 0x000, B at 0x100 on {path}", flush=True)
    try:
        while connections is None or connections > 0:
            if connections is not None:
                connections -= 1
            conn, _ = srv.accept()
            with conn, conn.makefile("rw") as f:
                for line in f:
                    parts = line.split()
                    if not parts:
                        continue
                    if parts[0] == "Q":
                        return
                    if parts[0] == "R":
                        f.write(f"{bus.read32(int(parts[1], 16)):x}\n")
                    elif parts[0] == "W":
                        bus.write32(int(parts[1], 16), int(parts[2], 16))
                        f.write("OK\n")
                    elif parts[0] == "T" and hasattr(bus, "tamper"):
                        bus.tamper(int(parts[1], 16))
                        f.write("OK\n")
                    else:
                        f.write("ERR\n")
                    f.flush()
    finally:
        srv.close()
        if os.path.exists(path):
            os.unlink(path)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--socket", required=True, help="UNIX socket path")
    ap.add_argument("--seed-a", type=int, default=1)
    ap.add_argument("--seed-b", type=int, default=2)
    args = ap.parse_args()
    serve(SimBus(args.seed_a, args.seed_b), args.socket)


if __name__ == "__main__":
    main()
