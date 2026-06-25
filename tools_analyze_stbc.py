#!/usr/bin/env python3
"""Separate a real chip's 2-antenna HT STBC frame from a 2-RX-channel B210 capture and
read its convention (HT-LTF P-matrix, pilot pattern, Alamouti ordering).

Two RX observations y0,y1 = H @ [ant0; ant1] -> estimate the 2x2 H from the 2 HT-LTF
symbols (standard P=[[1,-1],[1,1]]: STS0 LTF=[+,-], STS1 LTF=[+,+]) and invert to recover
the two transmit streams per subcarrier. Then inspect STS0 pilots and the STS0/STS1
relationship across symbol pairs.

  ./tools_analyze_stbc.py <cap.cf32>   (reads <cap>.0 and <cap>.1)
"""
import sys, numpy as np
import tools_gen_wifi as t

cap = sys.argv[1]
y0 = np.fromfile(cap + ".0", dtype=np.complex64)
y1 = np.fromfile(cap + ".1", dtype=np.complex64)
n = min(len(y0), len(y1)); y0, y1 = y0[:n], y1[:n]

LTF = np.asarray(t.HTLTF20, dtype=np.complex64)          # HT-LTF freq (DC@32)
LONG = np.asarray([complex(v) for v in t.LONG], np.complex64)
ltf_t = t.ifft_sym(LONG).astype(np.complex64)            # 64-sample L-LTF time ref
DSC = list(t.DATA_SC_HT); PSC = list(t.PILOT_SC)

def fft_sym(sig, off):   # 64-pt FFT of the OFDM symbol at off (skip 16 CP), DC@32
    x = sig[off+16:off+80]
    return np.fft.fftshift(np.fft.fft(x))

# --- find frames via L-LTF cross-correlation on the stronger channel ---
strong = y0 if np.mean(np.abs(y0)) > np.mean(np.abs(y1)) else y1
xc = np.abs(np.correlate(strong, ltf_t, mode="valid"))
thr = xc.max() * 0.5
peaks = np.where(xc > thr)[0]
# group peaks; an L-LTF rep sits at frame_start+192
starts = []
last = -1000
for p in peaks:
    if p - last > 300:
        starts.append(p - 192)   # frame start
    last = p
print(f"samples {n}, candidate frames {len(starts)}")

P = np.array([[1, -1], [1, 1]], dtype=np.complex64)       # standard 2-STS HT-LTF P
PT_inv = np.linalg.inv(P.T)

def analyze(fs):
    # CFO from the two L-LTF reps (frame_start+192, +256)
    a = strong[fs+192:fs+256]; b = strong[fs+256:fs+320]
    if len(b) < 64: return None
    cfo = np.angle(np.vdot(a, b)) / 64.0
    cor = np.exp(-1j * cfo * np.arange(n)).astype(np.complex64)
    z0, z1 = y0 * cor, y1 * cor
    # HT-LTF symbols (2 of them) at frame_start + 640, 720
    L00, L01 = fft_sym(z0, fs+640), fft_sym(z0, fs+720)   # ch0, ltf sym0/1
    L10, L11 = fft_sym(z1, fs+640), fft_sym(z1, fs+720)   # ch1
    occ = [k for k in range(6, 59) if k != 32 and abs(LTF[k]) > 0.1]
    # solve per-subcarrier H: [Y_ltf0;Y_ltf1]/LTF = P^T @ [h_sts0; h_sts1]
    H = {}
    for k in occ:
        h0 = PT_inv @ np.array([L00[k], L01[k]]) / LTF[k]   # [h00,h01]
        h1 = PT_inv @ np.array([L10[k], L11[k]]) / LTF[k]   # [h10,h11]
        H[k] = np.array([[h0[0], h0[1]], [h1[0], h1[1]]], np.complex64)
    # separate the data symbols (frame_start+800+); chip STBC frame has ~34
    sts0, sts1 = [], []
    for j in range(34):
        o = fs + 800 + j*80
        if o+80 > n: break
        Y0, Y1 = fft_sym(z0, o), fft_sym(z1, o)
        a0 = {}; a1 = {}
        for k in DSC + PSC:
            try: sol = np.linalg.solve(H[k], np.array([Y0[k], Y1[k]]))
            except Exception: continue
            a0[k] = sol[0]; a1[k] = sol[1]
        sts0.append(a0); sts1.append(a1)
    return H, sts0, sts1

def bpsk_clean(samps):
    d = np.array(samps)
    if len(d) < 40: return 9.9, 0.0
    ph = np.angle(np.mean(d**2)) / 2
    dr = d * np.exp(-1j*ph)
    return np.mean(np.abs(dr.imag)) / (np.mean(np.abs(dr.real)) + 1e-9), ph

def pair_mag(sts0, sts1, sel_x, sel_y):
    mags=[]
    for i in range(len(sts0)//2):
        xs=[]; ys=[]
        for k in DSC:
            vx=sel_x(sts0,sts1,i,k); vy=sel_y(sts0,sts1,i,k)
            if vx is not None and vy is not None: xs.append(vx); ys.append(vy)
        if len(xs)<20: continue
        xs=np.array(xs); ys=np.array(ys)
        mags.append(abs(np.vdot(ys,xs)/(np.vdot(ys,ys)+1e-9)))
    return mags

g=lambda d,k:(d[k] if k in d else None)
def cj(v): return None if v is None else np.conj(v)
# 4 candidate relationships
F1=(lambda s0,s1,i,k:g(s1[2*i],k),   lambda s0,s1,i,k:(None if g(s0[2*i+1],k) is None else -np.conj(g(s0[2*i+1],k))))  # forkRX
F2=(lambda s0,s1,i,k:g(s1[2*i+1],k), lambda s0,s1,i,k:cj(g(s0[2*i],k)))                                                # forkRX
S1=(lambda s0,s1,i,k:g(s0[2*i+1],k), lambda s0,s1,i,k:(None if g(s1[2*i],k) is None else -np.conj(g(s1[2*i],k))))      # std
S2=(lambda s0,s1,i,k:g(s0[2*i],k),   lambda s0,s1,i,k:cj(g(s1[2*i+1],k)))                                              # std
agg={'F1':[],'F2':[],'S1':[],'S2':[]}

# Aggregate over ALL 2-clean-stream frames. The chip's STBC frames share ONE convention
# (its line -> high |ratio|); ambient 2x2 MIMO is random and averages toward low/equal.
found = 0
for fs in starts[:120]:
    if fs < 0 or fs + 1200 > n: continue
    r = analyze(fs)
    if not r: continue
    H, sts0, sts1 = r
    if len(sts0) < 4: continue
    c0, ph = bpsk_clean([sts0[0][k] for k in DSC if k in sts0[0]])
    c1, _  = bpsk_clean([sts1[0][k] for k in DSC if k in sts1[0]])
    if c0 > 0.45 or c1 > 0.45:
        continue
    found += 1
    for nm,(sx,sy) in [('F1',F1),('F2',F2),('S1',S1),('S2',S2)]:
        agg[nm] += pair_mag(sts0, sts1, sx, sy)
print(f"\n=== aggregated over {found} two-clean-stream frames ===")
for nm,lbl in [('F1','[forkRX] STS1.s0~-conj(STS0.s1)'),('F2','[forkRX] STS1.s1~ conj(STS0.s0)'),
               ('S1','[std]    STS0.s1~-conj(STS1.s0)'),('S2','[std]    STS0.s0~ conj(STS1.s1)')]:
    v=agg[nm]; print(f"  {lbl}: mean|ratio|={np.mean(v):.3f} (n={len(v)})  90th pct={np.percentile(v,90):.2f}")
print("  >> the convention whose TWO lines are highest is the chip's.")
import sys as _s; _s.exit(0)
found = 0
for fs in starts[:60]:
    if fs < 0 or fs + 1200 > n: continue
    r = analyze(fs)
    if not r: continue
    H, sts0, sts1 = r
    if len(sts0) < 2: continue
    c0, ph = bpsk_clean([sts0[0][k] for k in DSC if k in sts0[0]])
    c1, _  = bpsk_clean([sts1[0][k] for k in DSC if k in sts1[0]])
    if c0 > 0.45 or c1 > 0.45:   # need BOTH streams clean -> real STBC
        continue
    found += 1
    print(f"\n=== STBC frame @ {fs}: BOTH streams clean BPSK (STS0 {c0:.2f}, STS1 {c1:.2f}) ===")
    # STS0 pilots, symbol 0 and 1
    for j in (0, 1):
        pil = np.array([sts0[j][k] for k in PSC if k in sts0[j]])
        pr = pil * np.exp(-1j*ph)
        print(f"  STS0 sym{j} pilots (derot, real): {np.round(pr.real,2)}")
    # Alamouti, CFO-robust: per PAIR the ratio phase is constant across subcarriers
    # (CFO adds a per-pair phase), so fit the ratio within each pair and average the
    # MAGNITUDES across pairs. The correct structural relation -> mean |ratio| ~ 1.
    npair = len(sts0)//2
    def rel(name, sel_x, sel_y):
        mags=[]
        for i in range(npair):
            xs=[]; ys=[]
            for k in DSC:
                vx=sel_x(i,k); vy=sel_y(i,k)
                if vx is not None and vy is not None: xs.append(vx); ys.append(vy)
            if len(xs)<20: continue
            xs=np.array(xs); ys=np.array(ys)
            r=np.vdot(ys,xs)/(np.vdot(ys,ys)+1e-9)
            mags.append(abs(r))
        m=float(np.mean(mags)) if mags else 0.0
        print(f"    {name}: mean|ratio| over {len(mags)} pairs = {m:.2f}")
        return m
    g=lambda d,k:(d[k] if k in d else None)
    cj=lambda v:(np.conj(v) if v is not None else None)
    print(f"  Alamouti per-pair magnitude fit ({npair} pairs):")
    # fork-RX conv [[s0,s1],[-s1*,s0*]]: STS1.s0=-conj(STS0.s1), STS1.s1=conj(STS0.s0)
    f1=rel("[forkRX] STS1.s0 ~ -conj(STS0.s1)", lambda i,k:g(sts1[2*i],k),     lambda i,k:(None if g(sts0[2*i+1],k) is None else -np.conj(g(sts0[2*i+1],k))))
    f2=rel("[forkRX] STS1.s1 ~  conj(STS0.s0)", lambda i,k:g(sts1[2*i+1],k),   lambda i,k:cj(g(sts0[2*i],k)))
    # std Alamouti [[s0,-s1*],[s1,s0*]]: STS1.s0=STS0-of-next? STS1.s0=s1, STS0.s1=-s1* -> STS1.s0=-conj(STS0.s1) too? check distinct: STS0.s1 ~ -conj(STS1.s0)
    s1a=rel("[std]    STS0.s1 ~ -conj(STS1.s0)", lambda i,k:g(sts0[2*i+1],k),   lambda i,k:(None if g(sts1[2*i],k) is None else -np.conj(g(sts1[2*i],k))))
    s2a=rel("[std]    STS0.s0 ~  conj(STS1.s1)", lambda i,k:g(sts0[2*i],k),     lambda i,k:cj(g(sts1[2*i+1],k)))
    print("  >> whichever pair of lines reads ~1.0 is the chip's convention.")
    break
else:
    print("no clean STBC frame separated (signal too weak / wrong P-matrix). "
          "Re-capture stronger or try P=[[1,1],[-1,1]].")
