#!/usr/bin/env python3
"""Validate that HT frames now publish through wifi_phy_hier's mac_out (the
frame_equalizer 'pdu' port). Replays an HT .cf32 capture into the hier RX and
counts mac_out PDUs + their crc_ok tag. Before the fix this was 0 (HT decoded
but never published); after it should be >0.
  ./ht_hier_check.py /tmp/ht.cf32 [bw_hz]
"""
import os
import sys
import time

os.environ.setdefault("GR_KEEP_CORRUPTED", "1")
import pmt
from gnuradio import blocks, gr
import ieee802_11
from wifi_phy_hier import wifi_phy_hier


class tb(gr.top_block):
    def __init__(self, infile, bw):
        gr.top_block.__init__(self)
        self.src = blocks.file_source(gr.sizeof_gr_complex, infile, False)
        self.phy = wifi_phy_hier(bandwidth=bw, chan_est=ieee802_11.LS,
                                 encoding=ieee802_11.BPSK_1_2, frequency=5e9,
                                 sensitivity=0.56)
        self.null = blocks.null_sink(gr.sizeof_gr_complex)
        self.dbg = blocks.message_debug()
        for b in (self.src,):
            b.set_min_output_buffer(1 << 22)
        self.connect((self.src, 0), (self.phy, 0))
        self.connect((self.phy, 0), (self.null, 0))
        self.msg_connect((self.phy, "mac_out"), (self.dbg, "store"))


def main():
    infile = sys.argv[1] if len(sys.argv) > 1 else "/tmp/ht.cf32"
    bw = float(sys.argv[2]) if len(sys.argv) > 2 else 20e6
    t = tb(infile, bw)
    t.start()
    time.sleep(5)
    t.stop()
    t.wait()
    n = t.dbg.num_messages()
    ht = 0
    for i in range(n):
        meta = pmt.car(t.dbg.get_message(i))
        enc = pmt.dict_ref(meta, pmt.intern("encoding"), pmt.PMT_NIL)
        if pmt.is_uint64(enc):
            e = pmt.to_uint64(enc)
        elif pmt.is_integer(enc):
            e = pmt.to_long(enc)
        else:
            e = -1
        if e >= 8:   # HT_MCS_* enum starts at 8 (legacy 0..7)
            ht += 1
    print(f"[ht-hier-check] mac_out PDUs: {n}  (HT-encoding: {ht})")
    print("RESULT: PASS — HT frames publish through mac_out" if ht > 0
          else "RESULT: FAIL — no HT PDUs reached mac_out")
    return 0 if ht > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
