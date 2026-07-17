#!/usr/bin/env python3
"""Raw-energy probe for on-air Trigger detection — complements wifi_rx_sniff.py.

The GNU Radio decoder only proves a Trigger aired if it demodulates as a legacy
OFDM frame. To rule out a Trigger that aired in an *undecodable* PPDU (e.g. HE),
this measures raw received power over short windows on a quiet channel, so a
transmitter turning on shows as a power step regardless of PHY format.

Usage — A/B around a devourer firing burst:
  # baseline (transmitter idle):
  ./trigger_energy_probe.py --freq 5825e6 --secs 4
  # then start the devourer firing and re-run:
  ./trigger_energy_probe.py --freq 5825e6 --secs 4

Or --survey to sweep several 5 GHz channels for the quietest (lowest floor).
"""
import argparse
import sys
import time

import numpy as np
import uhd


def measure(freq, secs, gain, rate=20e6, ant="RX2", args="type=b200"):
    usrp = uhd.usrp.MultiUSRP(args)
    usrp.set_rx_rate(rate, 0)
    usrp.set_rx_freq(uhd.types.TuneRequest(freq), 0)
    usrp.set_rx_gain(gain, 0)
    usrp.set_rx_antenna(ant, 0)
    st = uhd.usrp.StreamArgs("fc32", "sc8")
    st.channels = [0]
    rx = usrp.get_rx_stream(st)
    buf = np.zeros((1, 4096), dtype=np.complex64)
    md = uhd.types.RXMetadata()
    sc = uhd.types.StreamCMD(uhd.types.StreamMode.start_cont)
    sc.stream_now = True
    rx.issue_stream_cmd(sc)
    # Per-window mean power; the peak window catches a bursty transmitter that a
    # long-average would wash out against the noise floor.
    pk, tot, nwin = -200.0, 0.0, 0
    t_end = time.time() + secs
    while time.time() < t_end:
        n = rx.recv(buf, md)
        if n == 0 or md.error_code != uhd.types.RXMetadataErrorCode.none:
            continue
        p = float(np.mean(np.abs(buf[0, :n]) ** 2)) + 1e-12
        pdb = 10.0 * np.log10(p)
        pk = max(pk, pdb)
        tot += p
        nwin += 1
    rx.issue_stream_cmd(uhd.types.StreamCMD(uhd.types.StreamMode.stop_cont))
    mean_db = 10.0 * np.log10(tot / max(nwin, 1) + 1e-12)
    return mean_db, pk, nwin


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--freq", type=float, default=5825e6)
    p.add_argument("--secs", type=float, default=4.0)
    p.add_argument("--gain", type=float, default=60.0)
    p.add_argument("--survey", action="store_true",
                   help="sweep 5 GHz channels, report mean/peak power to find a quiet one")
    a = p.parse_args()

    if a.survey:
        chans = [36, 40, 44, 48, 100, 149, 157, 161, 165]
        print("ch   freq(MHz)  mean(dB)  peak(dB)  windows")
        for ch in chans:
            f = (5000 + 5 * ch) * 1e6
            m, pk, nw = measure(f, 2.0, a.gain)
            print(f"{ch:<4} {f/1e6:<9.0f} {m:8.1f}  {pk:8.1f}  {nw}")
        return 0

    m, pk, nw = measure(a.freq, a.secs, a.gain)
    print(f"[energy] freq={a.freq/1e6:.0f} MHz secs={a.secs:g} gain={a.gain:g}dB "
          f"-> mean={m:.1f} dB  peak={pk:.1f} dB  ({nw} windows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
