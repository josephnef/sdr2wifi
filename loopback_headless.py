#!/usr/bin/env python3
"""Rung-1 software loopback for gr-ieee802-11: 802.11 encode -> (pad+channel) -> decode.

No hardware, no Qt GUI. Validates the full 802.11a/g/p PHY+MAC in software.
Default config = 10 MHz / 5.890 GHz = 802.11p (the narrowband mode that would fit
USB 2.0 on the B220 Mini). Run:  python3.14 loopback_headless.py [bandwidth_hz] [seconds]
"""
import sys, time
from gnuradio import gr, blocks, channels
import ieee802_11
import foo
import pmt
from wifi_phy_hier import wifi_phy_hier


class loopback_tb(gr.top_block):
    def __init__(self, bandwidth=10e6, freq=5.89e9, pdu_len=100, interval_ms=40):
        gr.top_block.__init__(self, "wifi loopback (headless)")
        # PHY: encode on TX side, decode on RX side (one hier block does both)
        self.phy = wifi_phy_hier(
            bandwidth=bandwidth,
            chan_est=ieee802_11.LS,
            encoding=ieee802_11.BPSK_1_2,   # most robust rate (MCS0)
            frequency=freq,
            sensitivity=0.56,
        )
        self.mac = ieee802_11.mac([0x23] * 6, [0x42] * 6, [0xff] * 6)
        self.strobe = blocks.message_strobe(pmt.intern("x" * pdu_len), interval_ms)
        # gr-foo packet pad: 500 zero samples in front so the RX sync sees a lead-in
        self.pad = foo.packet_pad2(False, False, 0.001, 500, 0)
        self.gain = blocks.multiply_const_cc(10.0)
        # clean channel: no noise, no freq offset, unit tap  (proves encode<->decode)
        self.chan = channels.channel_model(0.0, 0.0, 1.0, [1.0], 0, False)
        self.dbg = blocks.message_debug()

        # gr-ieee802-11's OFDM TX emits whole frames as one tagged burst (~2 MB),
        # so every stream block on the sample path needs an output buffer that big.
        for b in (self.pad, self.gain, self.chan):
            b.set_min_output_buffer(1 << 22)

        # message path:  payload -> MAC frame -> PHY(TX)
        self.msg_connect(self.strobe, "strobe", self.mac, "app in")
        self.msg_connect(self.mac, "phy out", self.phy, "mac_in")
        # sample path:  PHY(TX) -> pad -> gain -> channel -> PHY(RX)
        self.connect((self.phy, 0), (self.pad, 0))
        self.connect((self.pad, 0), (self.gain, 0))
        self.connect((self.gain, 0), (self.chan, 0))
        self.connect((self.chan, 0), (self.phy, 0))
        # decoded frames out
        self.msg_connect(self.phy, "mac_out", self.dbg, "store")


def main():
    bw = float(sys.argv[1]) if len(sys.argv) > 1 else 10e6
    secs = float(sys.argv[2]) if len(sys.argv) > 2 else 3.0
    print(f"[loopback] bandwidth={bw/1e6:g} MHz  freq=5.890 GHz  encoding=BPSK_1/2 (802.11p)")
    tb = loopback_tb(bandwidth=bw)
    tb.start()
    time.sleep(secs)
    tb.stop()
    tb.wait()
    n = tb.dbg.num_messages()
    print(f"[loopback] decoded frames: {n}")
    if n:
        # show the first recovered frame: pull payload bytes out of the PDU
        msg = tb.dbg.get_message(0)
        car = pmt.car(msg)
        cdr = pmt.cdr(msg)
        nbytes = pmt.length(cdr) if pmt.is_u8vector(cdr) else 0
        print(f"[loopback] first frame PDU: {nbytes} bytes, meta={pmt.write_string(car)[:80]}")
        print("RESULT: PASS — 802.11 frames encoded and decoded in software")
        return 0
    print("RESULT: FAIL — no frames decoded")
    return 1


if __name__ == "__main__":
    sys.exit(main())
