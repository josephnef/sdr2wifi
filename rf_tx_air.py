#!/usr/bin/env python3
"""Over-air SDR transmitter: replay a synthetic 802.11 capture for a driver's RX.

This is the RX-direction half of the completeness harness. tools_gen_wifi.py
produces a baseband cf32 for an exact PHY (legacy / HT / HT40 / VHT, a chosen
MCS/BW/coding) whose PSDU carries the canonical SA 57:42:75:05:d6:00. This script
streams that capture out the B210 so an independent receiver -- the `devourer`
RTL88xxAU driver (WiFiDriverDemo, DEVOURER_STREAM_OUT=1) -- can decode it and
report the rate/bw/stbc/ldpc/sgi it recovered. Comparing what we transmitted with
what devourer reports (via desc_rate.py) tests devourer's *receive* path.

  cf32 (tools_gen_wifi.py)  ->  B210 TRXA  --(ch36 / 5180 MHz, 20 MHz)-->  air  ->  RTL8812AU

The file already contains inter-frame gaps (tools_gen_wifi --gap), so we stream it
on repeat as a continuous burst train -- no packet_pad / tagged-stream needed.

The SDR sample rate MUST equal the rate the capture was generated at: 20e6 for
legacy/HT20/VHT20, 40e6 for HT40. The center frequency + bandwidth MUST match the
channel devourer is monitoring.

SAFETY: this radiates on a real Wi-Fi channel. Use a low --tx-gain and a channel
you are authorised to transmit on. Start with --check (configures, does NOT emit).

Usage:
  ./tools_gen_wifi.py --mode vht --mcs 4 --reps 200 --out /tmp/vht.cf32
  ./rf_tx_air.py --in /tmp/vht.cf32 --freq 5180e6 --bw 20e6 --check     # no emit
  ./rf_tx_air.py --in /tmp/vht.cf32 --freq 5180e6 --bw 20e6 --secs 15   # transmit
"""
import sys, time, argparse
from gnuradio import gr, blocks, uhd


class rf_tx_air(gr.top_block):
    def __init__(self, a):
        gr.top_block.__init__(self, "802.11 over-air replay TX (B210)")
        dev = ",".join((a.args, ""))

        # cf32 capture -> optional amplitude trim -> B210 TX. file_source repeats so
        # the framed burst train streams continuously for the whole --secs window.
        self.fsrc = blocks.file_source(gr.sizeof_gr_complex, a.infile, repeat=True)
        # Large output buffers keep the USRP sink fed at 20 MS/s -- without this the
        # scheduler starves the TX (continuous underflows) whenever the host is busy
        # (e.g. a devourer RX is hammering USB on the same box), which corrupts the
        # burst train. 1<<22 samples per the project's documented constraint.
        self.fsrc.set_min_output_buffer(1 << 22)
        self.amp = blocks.multiply_const_cc(a.amp)

        self.snk = uhd.usrp_sink(
            dev,
            uhd.stream_args(cpu_format="fc32", otw_format=a.otw, channels=[a.tx_chan]),
        )
        self.snk.set_samp_rate(a.bw)
        self.snk.set_center_freq(uhd.tune_request(a.freq, a.lo_offset), 0)
        self.snk.set_gain(a.tx_gain, 0)
        self.snk.set_antenna(a.tx_ant, 0)

        self.connect((self.fsrc, 0), (self.amp, 0), (self.snk, 0))

    def config_str(self):
        return (
            f"  TX: ant={self.snk.get_antenna(0)} freq={self.snk.get_center_freq(0)/1e9:.4f} GHz "
            f"rate={self.snk.get_samp_rate()/1e6:g} MS/s gain={self.snk.get_gain(0):g} dB"
        )


def main():
    p = argparse.ArgumentParser(description="over-air 802.11 capture replay TX (B210)")
    p.add_argument("--in", dest="infile", required=True,
                   help="baseband cf32 capture from tools_gen_wifi.py (carries the canonical SA)")
    p.add_argument("--args", default="type=b200")
    p.add_argument("--bw", type=float, default=20e6,
                   help="sample rate (Hz) = the rate the capture was generated at "
                        "(20e6 legacy/HT20/VHT20, 40e6 HT40)")
    p.add_argument("--freq", type=float, default=5180e6,
                   help="RF center (Hz); 5180e6 = 5 GHz ch36, 2437e6 = 2.4 GHz ch6")
    p.add_argument("--lo-offset", dest="lo_offset", type=float, default=0.0,
                   help="LO offset (Hz); MUST be < Fs/2. 0 is safest (802.11 nulls DC).")
    p.add_argument("--tx-gain", dest="tx_gain", type=float, default=60.0)
    p.add_argument("--amp", type=float, default=1.0,
                   help="digital amplitude scale on the cf32 (captures are ~0.5 peak)")
    p.add_argument("--otw", default="sc8", help="over-the-wire fmt; sc8 halves USB load")
    p.add_argument("--tx-chan", dest="tx_chan", type=int, default=0)  # 0 -> frontend A (TRXA)
    p.add_argument("--tx-ant", dest="tx_ant", default="TX/RX")
    p.add_argument("--secs", type=float, default=15.0)
    p.add_argument("--check", action="store_true",
                   help="open + configure the B210 and print config, but do NOT transmit")
    a = p.parse_args()

    print(f"[rf-tx-air] in={a.infile}  bw={a.bw/1e6:g} MHz  freq={a.freq/1e9:.4f} GHz  "
          f"otw={a.otw}  TX=ch{a.tx_chan}/{a.tx_ant}  gain={a.tx_gain:g} dB")
    tb = rf_tx_air(a)
    print("[rf-tx-air] device opened, configured:")
    print(tb.config_str())

    if a.check:
        print("[rf-tx-air] --check: not transmitting. Start the devourer RX "
              "(WiFiDriverDemo, DEVOURER_STREAM_OUT=1, same freq+bw), then run without --check.")
        return 0

    print(f"[rf-tx-air] transmitting for {a.secs:g}s — radiating on a live Wi-Fi channel.")
    tb.start()
    time.sleep(a.secs)
    tb.stop()
    tb.wait()
    print("[rf-tx-air] done. Check the devourer RX for <devourer-stream> lines carrying "
          "the canonical SA 57:42:75:05:d6:00 and compare rate/bw via desc_rate.py.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
