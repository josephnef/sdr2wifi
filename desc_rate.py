#!/usr/bin/env python3
"""Decode devourer's RX `data_rate` (a hardware DESC_RATE index) into a PHY tuple.

The devourer RX demo (`WiFiDriverDemo`, DEVOURER_STREAM_OUT=1) prints
`<devourer-stream>rate=N ... bw=B stbc=S ldpc=L sgi=G ...` per frame. `rate=N`
is the chip RX-descriptor rate field (`GET_RX_STATUS_DESC_RX_RATE_8812`), which
uses Realtek's DESC_RATE encoding -- NOT the MGN_* enum and NOT a radiotap MCS.
This module turns that index into `(format, mcs, nss, label)` so a completeness
harness can compare what the SDR transmitted (the asked-for MCS/format) against
what devourer reports it received (the got MCS/format).

DESC_RATE map (src/ieee80211_radiotap.h):
  0x00..0x03  CCK   1 / 2 / 5.5 / 11 Mbps   (legacy DSSS)
  0x04..0x0b  OFDM  6 / 9 / 12 / 18 / 24 / 36 / 48 / 54 Mbps  (legacy OFDM, 802.11a/g)
  0x0c..0x2b  HT    MCS 0..31  (DESC_RATEMCS0 = 0x0c; index = 0x0c + mcs)
  0x2c..      VHT   SSn MCSm   (DESC_RATEVHTSS1MCS0 = 0x2c;
                                index = 0x2c + (nss-1)*10 + mcs, nss 1..4, mcs 0..9)

`bw`/`stbc`/`ldpc`/`sgi` are surfaced separately by the demo straight from the RX
descriptor; this module only resolves the rate index. `bw` is the chip BW enum
(0=20, 1=40, 2=80, 3=160 MHz on 8812/8821) -- decode with `bw_mhz()`.
"""

# Legacy rate labels by DESC_RATE index 0x00..0x0b.
_CCK_OFDM = [
    "CCK1M", "CCK2M", "CCK5_5M", "CCK11M",          # 0x00..0x03
    "OFDM6M", "OFDM9M", "OFDM12M", "OFDM18M",       # 0x04..0x07
    "OFDM24M", "OFDM36M", "OFDM48M", "OFDM54M",     # 0x08..0x0b
]

DESC_RATEMCS0 = 0x0C
DESC_RATEVHTSS1MCS0 = 0x2C


def decode(rate):
    """DESC_RATE index -> dict(format, mcs, nss, label).

    format is one of 'CCK', 'OFDM', 'HT', 'VHT'. For CCK/OFDM, mcs/nss are None.
    For HT, mcs is 0..31 (nss = mcs//8 + 1 per the HT MCS->stream mapping). For
    VHT, mcs is 0..9 and nss is 1..4.
    """
    rate = int(rate)
    if 0x00 <= rate <= 0x03:
        return {"format": "CCK", "mcs": None, "nss": None, "label": _CCK_OFDM[rate]}
    if 0x04 <= rate <= 0x0B:
        return {"format": "OFDM", "mcs": None, "nss": None, "label": _CCK_OFDM[rate]}
    if DESC_RATEMCS0 <= rate <= 0x2B:
        mcs = rate - DESC_RATEMCS0
        return {"format": "HT", "mcs": mcs, "nss": mcs // 8 + 1, "label": f"HT_MCS{mcs}"}
    if rate >= DESC_RATEVHTSS1MCS0:
        off = rate - DESC_RATEVHTSS1MCS0
        nss, mcs = off // 10 + 1, off % 10
        return {"format": "VHT", "mcs": mcs, "nss": nss, "label": f"VHT{nss}SS_MCS{mcs}"}
    return {"format": "?", "mcs": None, "nss": None, "label": f"raw0x{rate:02x}"}


def encode_ht(mcs):
    """HT MCS index -> DESC_RATE index (the value devourer should report)."""
    return DESC_RATEMCS0 + int(mcs)


def encode_vht(nss, mcs):
    """VHT (nss, mcs) -> DESC_RATE index (the value devourer should report)."""
    return DESC_RATEVHTSS1MCS0 + (int(nss) - 1) * 10 + int(mcs)


# Chip BW enum (8812/8821 RX descriptor) -> channel width in MHz.
_BW_MHZ = {0: 20, 1: 40, 2: 80, 3: 160}


def bw_mhz(bw):
    """RX-descriptor bw enum -> MHz (None if out of range)."""
    return _BW_MHZ.get(int(bw))


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        for a in sys.argv[1:]:
            r = int(a, 0)
            print(f"0x{r:02x} ({r:3d}) -> {decode(r)}")
    else:
        # self-check: every reachable index decodes, and HT/VHT round-trip.
        for r in range(0x00, 0x40):
            print(f"0x{r:02x} ({r:3d}) -> {decode(r)['label']}")
        assert decode(encode_ht(7))["label"] == "HT_MCS7"
        assert decode(encode_vht(2, 9))["label"] == "VHT2SS_MCS9"
        assert decode(0x0c) == {"format": "HT", "mcs": 0, "nss": 1, "label": "HT_MCS0"}
        assert decode(0x2c) == {"format": "VHT", "mcs": 0, "nss": 1, "label": "VHT1SS_MCS0"}
        print("desc_rate self-check: PASS")
