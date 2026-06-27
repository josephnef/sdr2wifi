#!/usr/bin/env python3
"""Replay a recorded .cf32 offline through the fork RX (no real-time / no USRP
overflow) and measure, for GENUINELY-corrupt HT frames, how much SBI-salvageable
structure survives the Viterbi decode. Run once hard, once soft (GR_SOFT_VITERBI)
to test the soft-decision premise over real air:

  GR_SOFT_VITERBI unset -> hard   |   GR_SOFT_VITERBI=1 -> soft

  ./iq_fec_diag.py /tmp/cap_mcs7m.cf32

Drops legacy "ghost" PDUs (the HT L-SIG cover that decode_mac mis-decodes); only
encoding>=HT frames reach the SBI receiver, so the corrupt metric is real.
"""
import os
import sys
import time

os.environ.setdefault("GR_KEEP_CORRUPTED", "1")
import pmt
from gnuradio import blocks, gr
import ieee802_11
from wifi_phy_hier import wifi_phy_hier

sys.path.insert(0, os.path.expanduser("~/git/devourer/tools/precoder"))
from fused_fec_link import FusedFecReceiver
from stream_fec import FecConfig

CANONICAL_SA = [0x57, 0x42, 0x75, 0x05, 0xd6, 0x00]
MAC_HDR = 24


class tb(gr.top_block):
    def __init__(self, infile, bw):
        gr.top_block.__init__(self)
        self.src = blocks.file_source(gr.sizeof_gr_complex, infile, False)
        self.phy = wifi_phy_hier(bandwidth=bw, chan_est=ieee802_11.LS,
                                 encoding=ieee802_11.BPSK_1_2, frequency=2.437e9,
                                 sensitivity=0.56)
        self.null = blocks.null_sink(gr.sizeof_gr_complex)
        self.dbg = blocks.message_debug()
        self.src.set_min_output_buffer(1 << 22)
        self.connect((self.src, 0), (self.phy, 0))
        self.connect((self.phy, 0), (self.null, 0))
        self.msg_connect((self.phy, "mac_out"), (self.dbg, "store"))


def geti(meta, key, default=0):
    v = pmt.dict_ref(meta, pmt.intern(key), pmt.PMT_NIL)
    if pmt.is_uint64(v):
        return pmt.to_uint64(v)
    if pmt.is_integer(v):
        return pmt.to_long(v)
    return default


def getf(meta, key, default=0.0):
    v = pmt.dict_ref(meta, pmt.intern(key), pmt.PMT_NIL)
    return pmt.to_double(v) if pmt.is_real(v) else default


def main():
    infile = sys.argv[1] if len(sys.argv) > 1 else "/tmp/cap_mcs7m.cf32"
    bw = float(sys.argv[2]) if len(sys.argv) > 2 else 20e6
    soft = bool(os.environ.get("GR_SOFT_VITERBI"))

    t = tb(infile, bw)
    t.start()
    # Run until the message count stops growing (file_source hits EOF). The soft
    # decoder is slower, so a fixed sleep undercounts it; poll to completion for a
    # fair hard-vs-soft compare.
    last, stable = -1, 0
    for _ in range(120):
        time.sleep(1.0)
        cur = t.dbg.num_messages()
        if cur == last:
            stable += 1
            if stable >= 4:
                break
        else:
            stable = 0
            last = cur
    t.stop()
    t.wait()

    cfg = FecConfig(k=8, symbol_size=32, overhead=0.25, scheme="rs")
    rcv = FusedFecReceiver(cfg, 10)

    n = t.dbg.num_messages()
    ht_pass = ht_fail = ghosts = 0
    snr_pass, snr_fail = [], []
    for i in range(n):
        msg = t.dbg.get_message(i)
        meta, blob = pmt.car(msg), pmt.cdr(msg)
        if not pmt.is_u8vector(blob):
            continue
        if geti(meta, "encoding") < 8:          # legacy ghost / ambient legacy
            ghosts += 1
            continue
        data = list(pmt.u8vector_elements(blob))
        if len(data) < MAC_HDR + 8:
            continue
        ok = pmt.to_bool(pmt.dict_ref(meta, pmt.intern("crc_ok"), pmt.PMT_NIL))
        snr = getf(meta, "snr")
        if ok:
            ht_pass += 1
            snr_pass.append(snr)
        else:
            ht_fail += 1
            snr_fail.append(snr)
        rcv.add_frame(bytes(data[MAC_HDR:]), crc_err=not ok)

    r = rcv.report()

    def med(xs):
        return sorted(xs)[len(xs) // 2] if xs else float("nan")

    mode = "SOFT" if soft else "HARD"
    print(f"[fec-diag {mode}] HT pass={ht_pass} HT fail={ht_fail} "
          f"(legacy ghosts dropped={ghosts})")
    print(f"[fec-diag {mode}] HT SNR med pass={med(snr_pass):.1f}dB "
          f"fail={med(snr_fail):.1f}dB")
    print(f"[fec-diag {mode}] corrupt-HT sub-blocks total={r.subblocks_total} "
          f"SBI-salvaged={r.subblocks_salvaged}")
    print(f"[fec-diag {mode}] base blocks={r.base_blocks} pkts={r.base_packets} | "
          f"sbi blocks={r.sbi_blocks} pkts={r.sbi_packets} | "
          f"GAIN={r.sbi_blocks - r.base_blocks}")
    # headline for the hard-vs-soft compare: salvaged sub-blocks per corrupt frame
    per = (r.subblocks_salvaged / ht_fail) if ht_fail else 0.0
    print(f"RESULT {mode} salvaged_per_corrupt={per:.3f} "
          f"sbi_pkts={r.sbi_packets} base_pkts={r.base_packets}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
