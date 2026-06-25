#!/usr/bin/env python3
"""Capture raw baseband IQ (cf32) from the B210 to a file.

Records the over-air signal so a real-RF frame (e.g. a devourer HT/VHT transmission
the live decoder chokes on) becomes a deterministic offline test vector: replay it
through tools_replay_iq.py / the fork decoder as many times as needed while
hardening the DSP, instead of re-running the radio each time.

  air  ->  B210 RXB  ->  cf32 file

The sample rate MUST match the signal bandwidth (20e6 for 20 MHz). The file is
~8 bytes/sample * rate -> ~160 MB/s at 20 MS/s; keep --secs small.

Usage:
  ./iq_capture.py --freq 5180e6 --bw 20e6 --secs 2 --out /tmp/real_ht.cf32
"""
import sys, time, argparse
from gnuradio import gr, blocks, uhd


class iq_capture(gr.top_block):
    def __init__(self, a):
        gr.top_block.__init__(self, "B210 IQ capture")
        dev = ",".join((a.args, ""))
        self.src = uhd.usrp_source(
            dev,
            uhd.stream_args(cpu_format="fc32", otw_format=a.otw, channels=[a.rx_chan]),
        )
        self.src.set_samp_rate(a.bw)
        self.src.set_center_freq(uhd.tune_request(a.freq, a.lo_offset), 0)
        self.src.set_gain(a.rx_gain, 0)
        self.src.set_antenna(a.rx_ant, 0)
        self.snk = blocks.file_sink(gr.sizeof_gr_complex, a.outfile, False)
        self.snk.set_unbuffered(False)
        self.connect((self.src, 0), (self.snk, 0))


def main():
    p = argparse.ArgumentParser(description="capture raw cf32 IQ from the B210")
    p.add_argument("--out", dest="outfile", default="/tmp/capture.cf32")
    p.add_argument("--args", default="type=b200")
    p.add_argument("--bw", type=float, default=20e6)
    p.add_argument("--freq", type=float, default=5180e6)
    p.add_argument("--lo-offset", dest="lo_offset", type=float, default=0.0)
    p.add_argument("--rx-gain", dest="rx_gain", type=float, default=50.0)
    p.add_argument("--otw", default="sc8")
    p.add_argument("--rx-chan", dest="rx_chan", type=int, default=0)
    p.add_argument("--rx-ant", dest="rx_ant", default="RX2")
    p.add_argument("--secs", type=float, default=2.0)
    a = p.parse_args()

    print(f"[iq-cap] freq={a.freq/1e9:.4f} GHz bw={a.bw/1e6:g} MHz gain={a.rx_gain:g} "
          f"secs={a.secs:g} -> {a.outfile}")
    tb = iq_capture(a)
    tb.start()
    time.sleep(a.secs)
    tb.stop()
    tb.wait()
    print(f"[iq-cap] done -> {a.outfile}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
