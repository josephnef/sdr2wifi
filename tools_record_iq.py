#!/usr/bin/env python3
"""Capture raw IQ from the SDR to a cf32 file for offline replay/debugging.

  ./tools_record_iq.py --secs 5 --out /tmp/cap.cf32   # 20 MS/s @ 5180 MHz, RX2

Replays via tools_replay_iq.py through the gr-ieee802-11 RX chain so an
intermittent, frame-triggered crash can be reproduced deterministically
(e.g. under AddressSanitizer).
"""
import sys, time, argparse
from gnuradio import gr, blocks, uhd


class rec(gr.top_block):
    def __init__(self, a):
        gr.top_block.__init__(self, "iq recorder")
        self.src = uhd.usrp_source(
            ",".join((a.args, "")),
            uhd.stream_args(cpu_format="fc32", otw_format=a.otw, channels=[a.rx_chan]),
        )
        self.src.set_samp_rate(a.bw)
        self.src.set_center_freq(uhd.tune_request(a.freq, a.lo_offset), 0)
        self.src.set_gain(a.rx_gain, 0)
        self.src.set_antenna(a.rx_ant, 0)
        self.snk = blocks.file_sink(gr.sizeof_gr_complex, a.out, False)
        self.snk.set_unbuffered(False)
        self.connect((self.src, 0), (self.snk, 0))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--args", default="type=b200")
    p.add_argument("--bw", type=float, default=20e6)
    p.add_argument("--freq", type=float, default=5180e6)
    p.add_argument("--lo-offset", dest="lo_offset", type=float, default=0.0)
    p.add_argument("--rx-gain", dest="rx_gain", type=float, default=40.0)
    p.add_argument("--otw", default="sc8")
    p.add_argument("--rx-chan", dest="rx_chan", type=int, default=0)
    p.add_argument("--rx-ant", dest="rx_ant", default="RX2")
    p.add_argument("--secs", type=float, default=5.0)
    p.add_argument("--out", default="/tmp/cap.cf32")
    a = p.parse_args()
    tb = rec(a)
    print(f"[rec] {a.bw/1e6:g} MS/s @ {a.freq/1e9:.4f} GHz -> {a.out} for {a.secs}s")
    tb.start(); time.sleep(a.secs); tb.stop(); tb.wait()
    print("[rec] done")


if __name__ == "__main__":
    sys.exit(main())
