#!/usr/bin/env python3
"""Rung-2: single-B210 over-cable RF loopback for gr-ieee802-11 (802.11a/g/p).

One flowgraph owns the B210 and runs full-duplex: the gr-ieee802-11 PHY encodes
802.11 frames out the TX port, over a physical cable, back into the RX port, and
decodes them. It is rung-1 with the internal channel model replaced by real RF.

  TX  ->  TRXA  (ch 0, antenna "TX/RX")
                 |
                 +--  SMA cable + ~60 dB attenuators  (PROTECTS THE RX FRONT-END)
                 |
  RX  <-  RXB   (ch 1, antenna "RX2")

Defaults: 5 MHz @ 5.890 GHz (802.11p quarter/half-rate) with sc8 over-the-wire so
the full-duplex stream fits a USB-2 link. 10/20 MHz need a USB-3 link.

SAFETY: never wire TX->RX without >=40 dB of attenuation. Keep TX gain low to start.
Without the cable connected the RX sees only noise (0 frames) -- that's expected.

Usage:
  ./rf_loopback.py --check                 # open device + print config, NO transmit
  ./rf_loopback.py                         # run the loopback (needs the cable+pads)
  ./rf_loopback.py --bw 5e6 --tx-gain 60 --rx-gain 30 --secs 5
"""
import sys, time, argparse
from gnuradio import gr, blocks, uhd
import ieee802_11
import foo
import pmt
from wifi_phy_hier import wifi_phy_hier


class rf_loopback(gr.top_block):
    def __init__(self, a):
        gr.top_block.__init__(self, "B210 RF loopback (gr-ieee802-11)")
        bw = a.bw
        dev = ",".join((a.args, ""))

        # ---- PHY: TX encode (mac_in -> samples out 0) + RX decode (samples in 0 -> mac_out)
        self.phy = wifi_phy_hier(bandwidth=bw, chan_est=ieee802_11.LS,
                                 encoding=ieee802_11.BPSK_1_2,
                                 frequency=a.freq, sensitivity=0.56)
        self.mac = ieee802_11.mac([0x23] * 6, [0x42] * 6, [0xff] * 6)
        self.strobe = blocks.message_strobe(pmt.intern("x" * a.pdu_len), a.interval_ms)
        self.pad = foo.packet_pad2(False, False, 0.001, 100, 0)
        self.pad.set_min_output_buffer(1 << 22)   # whole OFDM frame is one ~2 MB burst
        self.dbg = blocks.message_debug()

        # ---- B210 TX: physical channel 0 -> TRXA, antenna "TX/RX"
        self.snk = uhd.usrp_sink(
            dev,
            uhd.stream_args(cpu_format="fc32", otw_format=a.otw, channels=[a.tx_chan]),
            "packet_len",                          # tagged-stream length key from packet_pad2
        )
        self.snk.set_samp_rate(bw)
        self.snk.set_center_freq(uhd.tune_request(a.freq, a.lo_offset), 0)
        self.snk.set_gain(a.tx_gain, 0)
        self.snk.set_antenna(a.tx_ant, 0)

        # ---- B210 RX: physical channel 1 -> RXB, antenna "RX2"
        self.src = uhd.usrp_source(
            dev,
            uhd.stream_args(cpu_format="fc32", otw_format=a.otw, channels=[a.rx_chan]),
        )
        self.src.set_samp_rate(bw)
        self.src.set_center_freq(uhd.tune_request(a.freq, a.lo_offset), 0)
        self.src.set_gain(a.rx_gain, 0)
        self.src.set_antenna(a.rx_ant, 0)

        # ---- wiring (rung-1 topology, USRP replaces the channel model)
        self.msg_connect(self.strobe, "strobe", self.mac, "app in")
        self.msg_connect(self.mac, "phy out", self.phy, "mac_in")
        self.connect((self.phy, 0), (self.pad, 0))
        self.connect((self.pad, 0), (self.snk, 0))     # PHY TX -> pad -> B210 TX (TRXA)
        self.connect((self.src, 0), (self.phy, 0))     # B210 RX (RXB) -> PHY RX
        self.msg_connect(self.phy, "mac_out", self.dbg, "store")

    def config_str(self):
        return (
            f"  TX: ant={self.snk.get_antenna(0)} freq={self.snk.get_center_freq(0)/1e9:.4f} GHz "
            f"rate={self.snk.get_samp_rate()/1e6:g} MS/s gain={self.snk.get_gain(0):g} dB\n"
            f"  RX: ant={self.src.get_antenna(0)} freq={self.src.get_center_freq(0)/1e9:.4f} GHz "
            f"rate={self.src.get_samp_rate()/1e6:g} MS/s gain={self.src.get_gain(0):g} dB"
        )


def main():
    p = argparse.ArgumentParser(description="single-B210 RF loopback for gr-ieee802-11")
    # bare "type=b200" (no serial=) — the serial filter is unreliable on this flaky
    # USB-2 link / mid-morph; the uhd.conf [serial=] FPGA override still applies anyway.
    p.add_argument("--args", default="type=b200")
    p.add_argument("--bw", type=float, default=5e6, help="channel bandwidth = sample rate (Hz)")
    p.add_argument("--freq", type=float, default=5.89e9, help="RF center (Hz); 5.89e9 = 802.11p")
    p.add_argument("--lo-offset", dest="lo_offset", type=float, default=0.0,
                   help="LO offset (Hz); MUST be < Fs/2 or the signal leaves the band. "
                        "0 is safest (802.11 nulls the DC subcarrier).")
    p.add_argument("--tx-gain", dest="tx_gain", type=float, default=60.0)
    p.add_argument("--rx-gain", dest="rx_gain", type=float, default=30.0)
    p.add_argument("--otw", default="sc8", help="over-the-wire fmt; sc8 halves USB load")
    p.add_argument("--tx-chan", dest="tx_chan", type=int, default=0)   # 0 -> frontend A (TRXA)
    p.add_argument("--rx-chan", dest="rx_chan", type=int, default=1)   # 1 -> frontend B (RXB)
    p.add_argument("--tx-ant", dest="tx_ant", default="TX/RX")
    p.add_argument("--rx-ant", dest="rx_ant", default="RX2")
    p.add_argument("--pdu-len", dest="pdu_len", type=int, default=100)
    p.add_argument("--interval-ms", dest="interval_ms", type=int, default=40)
    p.add_argument("--secs", type=float, default=5.0)
    p.add_argument("--check", action="store_true",
                   help="open + configure the B210 and print config, but do NOT transmit")
    a = p.parse_args()

    print(f"[rf-loopback] bw={a.bw/1e6:g} MHz  freq={a.freq/1e9:.4f} GHz  otw={a.otw}  "
          f"TX=ch{a.tx_chan}/{a.tx_ant}  RX=ch{a.rx_chan}/{a.rx_ant}")
    tb = rf_loopback(a)
    print("[rf-loopback] device opened, configured:")
    print(tb.config_str())

    if a.check:
        print("[rf-loopback] --check: not transmitting. Connect TRXA -> 60 dB pads -> RXB, "
              "then run without --check.")
        return 0

    tb.start()
    time.sleep(a.secs)
    tb.stop()
    tb.wait()
    n = tb.dbg.num_messages()
    print(f"[rf-loopback] decoded frames: {n}")
    if n:
        print("RESULT: PASS — 802.11 frames recovered over the RF loopback")
        return 0
    print("RESULT: no frames — check the cable/attenuators, then tune --tx-gain/--rx-gain "
          "(too-low signal) or lower them (overload), and confirm 'Operating over USB'.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
