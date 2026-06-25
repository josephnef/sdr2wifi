#!/usr/bin/env python3
# Reference KAT: encode the same fixed info pattern as lib/tx/ldpc_kat.cc with each of the
# 12 codes via ldpc.py, printing "<name> <hex>". Diffed against the C++ encoder.
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import ldpc

order = ["R12_648", "R23_648", "R34_648", "R56_648",
         "R12_1296", "R23_1296", "R34_1296", "R56_1296",
         "R12_1944", "R23_1944", "R34_1944", "R56_1944"]
for name in order:
    base, Z = ldpc.CODES[name]
    H = ldpc.build_H(base, Z)
    P, info_cols, par_cols = ldpc.gf2_systematic(H)
    n = len(base[0]) * Z
    k = len(info_cols)
    info = [(i * 7 + 3) % 2 for i in range(k)]
    cw = ldpc.encode(info, P, info_cols, par_cols, n)
    hexs = "".join("%x" % sum(int(cw[i + b]) << (3 - b) for b in range(4) if i + b < n)
                   for i in range(0, n, 4))
    print(name, hexs)
