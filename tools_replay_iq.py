#!/usr/bin/env python3
"""Replay a recorded cf32 IQ file through the gr-ieee802-11 RX chain.

Deterministic, no SDR, no real-time constraint -> ideal for reproducing an
intermittent frame-triggered crash under AddressSanitizer:

  ./tools_replay_iq.py --in /tmp/cap.cf32 --freq 5180e6 --bw 20e6

Exit 0 if it ran to completion, non-zero count printed. A crash (segfault /
ASan abort) reproduces the bug on the exact captured samples.
"""
import sys, time, argparse
from gnuradio import gr, blocks
import ieee802_11
from wifi_phy_hier import wifi_phy_hier


class replay(gr.top_block):
    def __init__(self, a):
        gr.top_block.__init__(self, "iq replay")
        self.phy = wifi_phy_hier(bandwidth=a.bw, chan_est=ieee802_11.LS,
                                 encoding=ieee802_11.BPSK_1_2,
                                 frequency=a.freq, sensitivity=0.56)
        self.src = blocks.file_source(gr.sizeof_gr_complex, a.infile, False)
        self.null = blocks.null_sink(gr.sizeof_gr_complex)
        self.dbg = blocks.message_debug()
        self.connect((self.src, 0), (self.phy, 0))
        self.connect((self.phy, 0), (self.null, 0))
        self.msg_connect(self.phy, "mac_out", self.dbg, "store")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--in", dest="infile", default="/tmp/cap.cf32")
    p.add_argument("--bw", type=float, default=20e6)
    p.add_argument("--freq", type=float, default=5180e6)
    p.add_argument("--secs", type=float, default=20.0,
                   help="wall-clock budget; file_source runs as fast as the CPU "
                        "allows, so this just needs to exceed the processing time")
    a = p.parse_args()
    tb = replay(a)
    # file_source(repeat=False) hits EOF, but the hier block's message topology
    # can keep run() from returning -- drive it with start/sleep/stop instead.
    tb.start()
    time.sleep(a.secs)
    tb.stop()
    tb.wait()
    print(f"[replay] decoded (legacy) frames: {tb.dbg.num_messages()}")


if __name__ == "__main__":
    sys.exit(main())
