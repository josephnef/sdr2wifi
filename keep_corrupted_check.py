#!/usr/bin/env python3
"""Validate the GR_KEEP_CORRUPTED decode_mac change over a NOISY rung-1 loopback.

Same software PHY as loopback_headless.py but with AWGN turned up so a fraction
of frames fail the FCS. Tallies the decoded PDUs by their crc_ok meta tag:

  * without GR_KEEP_CORRUPTED  -> only crc_ok=#t frames are published (the
    decoder drops FCS failures, as upstream always did);
  * with    GR_KEEP_CORRUPTED  -> FCS failures ALSO surface, tagged crc_ok=#f,
    so a downstream fused-FEC sub-block-integrity layer can salvage them.

Run both halves and compare:
    ./keep_corrupted_check.py --noise 0.9 --secs 4
    GR_KEEP_CORRUPTED=1 ./keep_corrupted_check.py --noise 0.9 --secs 4

Usage (env set by run.sh / run_rx.sh, or the CLAUDE.md recipe).
"""
import argparse
import os
import sys
import time

import pmt
from gnuradio import blocks, channels, gr

import foo
import ieee802_11
from wifi_phy_hier import wifi_phy_hier


class noisy_loopback(gr.top_block):
    def __init__(self, bandwidth, noise, pdu_len, interval_ms, seed):
        gr.top_block.__init__(self, "wifi noisy loopback")
        self.phy = wifi_phy_hier(
            bandwidth=bandwidth, chan_est=ieee802_11.LS,
            encoding=ieee802_11.BPSK_1_2, frequency=5.89e9, sensitivity=0.56)
        self.mac = ieee802_11.mac([0x23] * 6, [0x42] * 6, [0xff] * 6)
        self.strobe = blocks.message_strobe(pmt.intern("x" * pdu_len), interval_ms)
        self.pad = foo.packet_pad2(False, False, 0.001, 500, 0)
        self.gain = blocks.multiply_const_cc(10.0)
        # AWGN turned up: noise_voltage = `noise` (std-dev of complex gaussian).
        self.chan = channels.channel_model(noise, 0.0, 1.0, [1.0], seed, False)
        self.dbg = blocks.message_debug()
        for b in (self.pad, self.gain, self.chan):
            b.set_min_output_buffer(1 << 22)
        self.msg_connect(self.strobe, "strobe", self.mac, "app in")
        self.msg_connect(self.mac, "phy out", self.phy, "mac_in")
        self.connect((self.phy, 0), (self.pad, 0))
        self.connect((self.pad, 0), (self.gain, 0))
        self.connect((self.gain, 0), (self.chan, 0))
        self.connect((self.chan, 0), (self.phy, 0))
        self.msg_connect(self.phy, "mac_out", self.dbg, "store")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bandwidth", type=float, default=10e6)
    ap.add_argument("--noise", type=float, default=0.9,
                    help="AWGN voltage std-dev (higher = more FCS failures)")
    ap.add_argument("--secs", type=float, default=4.0)
    ap.add_argument("--pdu-len", type=int, default=100)
    ap.add_argument("--interval-ms", type=int, default=20)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args(argv)

    keep = os.environ.get("GR_KEEP_CORRUPTED") is not None
    tb = noisy_loopback(args.bandwidth, args.noise, args.pdu_len,
                        args.interval_ms, args.seed)
    tb.start()
    time.sleep(args.secs)
    tb.stop()
    tb.wait()

    n = tb.dbg.num_messages()
    ok = bad = unknown = 0
    for i in range(n):
        meta = pmt.car(tb.dbg.get_message(i))
        v = pmt.dict_ref(meta, pmt.intern("crc_ok"), pmt.PMT_NIL)
        if pmt.is_bool(v):
            if pmt.to_bool(v):
                ok += 1
            else:
                bad += 1
        else:
            unknown += 1
    print(f"[keep-corrupted] GR_KEEP_CORRUPTED={'set' if keep else 'unset'} "
          f"noise={args.noise} secs={args.secs}")
    print(f"[keep-corrupted] decoded PDUs: {n}  crc_ok=#t: {ok}  "
          f"crc_ok=#f: {bad}  no-tag: {unknown}")
    if keep:
        # With the env on we expect to SEE some corrupt frames surface.
        if bad > 0:
            print(f"RESULT: PASS — {bad} FCS-failed frame(s) surfaced (crc_ok=#f)")
            return 0
        print("RESULT: INCONCLUSIVE — no corrupt frames at this noise; raise --noise")
        return 2
    else:
        # Default path must never surface a corrupt frame.
        if bad == 0:
            print("RESULT: PASS — default path published only clean frames")
            return 0
        print("RESULT: FAIL — corrupt frame leaked without GR_KEEP_CORRUPTED")
        return 1


if __name__ == "__main__":
    sys.exit(main())
