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

        # 2x2 MIMO: a 2nd antenna file -> a 2nd, independent sync+FFT chain feeding
        # input 1 of an n_rx=2 frame_equalizer. The synthetic captures are noiseless
        # with identical timing, so the two independent sync_long blocks lock to the
        # same frame start and the symbol streams co-index. (Real over-air, with CFO
        # and noise, needs sync driven off antenna 0 and applied to antenna 1.)
        self._blocks = []                    # keep python refs alive for GR
        n_rx = 2 if a.infile2 else 1
        self.fe = ieee802_11.frame_equalizer(
            ieee802_11.LS, freq, bw, False, a.debug, fft_len, n_rx)
        self.dm = ieee802_11.decode_mac(False, False)
        self.dbg = blocks.message_debug()

        files = [a.infile] + ([a.infile2] if a.infile2 else [])
        for idx, infile in enumerate(files):
            self._build_rx_chain(infile, idx, bw, fft_len, short, win_c, win_f,
                                 sync_length)

        self.connect((self.fe, 0), (self.dm, 0))
        self.msg_connect((self.dm, "out"), (self.dbg, "store"))

    def _build_rx_chain(self, infile, idx, bw, fft_len, short, win_c, win_f, sync_length):
        """One antenna's RX front-end: file -> throttle -> autocorr/sync -> FFT,
        connected to frame_equalizer input `idx`."""
        b = {}
        b["src"] = blocks.file_source(gr.sizeof_gr_complex, infile, False)
        # Pace the file like a live SDR (a throttle delivers steady bounded chunks ->
        # reliable detection, vs file_source flooding the whole capture at once).
        b["throttle"] = blocks.throttle(gr.sizeof_gr_complex, bw, True)
        b["delay16"] = blocks.delay(gr.sizeof_gr_complex, short)
        b["conj"] = blocks.conjugate_cc()
        b["mult"] = blocks.multiply_vcc(1)
        b["mavg_c"] = blocks.moving_average_cc(win_c, 1, 4000, 1)
        b["c2mag"] = blocks.complex_to_mag(1)
        b["c2mag2"] = blocks.complex_to_mag_squared(1)
        b["mavg_f"] = blocks.moving_average_ff(win_f, 1, 4000, 1)
        b["div"] = blocks.divide_ff(1)
        b["sync_short"] = ieee802_11.sync_short(0.56, 2, False, False)
        b["delay_sl"] = blocks.delay(gr.sizeof_gr_complex, sync_length)
        b["sync_long"] = ieee802_11.sync_long(sync_length, False, False, fft_len)
        b["s2v"] = blocks.stream_to_vector(gr.sizeof_gr_complex, fft_len)
        b["fft"] = fft.fft_vcc(fft_len, True, window.rectangular(fft_len), True, 1)
        self._blocks.append(b)

        self.connect((b["src"], 0), (b["throttle"], 0))
        self.connect((b["throttle"], 0), (b["c2mag2"], 0))
        self.connect((b["c2mag2"], 0), (b["mavg_f"], 0))
        self.connect((b["mavg_f"], 0), (b["div"], 1))
        self.connect((b["throttle"], 0), (b["delay16"], 0))
        self.connect((b["delay16"], 0), (b["conj"], 0))
        self.connect((b["conj"], 0), (b["mult"], 1))
        self.connect((b["throttle"], 0), (b["mult"], 0))
        self.connect((b["delay16"], 0), (b["sync_short"], 0))
        self.connect((b["mult"], 0), (b["mavg_c"], 0))
        self.connect((b["mavg_c"], 0), (b["c2mag"], 0))
        self.connect((b["c2mag"], 0), (b["div"], 0))
        self.connect((b["mavg_c"], 0), (b["sync_short"], 1))
        self.connect((b["div"], 0), (b["sync_short"], 2))
        self.connect((b["sync_short"], 0), (b["delay_sl"], 0))
        self.connect((b["delay_sl"], 0), (b["sync_long"], 1))
        self.connect((b["sync_short"], 0), (b["sync_long"], 0))
        self.connect((b["sync_long"], 0), (b["s2v"], 0))
        self.connect((b["s2v"], 0), (b["fft"], 0))
        self.connect((b["fft"], 0), (self.fe, idx))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--in", dest="infile", default="/tmp/wifi.cf32")
    p.add_argument("--in2", dest="infile2", default=None,
                   help="2nd antenna capture -> enables the 2x2 MIMO RX (n_rx=2)")
    p.add_argument("--bw", type=float, default=20e6)
    p.add_argument("--freq", type=float, default=5180e6)
    p.add_argument("--secs", type=float, default=0,
                   help="ignored; kept for compatibility (run() ends at EOF)")
    p.add_argument("--debug", action="store_true",
                   help="enable frame_equalizer debug output (HT-SIG sniff diagnostics)")
    a = p.parse_args()
    tb = replay(a)
    # Bound the work-chunk size so the gr-ieee802-11 sync blocks see uniform input
    # regardless of scheduler timing (large/variable chunks made detection
    # non-deterministic run-to-run). RX-only chain -> terminates at file EOF.
    tb.run(2048)
    print(f"[replay] decoded (legacy) frames: {tb.dbg.num_messages()}")


if __name__ == "__main__":
    sys.exit(main())
