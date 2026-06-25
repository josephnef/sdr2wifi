// C++ port of the validated 802.11 QC-LDPC min-sum decoder (ldpc.py), checked
// against a Python-validated noisy vector. Regenerate the vector + build:
//   python3 -c "import ldpc,numpy as np; H=ldpc.build_H(*ldpc.CODES['R12_648']);
//     P,ic,pc=ldpc.gf2_systematic(H); rng=np.random.default_rng(0);
//     info=rng.integers(0,2,len(ic)).astype(np.uint8); cw=ldpc.encode(info,P,ic,pc,648);
//     tx=1-2*cw.astype(float); s=10**(-4/20); rx=tx+rng.normal(0,s,648); llr=2*rx/s**2;
//     open('/tmp/ldpc_vec.h','w').write('static const float LLR_IN[648]={'+','.join('%.4ff'%x for x in llr)+'};\nstatic const int CW_TRUE[648]={'+','.join(map(str,cw))+'};')"
//   g++ -O2 -o /tmp/ldpc_test scripts/ldpc_decoder_test.cc -I/tmp && /tmp/ldpc_test
// Standalone C++ port of the validated min-sum 802.11 QC-LDPC decoder (R=1/2, n=648),
// checked against a Python-generated, Python-validated noisy test vector.
#include <cstdio>
#include <cmath>
#include <vector>
#include "ldpc_vec.h"

static const int Z = 27;
// R=1/2, n=648 base matrix (IEEE 802.11-2016 Table F-1).
static const int BASE[12][24] = {
    { 0,-1,-1,-1, 0, 0,-1,-1, 0,-1,-1, 0, 1, 0,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1},
    {22, 0,-1,-1,17,-1, 0, 0,12,-1,-1,-1,-1, 0, 0,-1,-1,-1,-1,-1,-1,-1,-1,-1},
    { 6,-1, 0,-1,10,-1,-1,-1,24,-1, 0,-1,-1,-1, 0, 0,-1,-1,-1,-1,-1,-1,-1,-1},
    { 2,-1,-1, 0,20,-1,-1,-1,25, 0,-1,-1,-1,-1,-1, 0, 0,-1,-1,-1,-1,-1,-1,-1},
    {23,-1,-1,-1, 3,-1,-1,-1, 0,-1, 9,11,-1,-1,-1,-1, 0, 0,-1,-1,-1,-1,-1,-1},
    {24,-1,23, 1,17,-1, 3,-1,10,-1,-1,-1,-1,-1,-1,-1,-1, 0, 0,-1,-1,-1,-1,-1},
    {25,-1,-1,-1, 8,-1,-1,-1, 7,18,-1,-1, 0,-1,-1,-1,-1,-1, 0, 0,-1,-1,-1,-1},
    {13,24,-1,-1, 0,-1, 8,-1, 6,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1, 0, 0,-1,-1,-1},
    { 7,20,-1,16,22,10,-1,-1,23,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1, 0, 0,-1,-1},
    {11,-1,-1,-1,19,-1,-1,-1,13,-1, 3,17,-1,-1,-1,-1,-1,-1,-1,-1,-1, 0, 0,-1},
    {25,-1, 8,-1,23,18,-1,14, 9,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1, 0, 0},
    { 3,-1,-1,-1,16,-1,-1, 2,25, 5,-1,-1, 1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1, 0},
};

int main()
{
    const int M = 12 * Z, N = 24 * Z;
    // build H, plus adjacency (checks per var, vars per check)
    std::vector<std::vector<int>> chk_vars(M), var_chks(N);
    for (int br = 0; br < 12; br++)
        for (int bc = 0; bc < 24; bc++) {
            int s = BASE[br][bc];
            if (s < 0)
                continue;
            for (int i = 0; i < Z; i++) {
                int r = br * Z + i;
                int c = bc * Z + (i + s) % Z; // np.roll(I, s, axis=1): col = (row+s)%Z
                chk_vars[r].push_back(c);
                var_chks[c].push_back(r);
            }
        }
    // min-sum (flooding), per-edge messages. msg_cv[r][i] is the message from check r
    // to its i-th variable chk_vars[r][i].
    std::vector<std::vector<double>> msg_cv(M);
    for (int r = 0; r < M; r++)
        msg_cv[r].assign(chk_vars[r].size(), 0.0);
    std::vector<double> total(N);
    auto pos_in_chk = [&](int r, int v) {
        for (size_t i = 0; i < chk_vars[r].size(); i++)
            if (chk_vars[r][i] == v)
                return (int)i;
        return -1;
    };
    std::vector<int> hard(N, 0);
    for (int it = 0; it < 50; it++) {
        for (int r = 0; r < M; r++) {
            int deg = chk_vars[r].size();
            std::vector<double> vc(deg);
            for (int i = 0; i < deg; i++) {
                int v = chk_vars[r][i];
                double s = LLR_IN[v];
                for (int r2 : var_chks[v])
                    if (r2 != r)
                        s += msg_cv[r2][pos_in_chk(r2, v)];
                vc[i] = s;
            }
            double sgn = 1.0;
            for (int i = 0; i < deg; i++)
                sgn *= (vc[i] >= 0 ? 1.0 : -1.0);
            for (int i = 0; i < deg; i++) {
                double mn = 1e18;
                for (int j = 0; j < deg; j++)
                    if (j != i && std::fabs(vc[j]) < mn)
                        mn = std::fabs(vc[j]);
                double si = sgn * (vc[i] >= 0 ? 1.0 : -1.0);
                msg_cv[r][i] = si * mn;
            }
        }
        for (int v = 0; v < N; v++)
            total[v] = LLR_IN[v];
        for (int r = 0; r < M; r++)
            for (size_t i = 0; i < chk_vars[r].size(); i++)
                total[chk_vars[r][i]] += msg_cv[r][i];
        for (int v = 0; v < N; v++)
            hard[v] = total[v] < 0 ? 1 : 0;
        // syndrome check
        bool ok = true;
        for (int r = 0; r < M && ok; r++) {
            int p = 0;
            for (int v : chk_vars[r])
                p ^= hard[v];
            if (p)
                ok = false;
        }
        if (ok)
            break;
    }
    int errs = 0;
    for (int v = 0; v < N; v++)
        if (hard[v] != CW_TRUE[v])
            errs++;
    std::printf("[ldpc-cpp] R12_648 decode: codeword_errors=%d -> %s\n", errs,
                errs == 0 ? "PASS" : "FAIL");
    return errs == 0 ? 0 : 1;
}
