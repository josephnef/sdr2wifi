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


def ifft_sym(freq):
    """freq (DC at index N/2) -> N time samples."""
    return np.fft.ifft(np.fft.ifftshift(freq)).astype(np.complex64)


def ofdm_symbol(freq, cp=None):
    n = len(freq)
    if cp is None:
        cp = n // 4          # 16 for 64-FFT, 32 for 128-FFT
    t = ifft_sym(freq)
    return np.concatenate([t[n - cp:], t]).astype(np.complex64)


# ---- HT40 (128-FFT) subcarrier geometry ----
# DC at bin 64. Data sc -58..-2, 2..58 minus pilots ±{11,25,53}; 108 data, 6 pilot.
DATA_SC_HT40 = [i for i in range(6, 123)
                if (6 <= i <= 62 or 66 <= i <= 122)
                and i not in (53, 75, 39, 89, 11, 117)]   # 108
PILOT_SC_HT40 = [11, 39, 53, 75, 89, 117]                  # sc -53,-25,-11,11,25,53
PILOT_VAL_HT40 = np.array([1, 1, 1, -1, -1, 1], dtype=np.complex64)  # 802.11 HT40 pilots

# HT40 L-LTF (non-HT duplicate): the 20 MHz L-LTF replicated on both 20 MHz
# subchannels (lower at sc-32 -> bins 6..58, upper at sc+32 -> bins 70..122,
# upper rotated by j), DC region (bins 59..69) null. Used to build the 128-sample
# sync reference and the L-preamble.
LONG40 = np.zeros(128, dtype=np.complex64)
LONG40[6:59] = LONG[6:59]
LONG40[70:123] = LONG[6:59] * np.complex64(1j)

# HT40 HT-LTF: fills ALL 114 occupied bins (incl the center carriers + subchannel
# centers the duplicate L-LTF leaves null) so the HT40 data channel can be
# estimated. Lower subchannel real, upper rotated by j. Matches base.cc HTLTF40.
HTLTF40 = LONG40.copy()
HTLTF40[32] = 1
HTLTF40[96] = 1j
for _b in (59, 60, 61, 62):
    HTLTF40[_b] = 1
for _b in (66, 67, 68, 69):
    HTLTF40[_b] = 1j


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


def interleave_ht(bits, n_cbps, n_bpsc, bw=20):
    """HT forward interleave (802.11 19.3.11.7). 20 MHz: N_COL=13, N_ROW=4*N_BPSCS;
    40 MHz: N_COL=18, N_ROW=6*N_BPSCS. Maps coded index k -> tx position j."""
    s = max(n_bpsc // 2, 1)
    n_col = 18 if bw == 40 else 13
    n_row = (6 if bw == 40 else 4) * n_bpsc
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


# Constellation point tables (value -> point), matching lib/constellations_impl.cc.
def _qpsk_pts():
    l = np.sqrt(0.5)
    return [(-l, -l), (l, -l), (-l, l), (l, l)]


def _qam16_pts():
    l = np.sqrt(0.1)
    re = {0: -3, 1: 3, 2: -1, 3: 1}  # bits b0(sign),b1(inner) -> level
    im = {0: -3, 1: 3, 2: -1, 3: 1}
    pts = [None] * 16
    for v in range(16):
        r = re[(v & 1) | ((v >> 1 & 1) << 1)]
        i = im[((v >> 2) & 1) | ((v >> 3 & 1) << 1)]
        pts[v] = (r * l, i * l)
    return pts


def _qam64_pts():
    l = np.sqrt(1 / 42.0)
    lvl = {0: -7, 1: 7, 2: -1, 3: 1, 4: -5, 5: 5, 6: -3, 7: 3}  # 3-bit -> level
    pts = [None] * 64
    for v in range(64):
        r = lvl[(v & 1) | ((v >> 1 & 1) << 1) | ((v >> 2 & 1) << 2)]
        i = lvl[((v >> 3) & 1) | ((v >> 4 & 1) << 1) | ((v >> 5 & 1) << 2)]
        pts[v] = (r * l, i * l)
    return pts


_CONST = {1: [(-1, 0), (1, 0)], 2: _qpsk_pts(), 4: _qam16_pts(), 6: _qam64_pts()}


def map_qam(coded_bits, n_bpsc):
    """Group n_bpsc bits (LSB-first) -> constellation point, matching the RX."""
    pts = _CONST[n_bpsc]
    n = len(coded_bits) // n_bpsc
    out = np.empty(n, dtype=np.complex64)
    for c in range(n):
        v = 0
        for k in range(n_bpsc):
            v |= int(coded_bits[c * n_bpsc + k]) << k
        r, i = pts[v]
        out[c] = np.complex64(complex(r, i))
    return out


def puncture(coded, num, den):
    """Mirror lib/utils.cc puncturing(): rates 1/2, 2/3, 3/4, 5/6."""
    if (num, den) == (1, 2):
        return coded
    out = []
    for i, b in enumerate(coded):
        if (num, den) == (2, 3):
            if i % 4 != 3:
                out.append(b)
        elif (num, den) == (3, 4):
            if i % 6 not in (3, 4):
                out.append(b)
        elif (num, den) == (5, 6):
            if i % 10 not in (3, 4, 7, 8):
                out.append(b)
        else:
            out.append(b)
    return np.array(out, dtype=np.uint8)


def data_symbol(pts, data_sc, sym_idx, rotate=False):
    """One OFDM data/SIG symbol from pre-mapped constellation points + pilots."""
    freq = np.zeros(64, dtype=np.complex64)
    pts = np.asarray(pts, dtype=np.complex64)
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
    sig = data_symbol(map_bpsk(sig_field(mcs_rate4, length)), DATA_SC_LEGACY, 2)
    syms = [sig]
    for j in range(nsym):
        syms.append(data_symbol(map_bpsk(il[j * n_cbps:(j + 1) * n_cbps]), DATA_SC_LEGACY, 3 + j))
    return np.concatenate([preamble()] + syms).astype(np.complex64)


def sig_field(rate4, length12):
    """48 interleaved BPSK bits for the (L-)SIG OFDM symbol."""
    coded = conv_encode(build_sig_bits(rate4, length12))   # 48
    return interleave(coded, 48, 1)


# HT MCS (per stream, 20 MHz): n_bpsc, rate num/den, n_cbps, n_dbps
HT_MCS = {
    0: (1, 1, 2, 52, 26), 1: (2, 1, 2, 104, 52), 2: (2, 3, 4, 104, 78),
    3: (4, 1, 2, 208, 104), 4: (4, 3, 4, 208, 156), 5: (6, 2, 3, 312, 208),
    6: (6, 3, 4, 312, 234), 7: (6, 5, 6, 312, 260),
}
# HT MCS (per stream, 40 MHz, 108 data SC): n_bpsc, rate, n_cbps, n_dbps
HT_MCS40 = {
    0: (1, 1, 2, 108, 54), 1: (2, 1, 2, 216, 108), 2: (2, 3, 4, 216, 162),
    3: (4, 1, 2, 432, 216), 4: (4, 3, 4, 432, 324), 5: (6, 2, 3, 648, 432),
    6: (6, 3, 4, 648, 486), 7: (6, 5, 6, 648, 540),
}


def dup40(freq64):
    """Replicate a 20 MHz freq-domain symbol (64-bin, DC@32) onto both 40 MHz
    subchannels (lower bins 6..58, upper 70..122 x j) -- the non-HT-duplicate
    format used for HT40 L-STF/L-LTF/L-SIG/HT-SIG."""
    f = np.zeros(128, dtype=np.complex64)
    f[6:59] = freq64[6:59]
    f[70:123] = freq64[6:59] * np.complex64(1j)
    return f


def data_symbol40(pts, sym_idx, rotate=False):
    """One HT40 OFDM symbol (128-FFT) from 108 mapped points + 6 pilots."""
    freq = np.zeros(128, dtype=np.complex64)
    pts = np.asarray(pts, dtype=np.complex64)
    if rotate:
        pts = pts * np.complex64(1j)
    for c, sc in enumerate(DATA_SC_HT40):
        freq[sc] = pts[c]
    p = POLARITY[(sym_idx - 2) % 127]
    for pv, sc in zip(PILOT_VAL_HT40, PILOT_SC_HT40):
        freq[sc] = pv * p * (np.complex64(1j) if rotate else np.complex64(1))
    return ofdm_symbol(freq)


def sig_symbol_dup40(bits48, sym_idx, rotate=False):
    """A legacy/HT-SIG symbol in HT40 non-HT-duplicate form (128-FFT): the 20 MHz
    symbol (48 BPSK data + 4 pilots on bins 6..58) duplicated to both subchannels."""
    f64 = np.zeros(64, dtype=np.complex64)
    pts = map_bpsk(bits48)
    if rotate:
        pts = pts * np.complex64(1j)
    for c, sc in enumerate(DATA_SC_LEGACY):
        f64[sc] = pts[c]
    p = POLARITY[(sym_idx - 2) % 127]
    for pv, sc in zip(PILOT_VAL, PILOT_SC):
        f64[sc] = pv * p * (np.complex64(1j) if rotate else np.complex64(1))
    return ofdm_symbol(dup40(f64))


def preamble40():
    # L-STF40: 20 MHz S duplicated -> period-32 at 40 MS/s; 320 samples (8 us)
    st = ifft_sym(dup40(lstf_freq()))
    lstf = np.tile(st[:32], 10)
    # L-LTF40: GI2(64) + 2 x 128
    lt = ifft_sym(LONG40)
    lltf = np.concatenate([lt[64:], lt, lt])
    return np.concatenate([lstf, lltf]).astype(np.complex64)


def gen_ht40(psdu, mcs=0):
    n_bpsc, rnum, rden, n_cbps, n_dbps = HT_MCS40[mcs]
    length = len(psdu)
    nsym = int(np.ceil((16 + 8 * length + 6) / n_dbps))
    lsig_len = max(length + 16, 60)

    # L-SIG + HT-SIG in non-HT-duplicate form (128-FFT)
    sig = sig_symbol_dup40(sig_field(11, lsig_len), 2)
    hsig = np.zeros(48, dtype=np.uint8)
    for i in range(7):
        hsig[i] = (mcs >> i) & 1
    hsig[7] = 1                            # CBW40
    for i in range(16):
        hsig[8 + i] = (length >> i) & 1
    crc = ht_sig_crc8(hsig[0:34])
    for i in range(8):
        hsig[34 + i] = (crc >> (7 - i)) & 1
    hsig_il = interleave(conv_encode(hsig), 48, 1)
    htsig1 = sig_symbol_dup40(hsig_il[0:48], 3, rotate=True)
    htsig2 = sig_symbol_dup40(hsig_il[48:96], 4, rotate=True)

    # HT-STF (sym5): full-band (RX skips). HT-LTF (sym6): true HT40 LTF covering
    # all 114 occupied bins -> the RX estimates the HT40 data channel from it.
    htstf = ofdm_symbol(LONG40)
    htltf = ofdm_symbol(HTLTF40)

    # HT DATA: 108 SC, 128-FFT
    data_bits = np.zeros(nsym * n_dbps, dtype=np.uint8)
    data_bits[16:16 + 8 * length] = bytes_to_bits(psdu)
    scr = scramble(data_bits)
    scr[16 + 8 * length:16 + 8 * length + 6] = 0
    coded = puncture(conv_encode(scr), rnum, rden)
    il = interleave_ht(coded, n_cbps, n_bpsc, bw=40)
    data_syms = [data_symbol40(map_qam(il[j * n_cbps:(j + 1) * n_cbps], n_bpsc), 7 + j)
                 for j in range(nsym)]
    return np.concatenate([preamble40(), sig, htsig1, htsig2, htstf, htltf]
                          + data_syms).astype(np.complex64)


def gen_ht(psdu, mcs=0):
    n_bpsc, rnum, rden, n_cbps, n_dbps = HT_MCS[mcs]
    length = len(psdu)
    nsym = int(np.ceil((16 + 8 * length + 6) / n_dbps))
    # L-SIG: always 6 Mbps (r=11); length large enough to cover the HT frame
    lsig_len = max(length + 16, 40)
    sig = data_symbol(map_bpsk(sig_field(11, lsig_len)), DATA_SC_LEGACY, 2)

    # HT-SIG: 48 info bits = HT-SIG1(24) + HT-SIG2(24); QBPSK over 2 symbols
    hsig = np.zeros(48, dtype=np.uint8)
    for i in range(7):
        hsig[i] = (mcs >> i) & 1          # MCS
    hsig[7] = 0                            # CBW20
    for i in range(16):
        hsig[8 + i] = (length >> i) & 1    # HT-Length
    # HT-SIG2: smoothing..fec(BCC=0)..sgi(0)..n_ess(0), CRC8 over bits0..33, tail
    crc = ht_sig_crc8(hsig[0:34])
    for i in range(8):
        hsig[34 + i] = (crc >> (7 - i)) & 1   # MSB-first c7..c0
    hsig_il = interleave(conv_encode(hsig), 48, 1)  # 96, legacy 48-interleaver
    htsig1 = data_symbol(map_bpsk(hsig_il[0:48]), DATA_SC_LEGACY, 3, rotate=True)
    htsig2 = data_symbol(map_bpsk(hsig_il[48:96]), DATA_SC_LEGACY, 4, rotate=True)

    # HT-STF (sym5), HT-LTF (sym6) -- RX skips both. Use FULL-BAND symbols (not
    # the periodic short-training pattern) so sync_short does not re-trigger
    # mid-frame and corrupt the symbol alignment.
    htstf = ofdm_symbol(LONG)
    htltf = ofdm_symbol(LONG)

    # HT DATA: scramble -> conv 1/2 -> puncture(rate) -> HT-interleave -> QAM map
    data_bits = np.zeros(nsym * n_dbps, dtype=np.uint8)
    data_bits[16:16 + 8 * length] = bytes_to_bits(psdu)
    scr = scramble(data_bits)
    scr[16 + 8 * length:16 + 8 * length + 6] = 0   # tail
    coded = puncture(conv_encode(scr), rnum, rden)  # -> nsym*n_cbps
    il = interleave_ht(coded, n_cbps, n_bpsc)
    data_syms = [data_symbol(map_qam(il[j * n_cbps:(j + 1) * n_cbps], n_bpsc),
                             DATA_SC_HT, 7 + j) for j in range(nsym)]
    return np.concatenate([preamble(), sig, htsig1, htsig2, htstf, htltf]
                          + data_syms).astype(np.complex64)


# ---- 802.11n HT 2x2 MIMO (MCS 8-15, 20 MHz) ----------------------------------
# MCS 8..15 are MCS 0..7 carried on 2 spatial streams (same per-stream modulation).
HT_MCS_MIMO = {8 + k: HT_MCS[k] for k in range(8)}  # mcs -> per-stream params

# HT20 HT-LTF: L-LTF extended to the HT edge tones (sc +-27,+-28 = bins 4,5,59,60),
# so the 2x2 channel can be estimated on every HT20 data/pilot subcarrier (4..60).
HTLTF20 = LONG.copy()
HTLTF20[4] = 1; HTLTF20[5] = 1; HTLTF20[59] = -1; HTLTF20[60] = -1

# HT-LTF mapping matrix P (802.11-2016 Table 19-17), 2x2 case.
P2 = np.array([[1, -1], [1, 1]], dtype=np.complex64)
N_ROT_20 = 11  # HT20 interleaver frequency-rotation unit


def stream_parse(coded, n_ss, n_bpsc, n_cbps_ss):
    """802.11n stream parser (19.3.11.6): single BCC stream -> n_ss streams, s bits
    at a time round-robin. Returns a list of n_ss bit arrays of n_cbps_ss*nsym each."""
    s = max(n_bpsc // 2, 1)
    nsym = len(coded) // (n_ss * n_cbps_ss)
    out = [np.zeros(nsym * n_cbps_ss, dtype=np.uint8) for _ in range(n_ss)]
    # per OFDM symbol, deal s-bit blocks across streams
    pos = [0] * n_ss
    idx = 0
    blocks_per_sym = (n_ss * n_cbps_ss) // s
    for sym in range(nsym):
        for blk in range(blocks_per_sym):
            ss = blk % n_ss
            for b in range(s):
                out[ss][pos[ss]] = coded[idx]; pos[ss] += 1; idx += 1
    return out


def interleave_ht_ss(bits, n_cbps_ss, n_bpsc, iss, bw=20):
    """HT interleave for spatial stream iss (0-based), with the 3rd permutation
    frequency rotation (19.3.11.7.3) applied for iss>0. Forward map k -> position."""
    s = max(n_bpsc // 2, 1)
    n_col = 18 if bw == 40 else 13
    n_row = (6 if bw == 40 else 4) * n_bpsc
    n_rot = 29 if bw == 40 else N_ROT_20
    # third-permutation shift in bits for this stream (i_SS is 1-based in the spec)
    issp = iss + 1
    jrot = (((issp - 1) * 2) % 3 + 3 * ((issp - 1) // 3)) * n_rot * n_bpsc
    out = np.empty_like(bits)
    nsym = len(bits) // n_cbps_ss
    for sym in range(nsym):
        base = sym * n_cbps_ss
        for k in range(n_cbps_ss):
            i = n_row * (k % n_col) + (k // n_col)
            j = s * (i // s) + (i + n_cbps_ss - (n_col * i) // n_cbps_ss) % s
            r = (j - jrot) % n_cbps_ss
            out[base + r] = bits[base + k]
    return out


def ht_mimo_streams(psdu, mcs):
    """Build the 2 spatial-stream frequency-domain data symbols for MCS 8-15 (HT20).
    Returns (stream_syms, nsym, coded) where stream_syms[ss][j] is a 52-point vector."""
    n_bpsc, rnum, rden, n_cbps_ss, n_dbps_ss = HT_MCS_MIMO[mcs]
    n_ss = 2
    length = len(psdu)
    n_dbps_tot = n_ss * n_dbps_ss
    nsym = int(np.ceil((16 + 8 * length + 6) / n_dbps_tot))

    data_bits = np.zeros(nsym * n_dbps_tot, dtype=np.uint8)
    data_bits[16:16 + 8 * length] = bytes_to_bits(psdu)
    scr = scramble(data_bits)
    scr[16 + 8 * length:16 + 8 * length + 6] = 0
    coded = puncture(conv_encode(scr), rnum, rden)            # nsym*n_ss*n_cbps_ss

    parts = stream_parse(coded, n_ss, n_bpsc, n_cbps_ss)
    stream_syms = []
    for ss in range(n_ss):
        il = interleave_ht_ss(parts[ss], n_cbps_ss, n_bpsc, ss, bw=20)
        syms = [map_qam(il[j * n_cbps_ss:(j + 1) * n_cbps_ss], n_bpsc) for j in range(nsym)]
        stream_syms.append(syms)
    return stream_syms, nsym, coded


def gen_ht_mimo(psdu, mcs=8, H=None):
    """2x2 HT-MIMO (MCS 8-15, 20 MHz) -> (rx0, rx1) time-domain captures.
    Legacy preamble/L-SIG/HT-SIG/HT-STF on TX antenna 0; HT-LTF over 2 symbols via
    the P matrix; HT-DATA = 2 spatial streams through a frequency-flat 2x2 channel H
    (rx_r[n] = sum_s H[r,s] tx_s[n])."""
    if H is None:
        H = np.eye(2, dtype=np.complex64)
    n_bpsc, rnum, rden, n_cbps_ss, n_dbps_ss = HT_MCS_MIMO[mcs]
    length = len(psdu)
    lsig_len = max(length + 16, 40)

    # --- TX antenna 0: the (single-stream) preamble + signalling ---
    sig = data_symbol(map_bpsk(sig_field(11, lsig_len)), DATA_SC_LEGACY, 2)
    hsig = np.zeros(48, dtype=np.uint8)
    for i in range(7):
        hsig[i] = (mcs >> i) & 1
    hsig[7] = 0                                   # CBW20
    for i in range(16):
        hsig[8 + i] = (length >> i) & 1
    crc = ht_sig_crc8(hsig[0:34])
    for i in range(8):
        hsig[34 + i] = (crc >> (7 - i)) & 1
    hsig_il = interleave(conv_encode(hsig), 48, 1)
    htsig1 = data_symbol(map_bpsk(hsig_il[0:48]), DATA_SC_LEGACY, 3, rotate=True)
    htsig2 = data_symbol(map_bpsk(hsig_il[48:96]), DATA_SC_LEGACY, 4, rotate=True)
    htstf = ofdm_symbol(LONG)

    tx0_pre = np.concatenate([preamble(), sig, htsig1, htsig2, htstf]).astype(np.complex64)
    tx1_pre = np.zeros_like(tx0_pre)              # antenna 1 silent during preamble

    # --- HT-LTF: 2 symbols, P-matrix across the 2 TX antennas ---
    def ltf_freq(antenna, ltf_sym):
        f = np.zeros(64, dtype=np.complex64)
        for sc in range(4, 61):
            f[sc] = HTLTF20[sc] * P2[ltf_sym, antenna]
        return f
    tx0_ltf = np.concatenate([ofdm_symbol(ltf_freq(0, 0)), ofdm_symbol(ltf_freq(0, 1))])
    tx1_ltf = np.concatenate([ofdm_symbol(ltf_freq(1, 0)), ofdm_symbol(ltf_freq(1, 1))])

    # --- HT-DATA: 2 spatial streams, direct spatial mapping (stream s -> antenna s) ---
    stream_syms, nsym, coded = ht_mimo_streams(psdu, mcs)
    tx0_data, tx1_data = [], []
    for j in range(nsym):
        f0 = np.zeros(64, dtype=np.complex64)
        f1 = np.zeros(64, dtype=np.complex64)
        for c, sc in enumerate(DATA_SC_HT):
            f0[sc] = stream_syms[0][j][c]
            f1[sc] = stream_syms[1][j][c]
        p = POLARITY[(7 + j - 2) % 127]
        # HT20 pilots: stream 0 on the 4 pilot tones (stream 1 uses the P-rotated set;
        # for a 2-stream direct-mapped synthetic test we drive pilots from stream 0).
        for pv, sc in zip(PILOT_VAL, PILOT_SC):
            f0[sc] = pv * p
        tx0_data.append(ofdm_symbol(f0))
        tx1_data.append(ofdm_symbol(f1))

    tx0 = np.concatenate([tx0_pre, tx0_ltf] + tx0_data).astype(np.complex64)
    tx1 = np.concatenate([tx1_pre, tx1_ltf] + tx1_data).astype(np.complex64)

    # frequency-flat 2x2 channel mixing in the time domain
    rx0 = (H[0, 0] * tx0 + H[0, 1] * tx1).astype(np.complex64)
    rx1 = (H[1, 0] * tx0 + H[1, 1] * tx1).astype(np.complex64)
    return rx0, rx1, coded


def demap_qam(sym, n_bpsc):
    """Hard-decision inverse of map_qam: nearest constellation point -> bits LSB-first."""
    pts = _CONST[n_bpsc]
    best, bestd = 0, 1e30
    for v, (r, i) in enumerate(pts):
        d = (sym.real - r) ** 2 + (sym.imag - i) ** 2
        if d < bestd:
            bestd, best = d, v
    return [(best >> k) & 1 for k in range(n_bpsc)]


def mimo_selftest(mcs=8, H=None):
    """Bit-exact reference receiver: recover the HT-MIMO *coded bits* from (rx0,rx1)
    via HT-LTF channel estimate + per-subcarrier ZF/MMSE + demap + deinterleave +
    stream-deparse, and check they equal the transmitted coded bits. Proves the 2x2
    data model is invertible (the C++ RX must reproduce this)."""
    if H is None:
        H = np.array([[1.0, 0.3 + 0.2j], [-0.2 + 0.1j, 0.9]], dtype=np.complex64)
    n_bpsc, rnum, rden, n_cbps_ss, n_dbps_ss = HT_MCS_MIMO[mcs]
    psdu = make_psdu()
    rx0, rx1, coded_tx = gen_ht_mimo(psdu, mcs, H)
    nsym = (len(rx0) - 800) // 80

    def sym_fft(rx, start):
        t = rx[start + 16:start + 80]               # strip 16-sample CP
        return np.fft.fftshift(np.fft.fft(t))

    # HT-LTF channel estimate: 2 LTF symbols at samples 640 / 720
    Pinv = np.linalg.inv(P2.astype(np.complex128))
    Hh = np.zeros((64, 2, 2), dtype=np.complex128)   # [sc][rx][stream]
    y_ltf = [[sym_fft(rx0, 640), sym_fft(rx0, 720)],
             [sym_fft(rx1, 640), sym_fft(rx1, 720)]]
    for sc in range(4, 61):
        if HTLTF20[sc] == 0:
            continue
        for r in range(2):
            yv = np.array([y_ltf[r][0][sc], y_ltf[r][1][sc]])
            Hh[sc, r, :] = (Pinv @ yv) / HTLTF20[sc]

    # data: per-subcarrier MMSE (noiseless -> ZF) recovers the 2 streams
    rec_streams = [np.zeros(nsym * n_cbps_ss, dtype=np.uint8) for _ in range(2)]
    for j in range(nsym):
        Y0 = sym_fft(rx0, 800 + j * 80); Y1 = sym_fft(rx1, 800 + j * 80)
        cbits = [[], []]
        for sc in DATA_SC_HT:
            Hk = Hh[sc]
            y = np.array([Y0[sc], Y1[sc]])
            xhat = np.linalg.solve(Hk, y)            # ZF (noiseless)
            for ss in range(2):
                cbits[ss].extend(demap_qam(xhat[ss], n_bpsc))
        for ss in range(2):
            rec_streams[ss][j * n_cbps_ss:(j + 1) * n_cbps_ss] = cbits[ss]

    # deinterleave each stream, then de-parse back to the single coded sequence
    deint = []
    for ss in range(2):
        fwd = np.empty(n_cbps_ss, dtype=int)
        s = max(n_bpsc // 2, 1); n_col = 13; n_row = 4 * n_bpsc
        issp = ss + 1
        jrot = (((issp - 1) * 2) % 3 + 3 * ((issp - 1) // 3)) * N_ROT_20 * n_bpsc
        for k in range(n_cbps_ss):
            i = n_row * (k % n_col) + (k // n_col)
            jj = s * (i // s) + (i + n_cbps_ss - (n_col * i) // n_cbps_ss) % s
            fwd[k] = (jj - jrot) % n_cbps_ss          # forward k -> position
        d = np.zeros_like(rec_streams[ss])
        for sym in range(nsym):
            base = sym * n_cbps_ss
            for k in range(n_cbps_ss):
                d[base + k] = rec_streams[ss][base + fwd[k]]
        deint.append(d)
    # de-parse (inverse stream_parse): s bits at a time, round-robin from the streams
    s = max(n_bpsc // 2, 1)
    rec_coded = np.zeros(2 * len(deint[0]), dtype=np.uint8)
    pos = [0, 0]; idx = 0
    blocks_per_sym = (2 * n_cbps_ss) // s
    for sym in range(nsym):
        for blk in range(blocks_per_sym):
            ss = blk % 2
            for b in range(s):
                rec_coded[idx] = deint[ss][pos[ss]]; pos[ss] += 1; idx += 1

    ok = np.array_equal(rec_coded[:len(coded_tx)], coded_tx)
    nerr = int(np.sum(rec_coded[:len(coded_tx)] != coded_tx))
    print(f"[mimo-selftest] mcs={mcs} nsym={nsym} coded_bits={len(coded_tx)} "
          f"bit_errors={nerr} -> {'PASS' if ok else 'FAIL'}")
    return ok


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
    p.add_argument("--mode", choices=["legacy", "ht", "ht40", "ht_mimo"], default="ht")
    p.add_argument("--mcs", type=int, default=0)
    p.add_argument("--out", default="/tmp/wifi.cf32")
    p.add_argument("--reps", type=int, default=20, help="repeat the frame N times")
    p.add_argument("--gap", type=int, default=400, help="zero samples between frames")
    p.add_argument("--snr", type=float, default=30.0, help="AWGN SNR (dB)")
    p.add_argument("--selftest", action="store_true",
                   help="ht_mimo: run the bit-exact reference-RX data-model self-test")
    a = p.parse_args()

    if a.mode == "ht_mimo" and a.selftest:
        mcs = a.mcs if a.mcs >= 8 else 8
        sys.exit(0 if mimo_selftest(mcs) else 1)

    psdu = make_psdu()
    if a.mode == "legacy":
        frame = gen_legacy(psdu)
    elif a.mode == "ht40":
        frame = gen_ht40(psdu, a.mcs)
    else:
        frame = gen_ht(psdu, a.mcs)
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
