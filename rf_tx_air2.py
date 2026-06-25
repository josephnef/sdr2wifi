#!/usr/bin/env python3
"""Two-channel over-air TX for the B210 (synchronized dual TX), for STBC / 2x2 MIMO.

Feeds two cf32 files (one per antenna) to the B210's two TX channels, which stream in
lockstep off the shared flowgraph clock so the two antennas stay sample-aligned (what
STBC/MIMO need). Mirrors rf_tx_air.py but with channels=[0,1].

SAFETY: radiates on a real Wi-Fi channel. Use a low --tx-gain and a quiet channel.

  python3 rf_tx_air2.py --in0 frame.cf32 --in1 frame.cf32.ant1 \
      --freq 2412e6 --bw 20e6 --tx-gain 70 --secs 20
"""
import sys, time, argparse
from gnuradio import gr, blocks, uhd


class rf_tx_air2(gr.top_block):
    def __init__(self, a):
        gr.top_block.__init__(self, "rf_tx_air2")
        self.snk = uhd.usrp_sink(
            a.args,
            uhd.stream_args(cpu_format="fc32", otw_format=a.otw, channels=[0, 1]),
        )
        self.snk.set_samp_rate(a.bw)
        for ch in (0, 1):
            self.snk.set_center_freq(uhd.tune_request(a.freq, a.lo_offset), ch)
            self.snk.set_gain(a.tx_gain, ch)
            self.snk.set_antenna(a.tx_ant, ch)

        if a.interleaved:
            # ONE locked source (ant0[t],ant1[t],...) -> deinterleave -> ch0/ch1. Keeps
            # the two TX channels sample-aligned (what STBC/MIMO require).
            self.fsrc = blocks.file_source(gr.sizeof_gr_complex, a.interleaved, repeat=True)
            self.fsrc.set_min_output_buffer(1 << 22)
            self.di = blocks.deinterleave(gr.sizeof_gr_complex, 1)
            self.amp0 = blocks.multiply_const_cc(a.amp)
            self.amp1 = blocks.multiply_const_cc(a.amp)
            self.connect(self.fsrc, self.di)
            self.connect((self.di, 0), (self.amp0, 0), (self.snk, 0))
            self.connect((self.di, 1), (self.amp1, 0), (self.snk, 1))
        else:
            self.fsrc0 = blocks.file_source(gr.sizeof_gr_complex, a.in0, repeat=True)
            self.fsrc1 = blocks.file_source(gr.sizeof_gr_complex, a.in1, repeat=True)
            self.fsrc0.set_min_output_buffer(1 << 22)
            self.fsrc1.set_min_output_buffer(1 << 22)
            self.amp0 = blocks.multiply_const_cc(a.amp)
            self.amp1 = blocks.multiply_const_cc(a.amp)
            self.connect((self.fsrc0, 0), (self.amp0, 0), (self.snk, 0))
            self.connect((self.fsrc1, 0), (self.amp1, 0), (self.snk, 1))
        print(f"[rf-tx-air2] B210 2ch TX @ {a.freq/1e6:g} MHz, "
              f"rate={self.snk.get_samp_rate()/1e6:g} MS/s gain={a.tx_gain:g} dB ant={a.tx_ant}"
              f"{' (interleaved)' if a.interleaved else ''}")


def main():
    p = argparse.ArgumentParser(description="2-channel over-air 802.11 TX (B210) for STBC/MIMO")
    p.add_argument("--in0", help="antenna-0 cf32 (with --in1; two independent sources)")
    p.add_argument("--in1", help="antenna-1 cf32")
    p.add_argument("--interleaved", help="single interleaved cf32 (ant0[t],ant1[t],...) "
                                         "-> deinterleave; keeps the channels sample-locked")
    p.add_argument("--args", default="type=b200")
    p.add_argument("--bw", type=float, default=20e6)
    p.add_argument("--freq", type=float, default=2412e6)
    p.add_argument("--lo-offset", dest="lo_offset", type=float, default=0.0)
    p.add_argument("--tx-gain", dest="tx_gain", type=float, default=70.0)
    p.add_argument("--amp", type=float, default=1.0)
    p.add_argument("--otw", default="sc8")
    p.add_argument("--tx-ant", dest="tx_ant", default="TX/RX")
    p.add_argument("--secs", type=float, default=15.0)
    a = p.parse_args()

    tb = rf_tx_air2(a)
    print(f"[rf-tx-air2] transmitting for {a.secs:g}s — radiating on a live Wi-Fi channel.")
    tb.start()
    time.sleep(a.secs)
    tb.stop()
    tb.wait()


if __name__ == "__main__":
    main()
