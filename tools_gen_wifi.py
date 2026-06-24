#!/usr/bin/env python3
"""Synthetic 802.11a/g (legacy) and 802.11n (HT) waveform generator.

Generates a baseband cf32 capture for a single frame so the gr-ieee802-11 RX
chain can be validated deterministically, with no SDR/RF. The modulator mirrors
gr-ieee802-11's own TX math (scrambler/conv-code/interleaver from lib/utils.cc)
and the L-LTF / pilot tables from lib/equalizer/base.cc, so a correct RX must
decode it.

Validation strategy (breaks circularity):
  1. --mode legacy : the UPSTREAM RX decodes it -> validates this modulator's
     preamble/sync/OFDM/scrambler/BCC/interleaver/CRC independently of HT code.
  2. --mode ht     : reuses that validated base + adds HT-SIG/HT-LTF/HT-data ->
     exercises the new HT RX path (CRC-32 over the recovered PSDU).

  ./tools_gen_wifi.py --mode legacy --out /tmp/legacy.cf32
  ./tools_gen_wifi.py --mode ht --mcs 0 --out /tmp/ht.cf32
"""
import sys, argparse, struct, zlib
import numpy as np

# ---- tables (must match lib/equalizer/base.cc) --------------------------------
# L-LTF in fft-shifted bin order (index 32 = DC).
LONG = np.array([0,0,0,0,0,0,1,1,-1,-1,1,1,-1,1,-1,1,1,1,1,1,1,-1,-1,1,1,-1,
                 1,-1,1,1,1,1,0,1,-1,-1,1,1,-1,1,-1,1,-1,-1,-1,-1,-1,1,1,-1,
                 -1,1,-1,1,-1,1,1,1,1,0,0,0,0,0], dtype=np.complex64)
POLARITY = np.array([1,1,1,1,-1,-1,-1,1,-1,-1,-1,-1,1,1,-1,1,-1,-1,1,1,-1,1,1,-1,1,1,1,
                     1,1,1,-1,1,1,1,-1,1,1,-1,-1,1,1,1,-1,1,-1,-1,-1,1,-1,1,-1,-1,1,-1,
                     -1,1,1,1,1,1,-1,-1,1,1,-1,-1,1,-1,1,-1,1,1,-1,-1,-1,1,1,-1,-1,-1,-1,
                     1,-1,-1,1,-1,1,1,1,1,-1,1,-1,1,-1,1,-1,-1,-1,-1,-1,1,-1,1,1,-1,1,-1,
                     1,1,1,-1,-1,1,-1,-1,-1,1,1,1,-1,-1,-1,-1,-1,-1,-1], dtype=np.float64)

# legacy short training sequence (subcarrier -26..26), sqrt(13/6) scaled
def lstf_freq():
    f = np.zeros(64, dtype=np.complex64)
    vals = {-24:1+1j,-20:-1-1j,-16:1+1j,-12:-1-1j,-8:-1-1j,-4:1+1j,
            4:-1-1j,8:-1-1j,12:1+1j,16:1+1j,20:1+1j,24:1+1j}
    for k, v in vals.items():
        f[32 + k] = np.complex64(v) * np.sqrt(13.0 / 6.0)
    return f

DATA_SC_LEGACY = [i for i in range(6, 59) if i not in (11, 25, 32, 39, 53)]   # 48
DATA_SC_HT     = [i for i in range(4, 61) if i not in (11, 25, 32, 39, 53)]   # 52
PILOT_SC = [11, 25, 39, 53]
PILOT_VAL = np.array([1, 1, 1, -1], dtype=np.complex64)  # {-21,-7,7,21}


def ifft_sym(freq64):
    """freq (DC at index 32) -> 64 time samples."""
    return np.fft.ifft(np.fft.ifftshift(freq64)).astype(np.complex64)


def ofdm_symbol(freq64, cp=16):
    t = ifft_sym(freq64)
    return np.concatenate([t[64 - cp:], t]).astype(np.complex64)


# ---- bit-level coding (mirrors lib/utils.cc) ----------------------------------
def parity(x):
    return bin(x).count("1") & 1


def conv_encode(bits):
    out = np.empty(len(bits) * 2, dtype=np.uint8)
    state = 0
    for i, b in enumerate(bits):
        state = ((state << 1) & 0x7e) | int(b)
        out[2 * i] = parity(state & 0x6d)      # 0155
        out[2 * i + 1] = parity(state & 0x4f)  # 0117
    return out


def scramble(bits, init=0x5d):
    out = np.empty(len(bits), dtype=np.uint8)
    state = init
    for i, b in enumerate(bits):
        fb = (1 if state & 64 else 0) ^ (1 if state & 8 else 0)
        out[i] = fb ^ int(b)
        state = ((state << 1) & 0x7e) | fb
    return out


def interleave(bits, n_cbps, n_bpsc):
    """Legacy forward interleave (N_COL=16), one OFDM symbol at a time."""
    s = max(n_bpsc // 2, 1)
    first = [s * (j // s) + ((j + int(np.floor(16.0 * j / n_cbps))) % s) for j in range(n_cbps)]
    second = [16 * i - (n_cbps - 1) * int(np.floor(16.0 * i / n_cbps)) for i in range(n_cbps)]
    out = np.empty_like(bits)
    nsym = len(bits) // n_cbps
    for sym in range(nsym):
        base = sym * n_cbps
        for k in range(n_cbps):
            out[base + k] = bits[base + second[first[k]]]
    return out


def interleave_ht(bits, n_cbps, n_bpsc):
    """HT forward interleave (802.11 19.3.11.7): N_COL=13, N_ROW=4*N_BPSCS.
    Maps coded index k -> transmitted position j: il[j(k)] = coded[k]."""
    s = max(n_bpsc // 2, 1)
    n_col, n_row = 13, 4 * n_bpsc
    out = np.empty_like(bits)
    nsym = len(bits) // n_cbps
    for sym in range(nsym):
        base = sym * n_cbps
        for k in range(n_cbps):
            i = n_row * (k % n_col) + (k // n_col)
            j = s * (i // s) + (i + n_cbps - (n_col * i) // n_cbps) % s
            out[base + j] = bits[base + k]
    return out


def bytes_to_bits(data):
    bits = np.zeros(len(data) * 8, dtype=np.uint8)
    for i, byte in enumerate(data):
        for b in range(8):
            bits[i * 8 + b] = (byte >> b) & 1
    return bits


def ht_sig_crc8(bits34):
    c = 0xff
    for b in bits34:
        fb = (int(b) & 1) ^ ((c >> 7) & 1)
        c = (c << 1) & 0xff
        if fb:
            c ^= 0x07
    return (~c) & 0xff


# ---- field / symbol builders --------------------------------------------------
def map_bpsk(bits):
    return np.where(np.asarray(bits) > 0, 1.0, -1.0).astype(np.complex64)


def data_symbol(coded_bits_for_sym, data_sc, sym_idx, rotate=False):
    """One OFDM data/SIG symbol from n_data_sc BPSK bits + pilots."""
    freq = np.zeros(64, dtype=np.complex64)
    pts = map_bpsk(coded_bits_for_sym)
    if rotate:
        pts = pts * np.complex64(1j)  # QBPSK (HT-SIG)
    for c, sc in enumerate(data_sc):
        freq[sc] = pts[c]
    p = POLARITY[(sym_idx - 2) % 127]
    for pv, sc in zip(PILOT_VAL, PILOT_SC):
        freq[sc] = pv * p * (np.complex64(1j) if rotate else np.complex64(1))
    return ofdm_symbol(freq)


def build_sig_bits(rate4, length12):
    bits = np.zeros(24, dtype=np.uint8)
    for i in range(4):
        bits[i] = (rate4 >> i) & 1
    for i in range(12):
        bits[5 + i] = (length12 >> i) & 1
    bits[17] = np.bitwise_xor.reduce(bits[0:17]) & 1  # parity over 0..16
    return bits


def preamble():
    st = ifft_sym(lstf_freq())            # 64-sample, period-16
    lstf = np.tile(st[:16], 10)           # 160 samples
    lt = ifft_sym(LONG)
    lltf = np.concatenate([lt[32:], lt, lt])  # GI2(32) + 2x64 = 160
    return np.concatenate([lstf, lltf]).astype(np.complex64)


def gen_legacy(psdu, mcs_rate4=11):  # SIGNAL RATE field read as r=11 -> BPSK_1/2 (6 Mbps)
    n_dbps, n_cbps, n_bpsc = 24, 48, 1
    length = len(psdu)
    nsym = int(np.ceil((16 + 8 * length + 6) / n_dbps))
    data_bits = np.zeros(nsym * n_dbps, dtype=np.uint8)
    data_bits[16:16 + 8 * length] = bytes_to_bits(psdu)
    scr = scramble(data_bits)
    # reset the 6 tail bits to 0 (so the conv encoder terminates to state 0)
    tail_pos = 16 + 8 * length
    scr[tail_pos:tail_pos + 6] = 0
    coded = conv_encode(scr)              # rate 1/2 -> nsym*48
    il = interleave(coded, n_cbps, n_bpsc)
    sig = data_symbol(conv_then_il_sig(build_sig_bits(mcs_rate4, length)), DATA_SC_LEGACY, 2)
    syms = [sig]
    for j in range(nsym):
        syms.append(data_symbol(il[j * n_cbps:(j + 1) * n_cbps], DATA_SC_LEGACY, 3 + j))
    return np.concatenate([preamble()] + syms).astype(np.complex64)


def conv_then_il_sig(sig_bits24):
    coded = conv_encode(sig_bits24)        # 48
    return interleave(coded, 48, 1)


def gen_ht(psdu, mcs=0):
    # HT MCS0 = BPSK 1/2, 52 data SC
    n_bpsc, n_cbps, n_dbps = 1, 52, 26
    length = len(psdu)
    # L-SIG: rate=6M(0x0D), length picked so legacy duration covers HT frame
    nsym = int(np.ceil((16 + 8 * length + 6) / n_dbps))
    lsig_len = max(length + 16, 40)
    sig = data_symbol(conv_then_il_sig(build_sig_bits(11, lsig_len)), DATA_SC_LEGACY, 2)

    # HT-SIG: 48 info bits = HT-SIG1(24) + HT-SIG2(24); QBPSK over 2 symbols
    hsig = np.zeros(48, dtype=np.uint8)
    for i in range(7):
        hsig[i] = (mcs >> i) & 1          # MCS (0..6)
    hsig[7] = 0                            # CBW20
    for i in range(16):
        hsig[8 + i] = (length >> i) & 1    # HT-Length
    # HT-SIG2: smoothing..fec(BCC=0)..sgi(0)..n_ess(0), CRC8 over bits0..33, tail
    crc = ht_sig_crc8(hsig[0:34])
    for i in range(8):
        hsig[34 + i] = (crc >> (7 - i)) & 1   # MSB-first c7..c0
    hsig_coded = conv_encode(hsig)         # 96
    hsig_il = interleave(hsig_coded, 48, 1)
    htsig1 = data_symbol(hsig_il[0:48], DATA_SC_LEGACY, 3, rotate=True)
    htsig2 = data_symbol(hsig_il[48:96], DATA_SC_LEGACY, 4, rotate=True)

    # HT-STF (sym5), HT-LTF (sym6) -- RX skips both. Use FULL-BAND symbols (not
    # the periodic short-training pattern) so sync_short does not re-trigger
    # mid-frame and corrupt the symbol alignment.
    htstf = ofdm_symbol(LONG)
    htltf = ofdm_symbol(LONG)

    # HT DATA
    data_bits = np.zeros(nsym * n_dbps, dtype=np.uint8)
    data_bits[16:16 + 8 * length] = bytes_to_bits(psdu)
    scr = scramble(data_bits)
    scr[16 + 8 * length:16 + 8 * length + 6] = 0   # tail
    coded = conv_encode(scr)               # nsym*52
    il = interleave_ht(coded, n_cbps, n_bpsc)
    data_syms = [data_symbol(il[j * n_cbps:(j + 1) * n_cbps], DATA_SC_HT, 7 + j)
                 for j in range(nsym)]
    return np.concatenate([preamble(), sig, htsig1, htsig2, htstf, htltf]
                          + data_syms).astype(np.complex64)


def make_psdu(payload_len=24):
    # minimal 802.11 data-ish MPDU + correct CRC-32 FCS (zlib == RX residue)
    mpdu = bytes([0x08, 0x01, 0x00, 0x00] + [0xff] * 6 +
                 [0x57, 0x42, 0x75, 0x05, 0xd6, 0x00] +
                 [0x11, 0x22, 0x33, 0x44, 0x55, 0x66, 0x00, 0x00])
    mpdu = mpdu[:payload_len] if len(mpdu) > payload_len else mpdu + bytes(payload_len - len(mpdu))
    fcs = zlib.crc32(mpdu) & 0xffffffff
    return mpdu + struct.pack("<I", fcs)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["legacy", "ht"], default="ht")
    p.add_argument("--mcs", type=int, default=0)
    p.add_argument("--out", default="/tmp/wifi.cf32")
    p.add_argument("--reps", type=int, default=20, help="repeat the frame N times")
    p.add_argument("--gap", type=int, default=400, help="zero samples between frames")
    p.add_argument("--snr", type=float, default=30.0, help="AWGN SNR (dB)")
    a = p.parse_args()

    psdu = make_psdu()
    frame = gen_legacy(psdu) if a.mode == "legacy" else gen_ht(psdu, a.mcs)
    frame = frame / np.max(np.abs(frame)) * 0.5

    gap = np.zeros(a.gap, dtype=np.complex64)
    one = np.concatenate([gap, frame])
    sig = np.tile(one, a.reps).astype(np.complex64)
    # AWGN
    p_sig = np.mean(np.abs(frame) ** 2)
    n0 = p_sig / (10 ** (a.snr / 10))
    noise = (np.random.normal(0, np.sqrt(n0 / 2), len(sig)) +
             1j * np.random.normal(0, np.sqrt(n0 / 2), len(sig))).astype(np.complex64)
    sig = (sig + noise).astype(np.complex64)

    sig.tofile(a.out)
    print(f"[gen] mode={a.mode} mcs={a.mcs} psdu={len(psdu)}B frame={len(frame)} samp "
          f"x{a.reps} -> {a.out} ({sig.nbytes} bytes)")


if __name__ == "__main__":
    sys.exit(main())
