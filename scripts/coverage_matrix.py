#!/usr/bin/env python3
"""SDR completeness coverage matrix for the devourer driver (TX direction).

For each (format, MCS) cell: devourer transmits the frame, the B210 captures it,
and the gr-ieee802-11 fork blind-decodes it. We record whether the SIG field
decodes to the EXPECTED MCS (the core completeness check -- proves devourer
transmitted the right modulation/format) and whether the DATA FCS passes (a
bonus, gated by the over-air link SNR).

This drives the single-cell capturer `capture_analyze_vht.sh` (which verifies
devourer's on-air rate before capturing), recovers a wedged adapter between
cells via lkl-wifi-poc/recover-chip.sh, and emits a text + CSV report.

Result legend:
  SIG+DATA  decode confirmed AND data FCS passed (full validation)
  SIG-ONLY  SIG decoded to the expected MCS, data FCS failed (link-SNR limited)
  NO-DECODE no SIG decode (weak/empty capture -- retried after a chip recover)
  SKIP      not reachable on one B210 + this fork (e.g. 80/160 MHz VHT)

Usage:
  sudo python3 scripts/coverage_matrix.py                 # default HT+VHT sweep, ch36
  sudo python3 scripts/coverage_matrix.py --gain 60 --retries 2
"""
import argparse
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
RECOVER = os.path.expanduser("~/git/lkl-wifi-poc/scripts/recover-chip.sh")

# (format, mcs, reachable, note). format: 'ht' | 'vht'. Legacy is separately proven
# both directions (rung-3) and is listed for completeness.
MATRIX = [
    ("ht", 0, True, "BPSK 1/2"),
    ("ht", 1, True, "QPSK 1/2"),
    ("ht", 3, True, "16-QAM 1/2"),
    ("ht", 5, True, "64-QAM 2/3"),
    ("ht", 7, True, "64-QAM 5/6"),
    ("vht", 0, True, "VHT BPSK 1/2"),
    ("vht", 4, True, "VHT 16-QAM 3/4"),
    ("vht", 9, False, "VHT MCS9 80MHz -> not capturable on one B210"),
]


def recover_chip():
    if os.path.exists(RECOVER):
        subprocess.run(["bash", RECOVER, "0bda:8812"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


sys.path.insert(0, REPO)
import desc_rate


def run_cell(mode, mcs, gain):
    """TX direction: devourer transmits, SDR blind-decodes. Returns parsed dict."""
    out = subprocess.run(
        ["bash", os.path.join(HERE, "capture_analyze_vht.sh"), mode, str(mcs), str(gain)],
        cwd=REPO, capture_output=True, text=True, timeout=300).stdout
    rms = _grep1(r"capture RMS:\s*([0-9.]+)", out)
    sig_hit = re.findall(r"CRC-OK mcs=(\d+)", out).count(str(mcs))
    data_pass = int(_grep1(r"PASS=(\d+)", out) or 0)
    return dict(rms=rms, sig_hit=sig_hit, data_pass=data_pass, raw=out)


def run_rx_cell(mode, mcs):
    """RX direction: a GR-WiFi SPEC frame is transmitted over the SDR; devourer
    receives it. PASS = devourer reports a clean (crc_err=0) frame whose decoded
    DESC_RATE matches the expected (format,mcs)."""
    out = subprocess.run(
        ["bash", os.path.join(HERE, "rx_grwifi_spec.sh"), mode, str(mcs)],
        cwd=REPO, capture_output=True, text=True, timeout=120).stdout
    n = int(_grep1(r"stream.* lines:\s*(\d+)", out) or 0)
    # the rate devourer decoded, and whether the FCS was clean
    rate = _grep1(r"<devourer-stream>rate=(\d+)", out)
    clean = bool(re.search(r"rate=\d+ len=\d+ crc_err=0", out))
    expect = desc_rate.encode_ht(mcs) if mode == "ht" else desc_rate.encode_vht(1, mcs)
    match = rate is not None and int(rate) == expect and clean
    return dict(n=n, rate=rate, expect=expect, match=match)


def _grep1(pat, text):
    m = re.search(pat, text)
    return m.group(1) if m else None


def classify(cell):
    if cell["data_pass"] > 0:
        return "SIG+DATA"
    if cell["sig_hit"] > 0:
        return "SIG-ONLY"
    return "NO-DECODE"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--gain", type=int, default=65, help="B210 RX gain (dB)")
    p.add_argument("--retries", type=int, default=1, help="retries (with chip recover) on NO-DECODE")
    # Default to a QUIET UNII-3 channel: devourer's weak monitor-injection TX is drowned
    # by ambient APs on congested UNII-1 (ch36/44/48). ch149/5745 is dead silent here
    # (scripts/channel_survey.sh). The cell scripts honour CH/FREQ via the environment.
    p.add_argument("--channel", type=int, default=149)
    p.add_argument("--freq", default="5745e6")
    p.add_argument("--csv", default="/tmp/coverage_matrix.csv")
    a = p.parse_args()
    os.environ["CH"] = str(a.channel)
    os.environ["FREQ"] = a.freq

    rows = []
    print(f"{'cell':12s} {'TX(dev->SDR)':16s} {'RX(SDR->dev)':16s}  note")
    for mode, mcs, reachable, note in MATRIX:
        label = f"{mode.upper()} MCS{mcs}"
        if not reachable:
            print(f"{label:12s} {'SKIP':16s} {'SKIP':16s}  {note}")
            rows.append((label, "SKIP", "SKIP", note))
            continue
        # TX direction (with chip-recover retry on no-decode)
        tx = None
        for _ in range(a.retries + 1):
            try:
                cell = run_cell(mode, mcs, a.gain)
            except subprocess.TimeoutExpired:
                cell = dict(rms="?", sig_hit=0, data_pass=0, raw="timeout")
            tx = classify(cell)
            if tx != "NO-DECODE":
                break
            recover_chip()
        # RX direction
        recover_chip()
        try:
            rx = run_rx_cell(mode, mcs)
            rxres = "RX-OK" if rx["match"] else ("RX-PART" if rx["n"] else "RX-LINK")
        except subprocess.TimeoutExpired:
            rxres = "RX-LINK"
            rx = dict(rate=None, expect=None)
        txtag = {"SIG+DATA": "OK", "SIG-ONLY": "~", "NO-DECODE": "X"}[tx]
        rxtag = {"RX-OK": "OK", "RX-PART": "~", "RX-LINK": "X"}[rxres]
        print(f"{label:12s} {tx+' ['+txtag+']':16s} "
              f"{rxres+' ['+rxtag+'] rate='+str(rx.get('rate')):16s}  {note}")
        rows.append((label, tx, rxres, note))

    with open(a.csv, "w") as f:
        f.write("cell,tx_direction,rx_direction,note\n")
        for r in rows:
            f.write(",".join(str(x) for x in r) + "\n")
    print(f"\nMatrix written to {a.csv}")
    print("TX legend: SIG+DATA=full / SIG-ONLY=format-confirmed,data-link-limited / NO-DECODE=weak.")
    print("RX legend: RX-OK=devourer decoded correct MCS+clean FCS / RX-PART=received,rate-mismatch / RX-LINK=not received (SNR).")
    print("Legacy 6M is separately proven both directions (rung-3 + rx_direction_check).")


if __name__ == "__main__":
    sys.exit(main())
