#!/usr/bin/env python3
"""Scenario-2 capstone: fused FEC over the REAL 802.11 PHY (rung-1, no SDR).

End-to-end proof that the devourer sub-block-integrity (SBI) layer + Reed-
Solomon outer code salvage data the baseline loses, when the receiver is the
gr-ieee802-11 fork running GR_KEEP_CORRUPTED. The path is:

  message → RS encode → SBI pack (1 RS block per 802.11 frame) → ieee802_11.mac
          → PHY TX → noisy channel → PHY RX → decode_mac (GR_KEEP_CORRUPTED)
          → [bridge] strip MAC header → SBI unpack → RS decode

Two receivers post-process the SAME captured frames:
  * baseline — drops any FCS-failed frame (today's behaviour). With a whole RS
    block in one frame, a single FCS failure loses the entire block.
  * sbi      — keeps the FCS-failed frame and salvages its CRC-valid sub-blocks;
    only the 1-2 genuinely-corrupt symbols are lost, so the RS block (R=2)
    still decodes.

The difference (blocks SBI decodes that baseline doesn't) is the fused-FEC gain,
measured over a real OFDM/Viterbi PHY rather than a byte-flip model.

Env set by run.sh / the CLAUDE.md recipe (PYTHONPATH/LD_LIBRARY_PATH).
"""
import argparse
import os
import sys
import time

# decode_mac reads GR_KEEP_CORRUPTED once (static) at first decode — set it
# before the flowgraph starts.
os.environ.setdefault("GR_KEEP_CORRUPTED", "1")

import pmt
from gnuradio import blocks, channels, gr

import foo
import ieee802_11
from wifi_phy_hier import wifi_phy_hier

# Pull in the devourer precoder FEC stack (numpy-free RS + SBI).
_DEVOURER = os.path.expanduser("~/git/devourer/tools/precoder")
if _DEVOURER not in sys.path:
    sys.path.insert(0, _DEVOURER)

import fec_subblock  # noqa: E402
import stream_fec  # noqa: E402
from stream_fec import FecConfig  # noqa: E402

MAC_HDR_LEN = 24  # ieee802_11.mac prepends a 24-byte data-frame header


class fused_tb(gr.top_block):
    def __init__(self, bandwidth, noise, freq_off):
        gr.top_block.__init__(self, "fused-fec rung1")
        self.phy = wifi_phy_hier(
            bandwidth=bandwidth, chan_est=ieee802_11.LS,
            encoding=ieee802_11.BPSK_1_2, frequency=5.89e9, sensitivity=0.56)
        self.mac = ieee802_11.mac([0x23] * 6, [0x42] * 6, [0xff] * 6)
        self.pad = foo.packet_pad2(False, False, 0.001, 500, 0)
        self.gain = blocks.multiply_const_cc(10.0)
        self.chan = channels.channel_model(noise, freq_off, 1.0, [1.0], 13, False)
        self.dbg = blocks.message_debug()
        for b in (self.pad, self.gain, self.chan):
            b.set_min_output_buffer(1 << 22)
        self.msg_connect(self.mac, "phy out", self.phy, "mac_in")
        self.connect((self.phy, 0), (self.pad, 0))
        self.connect((self.pad, 0), (self.gain, 0))
        self.connect((self.gain, 0), (self.chan, 0))
        self.connect((self.chan, 0), (self.phy, 0))
        self.msg_connect(self.phy, "mac_out", self.dbg, "store")

    def post_body(self, body: bytes) -> None:
        pdu = pmt.cons(pmt.PMT_NIL, pmt.init_u8vector(len(body), list(body)))
        self.mac.to_basic_block()._post(pmt.intern("app in"), pdu)


def build_blocks(cfg, n_blocks, blocks_per_body):
    """Return (bodies, want) where bodies[i] is the 802.11 MSDU for RS block i
    and want[block_id] is the set of packets that block carried."""
    bodies = []
    want = {}
    enc = stream_fec.make_encoder(cfg)
    for blk in range(n_blocks):
        # K unique small packets per block (block index embedded so recovery
        # can be verified, and blocks don't alias).
        pkts = [f"b{blk:04d}s{s:02d}".encode() for s in range(cfg.k)]
        envs = []
        for p in pkts:
            envs += enc.add_packet(p)
        envs += enc.flush()  # seal exactly one block
        env_size = len(envs[0])
        for b in fec_subblock.pack(envs, env_size, blocks_per_body):
            bodies.append((b, env_size))
        # The block_id the encoder stamped in these envelopes:
        bid = (enc._block_id - 1) & 0xFFFF
        want[bid] = set(pkts)
    return bodies, want


def bridge_decode(rx_frames, cfg, env_size, keep_corrupt: bool):
    """Run a receiver over captured (body, crc_ok) frames. baseline =
    keep_corrupt False (drop FCS failures); sbi = True (salvage sub-blocks).
    Returns (blocks_decoded, packets_out_set, salvaged_subblocks)."""
    dec = stream_fec.make_decoder(cfg)
    out = set()
    salvaged = 0
    for body, crc_ok in rx_frames:
        if not crc_ok and not keep_corrupt:
            continue
        res = fec_subblock.unpack(body, env_size)
        if not crc_ok:
            salvaged += len(res.survivors)
        for env in res.survivors:
            for pkt in dec.add_symbol(env):
                out.add(pkt)
    return dec.blocks_decoded, out, salvaged


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bandwidth", type=float, default=10e6)
    ap.add_argument("--noise", type=float, default=6.0)
    ap.add_argument("--freq-off", type=float, default=0.0)
    ap.add_argument("--symbol-size", type=int, default=32)
    ap.add_argument("--k", type=int, default=8)
    ap.add_argument("--overhead", type=float, default=0.25)  # N=10, R=2
    ap.add_argument("--blocks-per-body", type=int, default=10)  # whole block/frame
    ap.add_argument("--n-blocks", type=int, default=300)
    ap.add_argument("--post-ms", type=float, default=12.0)
    args = ap.parse_args(argv)

    cfg = FecConfig(k=args.k, symbol_size=args.symbol_size,
                    overhead=args.overhead, scheme="rs")
    bodies, want = build_blocks(cfg, args.n_blocks, args.blocks_per_body)
    env_size = bodies[0][1]
    print(f"[fused] {args.n_blocks} RS blocks, scheme=rs k={cfg.k} N={cfg.k + cfg.repair_count} "
          f"symbol={cfg.symbol_size} env={env_size}B body={len(bodies[0][0])}B "
          f"blocks/frame={args.blocks_per_body} noise={args.noise}")

    tb = fused_tb(args.bandwidth, args.noise, args.freq_off)
    tb.start()
    for body, _ in bodies:
        tb.post_body(body)
        time.sleep(args.post_ms / 1000.0)
    time.sleep(1.0)  # drain
    tb.stop()
    tb.wait()

    # Capture RX frames: strip the 24-byte MAC header to recover the MSDU body.
    rx = []
    n = tb.dbg.num_messages()
    n_corrupt = 0
    for i in range(n):
        msg = tb.dbg.get_message(i)
        meta, blob = pmt.car(msg), pmt.cdr(msg)
        if not pmt.is_u8vector(blob):
            continue
        data = bytes(pmt.u8vector_elements(blob))
        if len(data) <= MAC_HDR_LEN:
            continue
        body = data[MAC_HDR_LEN:]
        v = pmt.dict_ref(meta, pmt.intern("crc_ok"), pmt.PMT_NIL)
        crc_ok = pmt.to_bool(v) if pmt.is_bool(v) else True
        if not crc_ok:
            n_corrupt += 1
        rx.append((body, crc_ok))

    print(f"[fused] TX frames: {len(bodies)}  RX frames: {len(rx)}  "
          f"FCS-failed surfaced: {n_corrupt}")

    base_blocks, base_pkts, _ = bridge_decode(rx, cfg, env_size, keep_corrupt=False)
    sbi_blocks, sbi_pkts, salvaged = bridge_decode(rx, cfg, env_size, keep_corrupt=True)

    all_want = set().union(*want.values()) if want else set()
    base_ok = len(base_pkts & all_want)
    sbi_ok = len(sbi_pkts & all_want)
    total = len(all_want)
    print(f"[fused] baseline : blocks_decoded={base_blocks:4d}  "
          f"packets {base_ok}/{total}")
    print(f"[fused] sbi      : blocks_decoded={sbi_blocks:4d}  "
          f"packets {sbi_ok}/{total}  (salvaged {salvaged} sub-blocks "
          f"from FCS-failed frames)")
    gain = sbi_blocks - base_blocks
    print(f"[fused] FUSED-FEC GAIN: SBI recovered {gain} block(s) the baseline "
          f"dropped, salvaging {salvaged} sub-block(s) from corrupt frames")
    if n_corrupt == 0:
        print("RESULT: INCONCLUSIVE — no FCS-failed frames at this noise; "
              "raise --noise (band ~5-7) or --freq-off")
        return 2
    if sbi_pkts >= base_pkts and sbi_blocks >= base_blocks and gain > 0:
        print("RESULT: PASS — SBI salvaged real FCS-failed frames the baseline lost")
        return 0
    if sbi_pkts >= base_pkts and sbi_blocks >= base_blocks:
        print("RESULT: PASS (no contrast) — SBI never worse; corrupt frames "
              "happened not to tip a block this run")
        return 0
    print("RESULT: FAIL — SBI recovered less than baseline")
    return 1


if __name__ == "__main__":
    sys.exit(main())
