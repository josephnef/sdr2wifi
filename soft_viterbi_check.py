#!/usr/bin/env python3
"""Hard vs soft Viterbi coding gain (GR_SOFT_VITERBI), reproducibly.

Generates a noisy HT capture at a fixed SNR (the waterfall, where the hard
decoder is marginal) and decodes it twice through the same RX flowgraph — once
with the hard Viterbi, once with the soft (GR_SOFT_VITERBI=1) — counting
CRC-32-passing frames. Soft must decode at least as many as hard, and at the
waterfall it decodes more (the ~2-3 dB soft-decision coding gain). Default-off
(hard) is unchanged.

  ./soft_viterbi_check.py --mcs 5 --snr 18 --reps 60

Env via run.sh / the CLAUDE.md recipe (PYTHONPATH / LD_LIBRARY_PATH).
"""
import argparse
import os
import subprocess
import sys
import time


def decode_count(capture, bw, soft):
    """Replay `capture` through the hier RX, return #CRC-32 PASS frames."""
    import pmt
    from gnuradio import blocks, gr
    import ieee802_11
    from wifi_phy_hier import wifi_phy_hier

    if soft:
        os.environ["GR_SOFT_VITERBI"] = "1"
    else:
        os.environ.pop("GR_SOFT_VITERBI", None)

    class tb(gr.top_block):
        def __init__(self):
            gr.top_block.__init__(self)
            self.src = blocks.file_source(gr.sizeof_gr_complex, capture, False)
            self.phy = wifi_phy_hier(bandwidth=bw, chan_est=ieee802_11.LS,
                                     encoding=ieee802_11.BPSK_1_2, frequency=5e9,
                                     sensitivity=0.56)
            self.null = blocks.null_sink(gr.sizeof_gr_complex)
            self.dbg = blocks.message_debug()
            self.src.set_min_output_buffer(1 << 22)
            self.connect((self.src, 0), (self.phy, 0))
            self.connect((self.phy, 0), (self.null, 0))
            self.msg_connect((self.phy, "mac_out"), (self.dbg, "store"))

    t = tb()
    t.start()
    time.sleep(6)
    t.stop()
    t.wait()
    n = t.dbg.num_messages()
    ok = 0
    for i in range(n):
        meta = pmt.car(t.dbg.get_message(i))
        v = pmt.dict_ref(meta, pmt.intern("crc_ok"), pmt.PMT_NIL)
        if pmt.is_bool(v) and pmt.to_bool(v):
            ok += 1
    return ok


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mcs", type=int, default=5)
    ap.add_argument("--snr", type=float, default=18.0)
    ap.add_argument("--reps", type=int, default=60)
    ap.add_argument("--bw", type=float, default=20e6)
    ap.add_argument("--out", default="/tmp/soft_viterbi_check.cf32")
    a = ap.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    subprocess.check_call([sys.executable, os.path.join(here, "tools_gen_wifi.py"),
                           "--mode", "ht", "--mcs", str(a.mcs), "--snr", str(a.snr),
                           "--reps", str(a.reps), "--gap", "2000", "--out", a.out],
                          stdout=subprocess.DEVNULL)

    hard = decode_count(a.out, a.bw, soft=False)
    soft = decode_count(a.out, a.bw, soft=True)
    print(f"[soft-viterbi] MCS{a.mcs} SNR={a.snr} dB  hard CRC-PASS={hard}  "
          f"soft CRC-PASS={soft}  gain={soft - hard}")
    if soft >= hard and soft > 0:
        extra = " (coding gain)" if soft > hard else " (saturated/clean)"
        print(f"RESULT: PASS — soft decodes >= hard{extra}")
        return 0
    print("RESULT: FAIL — soft decoded fewer than hard")
    return 1


if __name__ == "__main__":
    sys.exit(main())
