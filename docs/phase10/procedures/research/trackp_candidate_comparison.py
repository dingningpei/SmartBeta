import numpy as np, json
from scipy import stats
rng = np.random.Generator(np.random.PCG64(20261010)); R = 20000
def series(T, rho, innov, mu):
    n = T + 200
    if innov == 'normal': e = rng.standard_normal((R, n))
    else:
        df = 5 if innov == 't5' else 3
        e = rng.standard_t(df, (R, n)) / np.sqrt(df / (df - 2))
    y = np.empty_like(e); y[:, 0] = e[:, 0]
    s = np.sqrt(1 - rho ** 2)
    for t in range(1, n): y[:, t] = rho * y[:, t - 1] + s * e[:, t]
    return mu + y[:, -T:]
def t_plain(x):
    T = x.shape[1]; m = x.mean(1); s = x.std(1, ddof=1); return np.sqrt(T) * m / s, T - 1
def ewc(x, nu):
    T = x.shape[1]; u = x - x.mean(1, keepdims=True); t = np.arange(1, T + 1)
    lam = np.stack([np.sqrt(2 / T) * (u * np.cos(np.pi * j * (t - 0.5) / T)).sum(1) for j in range(1, nu + 1)], 1)
    om = np.sqrt((lam ** 2).mean(1)); return np.sqrt(T) * x.mean(1) / om, nu
def ar1adj(x):
    T = x.shape[1]; u = x - x.mean(1, keepdims=True)
    r = (u[:, 1:] * u[:, :-1]).sum(1) / (u ** 2).sum(1)
    r = np.clip(r + (1 + 3 * r) / T, -0.9, 0.9)
    t, _ = t_plain(x); return t / np.sqrt((1 + r) / (1 - r)), T - 1
procs = {'t_plain': t_plain,
         'ewc_llsw': lambda x: ewc(x, max(2, int(round(0.4 * x.shape[1] ** (2 / 3))))),
         'ewc_big': lambda x: ewc(x, max(2, int(round(x.shape[1] ** (2 / 3))))),
         'ar1_adj_t': ar1adj}
mu1 = 1.0 / np.sqrt(12)  # annual Sharpe 1.0 on monthly marginal sd 1
out = []
for T in (24, 30, 36, 48, 60):
    for rho in (0.0, 0.1, 0.2, 0.3):
        for innov in ('normal', 't5', 't3'):
            x0 = series(T, rho, innov, 0.0); x1 = series(T, rho, innov, mu1)
            for name, f in procs.items():
                t0, df = f(x0); t1, _ = f(x1); cv = stats.t.ppf(0.95, df)
                out.append(dict(T=T, rho=rho, innov=innov, proc=name, df=int(df), size=float((t0 > cv).mean()), power_sr1=float((t1 > cv).mean())))
json.dump(out, open(__file__.replace('compare.py', 'results.json'), 'w'), indent=0)
import collections
print('proc        | df@T36 | max size in-region(|rho|<=0.2,normal/t5) | size rho=0.3 max | power SR1.0 @T36 rho0 normal | @T36 rho0.2 normal')
for name in procs:
    rr = [r for r in out if r['proc'] == name]
    inreg = [r['size'] for r in rr if r['rho'] <= 0.2 and r['innov'] in ('normal', 't5')]
    out3 = [r['size'] for r in rr if r['rho'] == 0.3]
    p0 = [r['power_sr1'] for r in rr if r['T'] == 36 and r['rho'] == 0 and r['innov'] == 'normal'][0]
    p2 = [r['power_sr1'] for r in rr if r['T'] == 36 and r['rho'] == 0.2 and r['innov'] == 'normal'][0]
    d36 = [r['df'] for r in rr if r['T'] == 36][0]
    print(f'{name:11s} | {d36:6d} | {max(inreg):.4f} (min {min(inreg):.4f}) | {max(out3):.4f} | {p0:.3f} | {p2:.3f}')
for name in procs:
    print(name, 'size by rho (T=36,normal):', [round([r['size'] for r in out if r['proc']==name and r['T']==36 and r['innov']=='normal' and r['rho']==rho][0],4) for rho in (0,0.1,0.2,0.3)], ' T=24:', [round([r['size'] for r in out if r['proc']==name and r['T']==24 and r['innov']=='normal' and r['rho']==rho][0],4) for rho in (0,0.1,0.2,0.3)])
