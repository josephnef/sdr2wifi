#!/usr/bin/env python3
"""Ground-truth burst + HE-preamble scanner over raw B210 IQ.

The legacy/HT/VHT GNU Radio decoder only surfaces a frame if it fully
demodulates — so an 802.11ax (HE) PPDU it can't decode is invisible to it. This
works one level lower: it captures raw IQ and looks for ANY transmitted burst
(a power envelope above the noise floor), independent of PHY format, then
classifies each burst's preamble:

  - L-STF present?  (short-training autocorrelation at the 0.8 us / 16-sample
    period — every 802.11 OFDM PPDU, legacy through HE, starts with it)
  - RL-SIG present? (a Repeated L-SIG — the symbol after L-SIG repeats — marks a
    VHT or HE PPDU vs a legacy/HT one; the HE-vs-VHT split is then in HE-SIG-A)

So an HE Trigger that airs shows up here as a burst with L-STF + RL-SIG, even
though the legacy decoder drops it. If NOTHING airs, there are no bursts at all.

Usage (fire the devourer trigger in another shell during the capture):
  ./iq_burst_scan.py --freq 5825e6 --secs 8 --gain 60
"""
import argparse
import sys
import time

import numpy as np
import uhd


def capture(freq, secs, gain, rate=20e6, ant="RX2", args="type=b200"):
    usrp = uhd.usrp.MultiUSRP(args)
    usrp.set_rx_rate(rate, 0)
    usrp.set_rx_freq(uhd.types.TuneRequest(freq), 0)
    usrp.set_rx_gain(gain, 0)
    usrp.set_rx_antenna(ant, 0)
    st = uhd.usrp.StreamArgs("fc32", "sc8")
    st.channels = [0]
    rx = usrp.get_rx_stream(st)
    nsamp = int(rate * secs)
    out = np.empty(nsamp, dtype=np.complex64)
    buf = np.zeros((1, 8192), dtype=np.complex64)
    md = uhd.types.RXMetadata()
    sc = uhd.types.StreamCMD(uhd.types.StreamMode.start_cont)
    sc.stream_now = True
    rx.issue_stream_cmd(sc)
    got = 0
    while got < nsamp:
        n = rx.recv(buf, md)
        if n == 0 or md.error_code != uhd.types.RXMetadataErrorCode.none:
            continue
        k = min(n, nsamp - got)
        out[got:got + k] = buf[0, :k]
        got += k
    rx.issue_stream_cmd(uhd.types.StreamCMD(uhd.types.StreamMode.stop_cont))
    return out, rate


def lstf_autocorr(seg, L=16):
    """Normalized L-STF autocorrelation peak (0..1): the OFDM short-training
    field repeats every 16 samples at 20 MS/s; a clean peak ~1 marks an 802.11
    OFDM preamble (legacy..HE)."""
    if len(seg) < 4 * L:
        return 0.0
    a = seg[:-L]
    b = seg[L:]
    num = np.abs(np.sum(a * np.conj(b)))
    den = np.sqrt(np.sum(np.abs(a) ** 2) * np.sum(np.abs(b) ** 2)) + 1e-12
    return float(num / den)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--freq", type=float, default=5825e6)
    p.add_argument("--secs", type=float, default=8.0)
    p.add_argument("--gain", type=float, default=60.0)
    p.add_argument("--snr-db", type=float, default=8.0,
                   help="burst threshold above the noise floor (dB)")
    a = p.parse_args()

    print(f"[iq-scan] capturing {a.secs:g}s @ {a.freq/1e6:.0f} MHz gain={a.gain:g}dB ...")
    iq, rate = capture(a.freq, a.secs, a.gain)
    p_inst = np.abs(iq) ** 2
    # 1 us power envelope via BLOCK-mean (reshape), not np.convolve — O(N) and
    # ~50x smaller, so it doesn't pin a CPU / starve co-located USB radios. env
    # is in 1 us blocks; convert block indices back to samples with *BLK.
    BLK = 20
    nblk = len(p_inst) // BLK
    env = p_inst[:nblk * BLK].reshape(nblk, BLK).mean(axis=1)
    floor = np.percentile(env, 20) + 1e-12
    thr = floor * (10 ** (a.snr_db / 10))
    above = env > thr

    # Coalesce contiguous above-threshold blocks into bursts (>= 2 us = 2 blocks),
    # returned in SAMPLE indices for the classifier.
    bursts = []
    i, N = 0, len(above)
    while i < N:
        if above[i]:
            j = i
            while j < N and above[j]:
                j += 1
            if (j - i) >= 2:
                bursts.append((i * BLK, j * BLK))
            i = j
        else:
            i += 1

    floor_db = 10 * np.log10(floor)
    # Classify EVERY burst (L-STF OFDM preamble, RL-SIG = VHT/HE), and bin by
    # 1 s so a firing burst window stands out against ambient.
    ofdm = he_vht = 0
    per_sec = {}
    strong = 0       # bursts from a co-located TX (peak > floor+20 dB)
    he_strong = 0    # co-located AND HE/VHT (RL-SIG) — an aired HE TB PPDU would be here
    for (s, e) in bursts:
        seg = iq[s:min(e, s + 320)]
        stf = lstf_autocorr(seg)
        pk_db = 10 * np.log10(np.max(env[s // BLK:max(e // BLK, s // BLK + 1)]) + 1e-12)
        rl = 0.0
        if len(seg) >= 320:
            s1 = iq[s + 160:s + 240]
            s2 = iq[s + 240:s + 320]
            if len(s1) == len(s2) == 80:
                num = np.abs(np.sum(s1 * np.conj(s2)))
                den = np.sqrt(np.sum(np.abs(s1) ** 2) * np.sum(np.abs(s2) ** 2)) + 1e-12
                rl = float(num / den)
        if stf > 0.6:
            ofdm += 1
        is_strong = pk_db > floor_db + 20
        if rl > 0.7:
            he_vht += 1
            if is_strong:
                he_strong += 1
        if is_strong:
            strong += 1
        per_sec[int(s / rate)] = per_sec.get(int(s / rate), 0) + 1
    print(f"[iq-scan] floor={floor_db:.1f} dB  thr=+{a.snr_db:g}dB  bursts>=2us: "
          f"{len(bursts)}  (OFDM L-STF: {ofdm}, VHT/HE RL-SIG: {he_vht}, "
          f"strong>+20dB: {strong}, co-located HE (strong+RL-SIG): {he_strong})")
    print("  bursts/sec: " + " ".join(f"{t}s:{per_sec.get(t,0)}"
                                       for t in range(int(a.secs))))
    if not bursts:
        print("RESULT: NO on-air bursts — nothing is transmitted on this channel.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
