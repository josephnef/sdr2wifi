#!/usr/bin/env python3
"""Capture raw IQ from TWO synchronized B210 RX channels to two cf32 files.

Two simultaneous RX observations y0(t), y1(t) of a 2-antenna transmitter let you solve
the 2x2 channel and SEPARATE the transmit streams -- needed to read a real chip's STBC /
MIMO convention (P-matrix signs, Alamouti ordering, pilots) off the air, which one RX
antenna cannot do. Both channels stream off the shared B210 clock, so they're sample-
aligned. Writes <out>.0 and <out>.1.

  ./iq_capture2.py --freq 2412e6 --bw 20e6 --secs 4 --rx-gain 50 --out /tmp/cap.cf32
"""
import sys, time, argparse
from gnuradio import gr, blocks, uhd


class iq_capture2(gr.top_block):
    def __init__(self, a):
        gr.top_block.__init__(self, "B210 2ch IQ capture")
        self.src = uhd.usrp_source(
            ",".join((a.args, "")),
            uhd.stream_args(cpu_format="fc32", otw_format=a.otw, channels=[0, 1]),
        )
        self.src.set_samp_rate(a.bw)
        for ch in (0, 1):
            self.src.set_center_freq(uhd.tune_request(a.freq, a.lo_offset), ch)
            self.src.set_gain(a.rx_gain, ch)
            self.src.set_antenna(a.rx_ant, ch)
        self.snk0 = blocks.file_sink(gr.sizeof_gr_complex, a.outfile + ".0", False)
        self.snk1 = blocks.file_sink(gr.sizeof_gr_complex, a.outfile + ".1", False)
        self.snk0.set_unbuffered(False)
        self.snk1.set_unbuffered(False)
        self.connect((self.src, 0), (self.snk0, 0))
        self.connect((self.src, 1), (self.snk1, 0))


def main():
    p = argparse.ArgumentParser(description="2-channel B210 IQ capture")
    p.add_argument("--out", dest="outfile", default="/tmp/cap2.cf32")
    p.add_argument("--args", default="type=b200")
    p.add_argument("--bw", type=float, default=20e6)
    p.add_argument("--freq", type=float, default=2412e6)
    p.add_argument("--lo-offset", dest="lo_offset", type=float, default=0.0)
    p.add_argument("--rx-gain", dest="rx_gain", type=float, default=50.0)
    p.add_argument("--otw", default="sc8")
    p.add_argument("--rx-ant", dest="rx_ant", default="RX2")
    p.add_argument("--secs", type=float, default=4.0)
    a = p.parse_args()
    print(f"[iq-cap2] 2ch freq={a.freq/1e9:.4f}GHz bw={a.bw/1e6:g} gain={a.rx_gain:g} "
          f"secs={a.secs:g} -> {a.outfile}.0/.1")
    tb = iq_capture2(a)
    tb.start(); time.sleep(a.secs); tb.stop(); tb.wait()
    print("[iq-cap2] done")


if __name__ == "__main__":
    main()
