#!/usr/bin/env python3
"""Replay a recorded .cf32 through the fork RX and bucket per-frame SNR / CFO by
CRC pass/fail. If FAILED frames carry HIGH SNR, the over-air corruption is a
receiver correction defect (CFO/SRO/phase/sync), not noise.
  ./iq_diag.py /tmp/cap_mcs1.cf32 [bw]
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
                                 encoding=ieee802_11.BPSK_1_2, frequency=2.437e9,
                                 sensitivity=0.56)
        self.null = blocks.null_sink(gr.sizeof_gr_complex)
        self.dbg = blocks.message_debug()
        self.src.set_min_output_buffer(1 << 22)
        self.connect((self.src, 0), (self.phy, 0))
        self.connect((self.phy, 0), (self.null, 0))
        self.msg_connect((self.phy, "mac_out"), (self.dbg, "store"))


def getf(meta, key, default=0.0):
    v = pmt.dict_ref(meta, pmt.intern(key), pmt.PMT_NIL)
    return pmt.to_double(v) if pmt.is_real(v) else default


def main():
    infile = sys.argv[1] if len(sys.argv) > 1 else "/tmp/cap_mcs1.cf32"
    bw = float(sys.argv[2]) if len(sys.argv) > 2 else 20e6
    t = tb(infile, bw)
    t.start()
    time.sleep(20)   # 4 s capture replays faster than real-time but give margin
    t.stop()
    t.wait()

    n = t.dbg.num_messages()
    passed, failed = [], []
    cfo_pass, cfo_fail = [], []
    for i in range(n):
        msg = t.dbg.get_message(i)
        meta = pmt.car(msg)
        v = pmt.dict_ref(meta, pmt.intern("crc_ok"), pmt.PMT_NIL)
        ok = pmt.to_bool(v) if pmt.is_bool(v) else True
        snr = getf(meta, "snr")
        cfo = getf(meta, "freq_offset")
        (passed if ok else failed).append(snr)
        (cfo_pass if ok else cfo_fail).append(cfo)

    def stats(xs):
        if not xs:
            return "n=0"
        xs = sorted(xs)
        m = sum(xs) / len(xs)
        return (f"n={len(xs)} min={xs[0]:.1f} p25={xs[len(xs)//4]:.1f} "
                f"med={xs[len(xs)//2]:.1f} mean={m:.1f} p75={xs[3*len(xs)//4]:.1f} "
                f"max={xs[-1]:.1f}")

    print(f"[iq-diag] frames={n}  pass={len(passed)}  fail={len(failed)}  "
          f"fail-rate={len(failed)/max(1,n)*100:.0f}%")
    print(f"[iq-diag] SNR(dB) PASS : {stats(passed)}")
    print(f"[iq-diag] SNR(dB) FAIL : {stats(failed)}")
    print(f"[iq-diag] CFO(Hz) PASS : {stats(cfo_pass)}")
    print(f"[iq-diag] CFO(Hz) FAIL : {stats(cfo_fail)}")
    if failed and passed:
        import statistics
        mf = statistics.median(failed)
        mp = statistics.median(passed)
        if mf > mp - 3:
            print("VERDICT: failed frames have HIGH SNR (~ like passing frames) "
                  "-> corruption is a RECEIVER CORRECTION DEFECT, not noise.")
        else:
            print("VERDICT: failed frames have LOW SNR -> noise/EVM-limited.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
