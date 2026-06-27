#!/usr/bin/env python3
"""Replay a capture and emit the per-corrupt-frame SUB-BLOCK SURVIVOR histogram
(how many of a corrupt frame's sub-blocks still pass their CRC) plus the
corrupt-frame rate. This is the channel statistic the SBI-vs-plain-FEC A/B needs:
SBI recovers a frame's block only if survivors >= K, so the full distribution
matters, not just the mean.

  GR_SOFT_VITERBI unset|1   ./survivor_hist.py /tmp/cap_mcs7w.cf32
Prints a Python-literal dict (corrupt_rate, n_sub, hist) to stdout for the A/B.
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
import fec_subblock
from fused_fec_link import env_size
from stream_fec import FecConfig

MAC_HDR = 24
CRC_BYTES = 2
_CFG = FecConfig(k=8, symbol_size=32, overhead=0.25, scheme="rs")
BLOCK_PAYLOAD = env_size(_CFG)   # RS envelope (11B header + 32B symbol) = the SBI sub-block payload


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


def main():
    infile = sys.argv[1] if len(sys.argv) > 1 else "/tmp/cap_mcs7w.cf32"
    bw = float(sys.argv[2]) if len(sys.argv) > 2 else 20e6
    soft = bool(os.environ.get("GR_SOFT_VITERBI"))

    t = tb(infile, bw)
    t.start()
    last, stable = -1, 0
    for _ in range(140):
        time.sleep(1.0)
        cur = t.dbg.num_messages()
        if cur == last:
            stable += 1
            if stable >= 4:
                break
        else:
            stable, last = 0, cur
    t.stop(); t.wait()

    n_sub = None
    clean = 0
    corrupt = 0
    hist = {}
    for i in range(t.dbg.num_messages()):
        msg = t.dbg.get_message(i)
        meta, blob = pmt.car(msg), pmt.cdr(msg)
        if not pmt.is_u8vector(blob) or geti(meta, "encoding") < 8:
            continue
        data = bytes(pmt.u8vector_elements(blob))
        if len(data) < MAC_HDR + 8:
            continue
        body = data[MAC_HDR:]
        res = fec_subblock.unpack(body, BLOCK_PAYLOAD, CRC_BYTES)
        if res.n_blocks <= 0:
            continue
        n_sub = res.n_blocks
        ok = pmt.to_bool(pmt.dict_ref(meta, pmt.intern("crc_ok"), pmt.PMT_NIL))
        if ok:
            clean += 1
        else:
            corrupt += 1
            s = len(res.survivors)
            hist[s] = hist.get(s, 0) + 1

    total = clean + corrupt
    mode = "soft" if soft else "hard"
    rate = corrupt / total if total else 0.0
    print(f"# {mode}: frames={total} clean={clean} corrupt={corrupt} "
          f"corrupt_rate={rate:.3f} n_sub={n_sub}")
    print(f"CHANNEL_{mode} = {{'corrupt_rate': {rate:.4f}, 'n_sub': {n_sub}, "
          f"'survivor_hist': {dict(sorted(hist.items()))}}}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
