#!/usr/bin/env python3
"""Single-B210 over-air loopback CAPTURE: transmit a cf32 on ch0/TX-RX while
capturing ch1/RX2 to a file. Decisively answers "does the B210 radiate this frame
over the air" (and is the frame chip-decodable) independent of any external device.

  loopback_capture.py --in tx.cf32 --out cap.cf32 --freq 5745e6 --bw 20e6 --secs 2
"""
import sys, time, argparse
from gnuradio import gr, blocks, uhd


class lb(gr.top_block):
    def __init__(self, a):
        gr.top_block.__init__(self)
        dev = a.args + ","
        # TX: file -> ch0 (TX/RX)
        self.fsrc = blocks.file_source(gr.sizeof_gr_complex, a.infile, repeat=True)
        self.fsrc.set_min_output_buffer(1 << 22)
        self.amp = blocks.multiply_const_cc(a.amp)
        self.snk = uhd.usrp_sink(dev, uhd.stream_args("fc32", a.otw, channels=[0]))
        self.snk.set_samp_rate(a.bw)
        self.snk.set_center_freq(uhd.tune_request(a.freq, a.lo_offset), 0)
        self.snk.set_gain(a.tx_gain, 0)
        self.snk.set_antenna("TX/RX", 0)
        self.connect(self.fsrc, self.amp, self.snk)
        # RX: ch1 (RX2) -> file
        self.src = uhd.usrp_source(dev, uhd.stream_args("fc32", a.otw, channels=[1]))
        self.src.set_samp_rate(a.bw)
        self.src.set_center_freq(uhd.tune_request(a.freq, a.lo_offset), 0)
        self.src.set_gain(a.rx_gain, 0)
        self.src.set_antenna("RX2", 0)
        self.fout = blocks.file_sink(gr.sizeof_gr_complex, a.outfile, False)
        self.connect(self.src, self.fout)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--in", dest="infile", required=True)
    p.add_argument("--out", dest="outfile", default="/tmp/lbcap.cf32")
    p.add_argument("--args", default="type=b200")
    p.add_argument("--bw", type=float, default=20e6)
    p.add_argument("--freq", type=float, default=5745e6)
    p.add_argument("--lo-offset", dest="lo_offset", type=float, default=0.0)
    p.add_argument("--tx-gain", dest="tx_gain", type=float, default=70.0)
    p.add_argument("--rx-gain", dest="rx_gain", type=float, default=50.0)
    p.add_argument("--amp", type=float, default=1.0)
    p.add_argument("--otw", default="sc8")
    p.add_argument("--secs", type=float, default=2.0)
    a = p.parse_args()
    tb = lb(a)
    tb.start()
    time.sleep(a.secs)
    tb.stop()
    tb.wait()
    print(f"[loopback] captured -> {a.outfile}")


if __name__ == "__main__":
    sys.exit(main())
