#!/usr/bin/env python3
"""Rung-3: SDR as a pure 802.11 receiver, decoding a real external Wi-Fi TX.

This is rung-2 (rf_loopback.py) with the transmit half deleted: the B210/LibreSDR
only *receives*, and the 802.11 frames come from a separate, independent radio --
the `devourer` userspace RTL88xxAU driver (~/git/devourer) driving an RTL8812AU.
It proves the gr-ieee802-11 RX chain against a genuine over-air transmitter.

  RTL8812AU  --(legacy 6 Mbps OFDM, ch36 / 5180 MHz, 20 MHz)-->  air  -->  B210 RXB

This fork decodes legacy 802.11a/g/p AND modern OFDM (HT20/HT40 SISO, 2x2 MIMO,
VHT-SU SISO) — the format-detecting equalizer reads each frame's SIG. A simple
legacy transmitter is devourer's PrecoderDemo (legacy 6 Mbps OFDM):
  cd ~/git/devourer && head -c 64 /dev/urandom > /tmp/psdu.bin
  sudo DEVOURER_PID=0x8812 DEVOURER_CHANNEL=36 \
       ./build/PrecoderDemo --psdu /tmp/psdu.bin --interval-ms 2
StreamTxDemo (DEVOURER_TX_RATE=MCS0..7) emits HT20, also decodable here.

Both sides MUST share center frequency (5180 MHz) and 20 MHz bandwidth.

Env (this Linux box -- NOT the Mac paths in CLAUDE.md); run.sh equivalent is run_rx.sh:
  PREFIX="$HOME/grwifi-install"
  export PYTHONPATH="$PREFIX/lib/python3.14/site-packages:$PWD"
  export LD_LIBRARY_PATH="$PREFIX/lib:$LD_LIBRARY_PATH"
  ulimit -n 8192

Usage:
  ./wifi_rx_sniff.py --check                 # open + configure the SDR, print config, no decode
  ./wifi_rx_sniff.py --secs 10               # listen 10 s, count decoded frames
  ./wifi_rx_sniff.py --freq 2437e6 --secs 10 # 2.4 GHz ch6 instead of 5 GHz ch36
"""
import sys, time, argparse
from gnuradio import gr, blocks, uhd
import ieee802_11
import pmt
from wifi_phy_hier import wifi_phy_hier

# devourer's canonical TX source MAC (txdemo/precoder_demo). addr2/SA at byte 10 of
# the 802.11 header -- a decoded frame carrying this proves it came from our TX.
CANONICAL_SA = [0x57, 0x42, 0x75, 0x05, 0xd6, 0x00]


class wifi_rx_sniff(gr.top_block):
    def __init__(self, a):
        gr.top_block.__init__(self, "802.11 RX-only sniffer (gr-ieee802-11)")
        bw = a.bw
        dev = ",".join((a.args, ""))

        # ---- PHY: RX decode only (samples in 0 -> mac_out). `encoding` is a TX-only
        # knob; the RX path reads each frame's own SIGNAL field for its rate.
        self.phy = wifi_phy_hier(bandwidth=bw, chan_est=ieee802_11.LS,
                                 encoding=ieee802_11.BPSK_1_2,
                                 frequency=a.freq, sensitivity=0.56)
        self.dbg = blocks.message_debug()

        # ---- SDR RX: single channel 0, antenna "RX2" (B210 frontend B).
        self.src = uhd.usrp_source(
            dev,
            uhd.stream_args(cpu_format="fc32", otw_format=a.otw, channels=[a.rx_chan]),
        )
        self.src.set_samp_rate(bw)
        self.src.set_center_freq(uhd.tune_request(a.freq, a.lo_offset), 0)
        self.src.set_gain(a.rx_gain, 0)
        self.src.set_antenna(a.rx_ant, 0)

        # wifi_phy_hier is bidirectional: its TX output port (0) must be connected
        # even RX-only. We drive no frames into mac_in, so the TX chain stays idle;
        # a null sink just satisfies the port and discards the (empty) TX stream.
        self.null = blocks.null_sink(gr.sizeof_gr_complex)

        # ---- wiring: samples -> PHY RX; decoded frames -> message sink
        self.connect((self.src, 0), (self.phy, 0))
        self.connect((self.phy, 0), (self.null, 0))
        self.msg_connect(self.phy, "mac_out", self.dbg, "store")

    def config_str(self):
        return (
            f"  RX: ant={self.src.get_antenna(0)} freq={self.src.get_center_freq(0)/1e9:.4f} GHz "
            f"rate={self.src.get_samp_rate()/1e6:g} MS/s gain={self.src.get_gain(0):g} dB"
        )


def frame_bytes(msg):
    cdr = pmt.cdr(msg)
    if not pmt.is_u8vector(cdr):
        return None
    return list(pmt.u8vector_elements(cdr))


def scan_frames(dbg):
    """Walk every decoded frame; return (total, devourer_count, first_devourer_bytes).

    A frame is attributed to devourer when its 802.11 addr2/SA (bytes 10..15)
    equals the canonical TX MAC. The ambient channel carries other 802.11
    traffic (ACK/CTS/beacons), so this filter is what makes the test specific.
    """
    n = dbg.num_messages()
    dev_count, first_dev = 0, None
    for i in range(n):
        data = frame_bytes(dbg.get_message(i))
        if data and len(data) >= 16 and data[10:16] == CANONICAL_SA:
            dev_count += 1
            if first_dev is None:
                first_dev = data
    return n, dev_count, first_dev


def dump_frame(data, label):
    head = " ".join(f"{b:02x}" for b in data[:24])
    print(f"  {label}: {len(data)} bytes | first24: {head}")
    if len(data) >= 16:
        print(f"    SA(addr2) = {':'.join(f'{b:02x}' for b in data[10:16])}")


def main():
    p = argparse.ArgumentParser(description="RX-only 802.11a/g/p sniffer (gr-ieee802-11)")
    p.add_argument("--args", default="type=b200")
    p.add_argument("--bw", type=float, default=20e6,
                   help="channel bandwidth = sample rate (Hz). 20e6 = standard 802.11a/g")
    p.add_argument("--freq", type=float, default=5180e6,
                   help="RF center (Hz); 5180e6 = 5 GHz ch36, 2437e6 = 2.4 GHz ch6")
    p.add_argument("--lo-offset", dest="lo_offset", type=float, default=0.0,
                   help="LO offset (Hz); MUST be < Fs/2. 0 is safest (802.11 nulls DC).")
    # 50 dB is the measured sweet spot on this link (RX2 antenna): a gain sweep
    # gave ~2.9k decoded frames @40 dB, ~8.7k @55 dB, ~3.9k @70 dB (overload).
    p.add_argument("--rx-gain", dest="rx_gain", type=float, default=50.0)
    p.add_argument("--otw", default="sc8", help="over-the-wire fmt; sc8 halves USB load")
    p.add_argument("--rx-chan", dest="rx_chan", type=int, default=0)
    p.add_argument("--rx-ant", dest="rx_ant", default="RX2")
    p.add_argument("--secs", type=float, default=10.0)
    p.add_argument("--check", action="store_true",
                   help="open + configure the SDR and print config, but do NOT receive")
    a = p.parse_args()

    print(f"[wifi-rx] bw={a.bw/1e6:g} MHz  freq={a.freq/1e9:.4f} GHz  otw={a.otw}  "
          f"RX=ch{a.rx_chan}/{a.rx_ant}  gain={a.rx_gain:g} dB")
    tb = wifi_rx_sniff(a)
    print("[wifi-rx] device opened, configured:")
    print(tb.config_str())

    if a.check:
        print("[wifi-rx] --check: not receiving. Start the devourer TX (PrecoderDemo, "
              "same freq+bw), then run without --check.")
        return 0

    tb.start()
    time.sleep(a.secs)
    tb.stop()
    tb.wait()

    total, dev_count, first_dev = scan_frames(tb.dbg)
    print(f"[wifi-rx] decoded frames: {total} total  |  {dev_count} from devourer "
          f"(SA {':'.join(f'{b:02x}' for b in CANONICAL_SA)})")
    if first_dev is not None:
        dump_frame(first_dev, "devourer frame")

    if dev_count:
        print("RESULT: PASS — decoded over-air 802.11 frames from the devourer TX")
        return 0
    if total:
        print(f"RESULT: PARTIAL — the RX chain works ({total} ambient 802.11 frames "
              "decoded) but none carried the devourer SA. Confirm PrecoderDemo is "
              "running on the SAME freq+bw, and tune --rx-gain / antenna spacing.")
        return 1
    print("RESULT: no frames — confirm the TX is running on the SAME freq+bw (5180 MHz, "
          "20 MHz), tune --rx-gain (try 30–60), move the antennas closer, and check that "
          "UHD reports 'Operating over USB 3'.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
