#!/usr/bin/env python3
"""Rung-3 OVER-REAL-AIR fused FEC: devourer chip TX → B210 SDR RX.

The scenario-2 path (drone-disposable Wi-Fi TX, SDR ground RX) over genuine
air, vs the rung-1 software-channel capstone (fused_fec_rung1.py). A devourer
RTL8812AU transmits SBI-framed RS-FEC payloads; the B210 + gr-ieee802-11 fork
decodes them with GR_KEEP_CORRUPTED so FCS-failed frames are surfaced
(crc_ok=#f); the SAME FusedFecReceiver as the chip path salvages the CRC-valid
sub-blocks and reports baseline-vs-SBI gain.

TX at HT MCS7 (HT20, 64-QAM 5/6) — same fragile operating point as the chip
path: the robust BPSK preamble/HT-SIG keep each frame DETECTED and surfaced
while the 64-QAM data fails the FCS → corrupt-but-received, the salvage regime.
The fork decodes HT20 (MCS0-7) and surfaces FCS failures from frame_equalizer's
'pdu' port under GR_KEEP_CORRUPTED.

TX side (separate process, devourer):
  cd ~/git/devourer
  python3 tools/precoder/fused_fec_tx.py --input src.bin --repeat 40 \
      --symbol-size 32 --overhead 0.25 --blocks-per-body 10 > /tmp/bodies.bin
  sudo env DEVOURER_PID=0x8812 DEVOURER_CHANNEL=6 DEVOURER_TX_RATE=MCS7 \
       build/StreamTxDemo < /tmp/bodies.bin

Both sides on ch6 / 2.437 GHz / 20 MHz. Env via run_rx.sh / the CLAUDE.md recipe.
"""
import argparse
import os
import sys
import time

# decode_mac reads GR_KEEP_CORRUPTED once (static) at first decode — set early.
os.environ.setdefault("GR_KEEP_CORRUPTED", "1")

import pmt
from gnuradio import blocks, gr, uhd

import ieee802_11
from wifi_phy_hier import wifi_phy_hier

sys.path.insert(0, os.path.expanduser("~/git/devourer/tools/precoder"))
from fused_fec_link import FusedFecReceiver  # noqa: E402
from stream_fec import FecConfig  # noqa: E402

CANONICAL_SA = [0x57, 0x42, 0x75, 0x05, 0xd6, 0x00]  # StreamTxDemo SA (addr2)
MAC_HDR = 24  # devourer probe-req 802.11 header before the SBI body


class rx_tb(gr.top_block):
    def __init__(self, a):
        gr.top_block.__init__(self, "fused-fec rung3 RX")
        dev = ",".join((a.args, ""))
        self.phy = wifi_phy_hier(bandwidth=a.bw, chan_est=ieee802_11.LS,
                                 encoding=ieee802_11.BPSK_1_2,
                                 frequency=a.freq, sensitivity=0.56)
        self.dbg = blocks.message_debug()
        self.src = uhd.usrp_source(
            dev, uhd.stream_args(cpu_format="fc32", otw_format=a.otw,
                                 channels=[a.rx_chan]))
        self.src.set_samp_rate(a.bw)
        self.src.set_center_freq(uhd.tune_request(a.freq, a.lo_offset), 0)
        self.src.set_gain(a.rx_gain, 0)
        self.src.set_antenna(a.rx_ant, 0)
        self.null = blocks.null_sink(gr.sizeof_gr_complex)
        self.connect((self.src, 0), (self.phy, 0))
        self.connect((self.phy, 0), (self.null, 0))
        self.msg_connect(self.phy, "mac_out", self.dbg, "store")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--args", default="type=b200")
    p.add_argument("--bw", type=float, default=20e6)
    p.add_argument("--freq", type=float, default=2437e6)  # ch6
    p.add_argument("--lo-offset", dest="lo_offset", type=float, default=0.0)
    p.add_argument("--rx-gain", dest="rx_gain", type=float, default=50.0)
    p.add_argument("--otw", default="sc8")
    p.add_argument("--rx-chan", dest="rx_chan", type=int, default=0)
    p.add_argument("--rx-ant", dest="rx_ant", default="RX2")
    p.add_argument("--secs", type=float, default=20.0)
    # FEC params — MUST match the TX side.
    p.add_argument("--k", type=int, default=8)
    p.add_argument("--symbol-size", type=int, default=32)
    p.add_argument("--overhead", type=float, default=0.25)
    p.add_argument("--blocks-per-body", type=int, default=10)
    a = p.parse_args()

    print(f"[rung3] RX freq={a.freq/1e9:.4f} GHz bw={a.bw/1e6:g} MS/s "
          f"gain={a.rx_gain:g} dB ant={a.rx_ant}  GR_KEEP_CORRUPTED="
          f"{os.environ.get('GR_KEEP_CORRUPTED')}")
    tb = rx_tb(a)
    tb.start()
    time.sleep(a.secs)
    tb.stop()
    tb.wait()

    cfg = FecConfig(k=a.k, symbol_size=a.symbol_size, overhead=a.overhead,
                    scheme="rs")
    rcv = FusedFecReceiver(cfg, a.blocks_per_body)

    n = tb.dbg.num_messages()
    total = devourer = 0
    for i in range(n):
        msg = tb.dbg.get_message(i)
        meta, blob = pmt.car(msg), pmt.cdr(msg)
        if not pmt.is_u8vector(blob):
            continue
        total += 1
        data = list(pmt.u8vector_elements(blob))
        if len(data) < MAC_HDR + 8:
            continue
        # Do NOT filter by SA: the SA lives in the (corruptible) 64-QAM data
        # field, so a corrupt devourer frame — exactly what we want to salvage —
        # may have a garbled SA. Feed EVERY frame to the receiver; ambient
        # 802.11 is rejected downstream because its random bytes fail the SBI
        # sub-block CRCs AND the RS envelope magic (0xF540). We still count
        # clean-SA matches for information.
        if data[10:16] == CANONICAL_SA:
            devourer += 1
        body = bytes(data[MAC_HDR:])
        v = pmt.dict_ref(meta, pmt.intern("crc_ok"), pmt.PMT_NIL)
        crc_ok = pmt.to_bool(v) if pmt.is_bool(v) else True
        rcv.add_frame(body, crc_err=not crc_ok)

    r = rcv.report()
    gain = r.sbi_blocks - r.base_blocks
    print(f"[rung3] decoded {total} 802.11 frames total; {devourer} from the "
          f"devourer SA")
    print(f"[rung3] frames={r.frames_seen} corrupt={r.frames_corrupt} "
          f"sub-blocks={r.subblocks_total} salvaged={r.subblocks_salvaged}")
    print(f"[rung3] baseline blocks={r.base_blocks} pkts={r.base_packets}")
    print(f"[rung3] sbi      blocks={r.sbi_blocks} pkts={r.sbi_packets}")
    print(f"[rung3] FUSED-FEC GAIN = {gain} block(s) recovered over real air")
    print(f"[rung3] (clean-SA devourer frames seen: {devourer} / {total} decoded)")
    if r.sbi_blocks == 0:
        print("RESULT: no devourer RS blocks decoded — likely RX overload (the "
              "8812 is close): LOWER --rx-gain (try 10-30). Confirm 8812 TXing "
              "MCS7 on ch6/2437. ambient-only means the front end is saturating "
              "on the strong nearby TX.")
        return 1
    print("RESULT: PASS — fused-FEC salvage over REAL AIR (SDR RX, HT MCS7)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
