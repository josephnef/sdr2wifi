#!/usr/bin/env python3
"""Replay a recorded/synthetic cf32 IQ file through an RX-ONLY 802.11 chain.

Deterministic, no SDR. Builds the gr-ieee802-11 receive front-end directly
(sync_short -> sync_long -> FFT -> frame_equalizer -> decode_mac) WITHOUT the
TX half of wifi_phy_hier -- so the flowgraph terminates cleanly at file EOF via
run() (the bidirectional hier block's idle TX chain otherwise keeps run() alive
and forces an unreliable start/sleep/stop, which dropped frames non-determin-
istically). Decoded frames + the HT decoder's stdout are the result.

  ./tools_replay_iq.py --in /tmp/ht.cf32 --freq 5180e6 --bw 20e6
"""
import sys, argparse
from gnuradio import gr, blocks, fft
from gnuradio.fft import window
import ieee802_11

SYNC_LENGTH = 320


class replay(gr.top_block):
    def __init__(self, a):
        gr.top_block.__init__(self, "iq replay (RX only)")
        bw, freq = a.bw, a.freq

        # HT40 (40 MHz) uses a 128-FFT front-end: L-STF short symbol = FFT/4 samples
        # (32 vs 16), the L-LTF matched filter is 128-tap, and the OFDM symbol path is
        # 128-wide. The L-preamble is non-HT-duplicate so the autocorr wiring is the
        # same shape, only the lag/window lengths and vector sizes scale with FFT.
        fft_len = 128 if bw >= 40e6 else 64
        short = fft_len // 4                 # L-STF short-symbol period (16 or 32)
        win_c = 3 * short                    # complex autocorr moving-average window
        win_f = 4 * short                    # power moving-average window
        sync_length = SYNC_LENGTH if fft_len == 64 else 2 * SYNC_LENGTH

        self.src = blocks.file_source(gr.sizeof_gr_complex, a.infile, False)
        # Pace the file like a live SDR. file_source floods the whole capture in
        # one giant work() call, which makes gr-ieee802-11's streaming sync blocks
        # behave non-deterministically (detect all frames or none). A throttle at
        # the sample rate delivers steady bounded chunks -> reliable, like live RX.
        self.throttle = blocks.throttle(gr.sizeof_gr_complex, bw, True)

        # --- autocorrelation front-end (mirrors wifi_phy_hier RX wiring) ---
        self.delay16 = blocks.delay(gr.sizeof_gr_complex, short)
        self.conj = blocks.conjugate_cc()
        self.mult = blocks.multiply_vcc(1)
        self.mavg_c = blocks.moving_average_cc(win_c, 1, 4000, 1)
        self.c2mag = blocks.complex_to_mag(1)
        self.c2mag2 = blocks.complex_to_mag_squared(1)
        self.mavg_f = blocks.moving_average_ff(win_f, 1, 4000, 1)
        self.div = blocks.divide_ff(1)
        self.sync_short = ieee802_11.sync_short(0.56, 2, False, False)
        self.delay_sl = blocks.delay(gr.sizeof_gr_complex, sync_length)
        self.sync_long = ieee802_11.sync_long(sync_length, False, False, fft_len)
        self.s2v = blocks.stream_to_vector(gr.sizeof_gr_complex, fft_len)
        self.fft = fft.fft_vcc(fft_len, True, window.rectangular(fft_len), True, 1)
        self.fe = ieee802_11.frame_equalizer(
            ieee802_11.LS, freq, bw, False, False, fft_len)
        self.dm = ieee802_11.decode_mac(False, False)
        self.dbg = blocks.message_debug()

        # source -> throttle -> three sync_short inputs
        self.connect((self.src, 0), (self.throttle, 0))
        self.connect((self.throttle, 0), (self.c2mag2, 0))
        self.connect((self.c2mag2, 0), (self.mavg_f, 0))
        self.connect((self.mavg_f, 0), (self.div, 1))
        self.connect((self.throttle, 0), (self.delay16, 0))
        self.connect((self.delay16, 0), (self.conj, 0))
        self.connect((self.conj, 0), (self.mult, 1))
        self.connect((self.throttle, 0), (self.mult, 0))
        self.connect((self.delay16, 0), (self.sync_short, 0))
        self.connect((self.mult, 0), (self.mavg_c, 0))
        self.connect((self.mavg_c, 0), (self.c2mag, 0))
        self.connect((self.c2mag, 0), (self.div, 0))
        self.connect((self.mavg_c, 0), (self.sync_short, 1))
        self.connect((self.div, 0), (self.sync_short, 2))
        # sync_short -> sync_long -> FFT -> equalizer -> mac
        self.connect((self.sync_short, 0), (self.delay_sl, 0))
        self.connect((self.delay_sl, 0), (self.sync_long, 1))
        self.connect((self.sync_short, 0), (self.sync_long, 0))
        self.connect((self.sync_long, 0), (self.s2v, 0))
        self.connect((self.s2v, 0), (self.fft, 0))
        self.connect((self.fft, 0), (self.fe, 0))
        self.connect((self.fe, 0), (self.dm, 0))
        self.msg_connect((self.dm, "out"), (self.dbg, "store"))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--in", dest="infile", default="/tmp/wifi.cf32")
    p.add_argument("--bw", type=float, default=20e6)
    p.add_argument("--freq", type=float, default=5180e6)
    p.add_argument("--secs", type=float, default=0,
                   help="ignored; kept for compatibility (run() ends at EOF)")
    a = p.parse_args()
    tb = replay(a)
    # Bound the work-chunk size so the gr-ieee802-11 sync blocks see uniform input
    # regardless of scheduler timing (large/variable chunks made detection
    # non-deterministic run-to-run). RX-only chain -> terminates at file EOF.
    tb.run(2048)
    print(f"[replay] decoded (legacy) frames: {tb.dbg.num_messages()}")


if __name__ == "__main__":
    sys.exit(main())
