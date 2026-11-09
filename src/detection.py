"""Monitoring layer: what a control room would run on top of SCADA.

1. Instrument health  - spikes and frozen transmitters.
2. Digital twin       - quasi-steady hydraulic model fed with measured flow,
                        density and terminal pressure; residual per station.
3. Volume balance     - line-pack-compensated, three windows (2, 10, 60 min).
4. Pressure waves     - step-like drops per transmitter (detrended), origin
                        located by arrival times; boundary origins = operations.
5. Leak localisation  - pressure-wave origin (sudden leaks) and twin residual
                        pattern / hydraulic-gradient fit (any leak).
6. Operating envelope - MAOP, minimum line pressure, deposition velocity.
7. Batch tracking     - predicted vs measured arrival of water batches.

Only measured signals are used here; ground truth is never read.
"""
from dataclasses import dataclass

import numpy as np
from scipy.ndimage import median_filter
from scipy.optimize import minimize_scalar
from scipy.signal import lfilter

from .config import PipelineConfig
from .hydraulics import Pipe, block_mean, track_density
from .simulator import SimResult, ema_init

TWIN_STEP = 60                     # s, twin and dashboard resolution
BAL_WINDOWS = (120, 600, 3600)     # s
BAL_PERSIST = (30, 60, 120)        # s above threshold before alarming
BAL_Z = 5.0
BAL_FLOOR = (0.020, 0.006, 0.003)  # fraction of q_nom
NPW_K = 6.0                        # sigmas
NPW_REFRACTORY = 180               # s
TWIN_BASELINE = True               # remove pre-leak residual before localising


@dataclass
class Calibration:
    friction_scale: float
    station_offsets: np.ndarray    # bar, twin residual at commissioning
    flow_bias: float               # m3/s, balance offset (meter bias)
    bal_thresholds: np.ndarray     # m3/s per window
    npw_sigma: np.ndarray          # bar per station
    commissioning_s: int


# Helpers
def rolling_mean(x, w):
    cs = np.concatenate([[0.0], np.cumsum(x)])
    out = np.full(len(x), np.nan)
    out[w - 1:] = (cs[w:] - cs[:-w]) / w
    return out


def runs(mask):
    """(start, end_exclusive) of consecutive True runs."""
    m = np.concatenate([[False], np.asarray(mask, bool), [False]])
    d = np.diff(m.astype(int))
    return list(zip(np.where(d == 1)[0], np.where(d == -1)[0]))


def station_weights(cfg):
    g = np.gradient(cfg.station_m)
    return g / g.sum()


# 1. instrument health
def instrument_health(sim: SimResult):
    p = sim.p_meas
    med = median_filter(p, size=(5, 1), mode="nearest")
    spike = np.abs(p - med) > 1.0
    # frozen transmitter: no change at all for >= 10 min (noise is never exactly 0)
    unchanged = np.vstack([np.zeros((1, p.shape[1]), bool), np.diff(p, axis=0) == 0])
    frozen = np.zeros_like(spike)
    alerts = []
    for s in range(p.shape[1]):
        for a, b in runs(unchanged[:, s]):
            if b - a >= 600:
                frozen[a - 1:b, s] = True
                alerts.append(dict(station=s, start=a - 1, end=b, kind="Transmisor congelado",
                                   detail=f"Sin variación durante {(b - a + 1) / 60:.0f} min"))
        for a, b in runs(spike[:, s]):
            alerts.append(dict(station=s, start=a, end=b, kind="Pico (spike)",
                               detail=f"Desviación {float((p[a:b, s] - med[a:b, s]).max()):+.2f} bar vs mediana"))
    bad = spike | frozen
    clean = med.copy()
    return clean, bad, frozen, alerts


# 2. digital twin
class Twin:
    def __init__(self, cfg: PipelineConfig, friction_scale=1.0):
        self.cfg = cfg
        self.pipe = Pipe(cfg)                       # design friction, uniform
        self.fs = friction_scale

    def inputs(self, sim: SimResult, p_clean):
        cfg = self.cfg
        n = TWIN_STEP
        t60 = block_mean(sim.t.astype(float), n)
        q60 = block_mean(sim.q_in_meas, n)
        p60 = block_mean(p_clean, n)
        rho_cells = track_density(t60, sim.t.astype(float), np.maximum(sim.q_in_meas, 1e-4),
                                  sim.rho_in_meas, self.pipe.x_mid, cfg.area, cfg.rho_slurry)
        return t60, q60, p60, rho_cells

    def station_model(self, q60, rho_cells, p_out_pa, fs=None):
        fs = self.fs if fs is None else fs
        qc = np.repeat(q60[:, None], self.pipe.n_cells, axis=1)
        return self.pipe.station_pressures(qc, rho_cells, p_out_pa, fs) / 1e5

    def node_model(self, q60, rho_cells, p_out_pa):
        qc = np.repeat(q60[:, None], self.pipe.n_cells, axis=1)
        return self.pipe.node_pressures(qc, rho_cells, p_out_pa, self.fs) / 1e5

    def leak_signature(self, q, rho_cells_row, p_out_pa, x_grid_km, q_unit=None):
        """d p_station / d q_leak (bar per m3/s) for each candidate location."""
        q_unit = q_unit or 0.01 * self.cfg.q_nom_m3s
        pipe = self.pipe
        base = pipe.station_pressures(np.full(pipe.n_cells, q), rho_cells_row, p_out_pa, self.fs)
        down = (pipe.x_mid[None, :] > x_grid_km[:, None] * 1000.0).astype(float)
        qc = q - q_unit * down
        pl = pipe.station_pressures(qc, np.broadcast_to(rho_cells_row, qc.shape), np.full(len(x_grid_km), p_out_pa), self.fs)
        return (pl - base[None, :]) / 1e5 / q_unit


# 3. volume balance
LP_UNCERTAINTY = 0.30              # relative uncertainty of the line-pack correction


def balance_signal(sim: SimResult, p_used, rho_mean_f, compensate=True):
    """Imbalance q_in - q_out - d(line pack)/dt, plus the line-pack term itself.

    p_used: pressures with bad samples already replaced (spikes, frozen
    transmitters), so the mean line pressure has no artificial steps."""
    cfg = sim.cfg
    p_mean = (p_used * station_weights(cfg)).sum(axis=1) * 1e5
    dPdt = np.gradient(ema_init(p_mean, 3.0))
    lp = (cfg.area * cfg.length_m) / (rho_mean_f * cfg.wave_speed ** 2) * dPdt
    e = sim.q_in_meas - sim.q_out_meas
    if compensate:
        e = e - lp
    return e, lp


def balance_thresholds(thr, lp):
    """Adaptive thresholds: base threshold + uncertainty of the line-pack correction."""
    return [th + LP_UNCERTAINTY * np.abs(np.nan_to_num(rolling_mean(lp, w)))
            for th, w in zip(thr, BAL_WINDOWS)]


def balance_alarms(d, thr, q_nom, lp=None):
    """d: imbalance minus bias (m3/s). Returns rolling means, effective thresholds, alarms."""
    rms = [rolling_mean(d, w) for w in BAL_WINDOWS]
    thr_eff = balance_thresholds(thr, lp) if lp is not None else [np.full(len(d), th) for th in thr]
    trig = np.zeros(len(d), bool)
    for rm, th, pers in zip(rms, thr_eff, BAL_PERSIST):
        exc = np.nan_to_num(rm) > th
        cnt = rolling_mean(exc.astype(float), pers)
        trig |= np.nan_to_num(cnt) >= 1.0 - 1e-9
    clear = np.ones(len(d), bool)
    for rm, th in zip(rms, thr_eff):
        clear &= np.nan_to_num(rm) < 0.5 * th
    clear_run = np.nan_to_num(rolling_mean(clear.astype(float), 600)) >= 1.0 - 1e-9
    alarms = []
    i = 0
    trig_idx = np.where(trig)[0]
    while len(trig_idx):
        t0 = int(trig_idx[0])
        after = np.where(clear_run[t0:])[0]
        t1 = t0 + int(after[0]) if len(after) else len(d)
        which = [k for k, (rm, th) in enumerate(zip(rms, thr_eff)) if np.nan_to_num(rm[t0]) > th[t0]]
        size = float(np.nanmean(d[t0:min(t0 + 600, len(d))])) / q_nom
        alarms.append(dict(t_det=t0, t_end=t1, size_frac=size,
                           window_s=BAL_WINDOWS[which[0]] if which else BAL_WINDOWS[-1]))
        trig_idx = trig_idx[trig_idx > t1]
        i += 1
    return rms, thr_eff, alarms


# 4. pressure waves
def _fir_step_stat():
    """FIR coefficients: mean(last 3 s) - linear extrapolation of the previous 30 s."""
    j = np.arange(3, 33)
    k = -j.astype(float)
    kbar = k.mean()
    skk = ((k - kbar) ** 2).sum()
    b_mean = np.zeros(33)
    b_mean[3:33] = 1 / 30
    b_slope = np.zeros(33)
    b_slope[3:33] = (k - kbar) / skk
    b_last = np.zeros(33)
    b_last[0:3] = 1 / 3
    return b_last - (b_mean + (-1 - kbar) * b_slope)


def step_statistic(p):
    return lfilter(_fir_step_stat(), [1.0], p, axis=0)


def npw_onsets(D, sigma, bad, k=NPW_K):
    """Per-station onset times (sub-second) of negative pressure steps."""
    out = []
    for s in range(D.shape[1]):
        z = D[:, s] / sigma[s]
        cand = np.where((z < -k) & ~bad[:, s])[0]
        last = -10 ** 9
        for i in cand:
            if i < 40 or i - last < NPW_REFRACTORY:
                continue
            seg = D[i - 3:i + 6, s]
            dmin = seg.min()
            half = 0.5 * dmin
            # first crossing of half amplitude, linear interpolation
            j = np.where(seg <= half)[0][0]
            if j == 0:
                tc = i - 3.0
            else:
                y0, y1 = seg[j - 1], seg[j]
                tc = (i - 3 + j - 1) + (half - y0) / (y1 - y0)
            out.append(dict(station=s, t=tc, amp=float(-dmin)))
            last = i
    return out


def locate_wave(onsets, x_st, a, L):
    """Grid-search origin x minimising the spread of implied emission times."""
    xs = np.array([x_st[o["station"]] for o in onsets])
    ts = np.array([o["t"] for o in onsets])
    grid = np.arange(0.0, L + 1, 50.0)
    t0 = ts[None, :] - np.abs(xs[None, :] - grid[:, None]) / a
    cost = t0.var(axis=1)
    i = int(np.argmin(cost))
    return grid[i] / 1000.0, float(np.median(t0[i])), float(np.sqrt(cost[i]))


def npw_events(onsets, cfg, min_stations=2):
    a = cfg.wave_speed
    window = cfg.length_m / a + 20
    onsets = sorted(onsets, key=lambda o: o["t"])
    groups, cur = [], []
    for o in onsets:
        if cur and o["t"] - cur[0]["t"] > window:
            groups.append(cur)
            cur = []
        if all(c["station"] != o["station"] for c in cur):
            cur.append(o)
    if cur:
        groups.append(cur)
    events = []
    last = len(cfg.stations) - 1
    for g in groups:
        stations = sorted(c["station"] for c in g)
        first = min(g, key=lambda c: c["t"])["station"]
        base = dict(n_st=len(g), stations=stations, first_station=first,
                    amp=max(c["amp"] for c in g), km_bound=np.nan, side=None,
                    t_alarm=max(c["t"] for c in g) + 3.0)   # last arrival + 3-s statistic window
        if len(g) < min_stations:
            events.append(dict(base, t_emit=g[0]["t"], km=np.nan, spread_s=np.nan, origin="Una estación"))
            continue
        km, t_emit, spread = locate_wave(g, cfg.station_m, a, cfg.length_m)
        km_st = cfg.station_km[stations]
        bracketed = km_st.min() < km < km_st.max()
        if first == 0 and km < 2.0:
            origin = "Estación de bombeo"
        elif first == last and km > cfg.length_km - 2.0:
            origin = "Terminal"
        elif bracketed:
            origin = "Interior"
        else:
            # every detecting transmitter is on the same side: the front is
            # incomplete, the origin is only bounded (beyond the first station)
            origin = "Interior (frente incompleto)"
            # The silent transmitters beyond the first one bound the origin.
            fk = cfg.station_km[first]
            silent = np.setdiff1d(np.arange(len(cfg.stations)), stations)
            if fk <= km_st.min():
                base["side"] = "aguas arriba"
                lower = cfg.station_km[silent][cfg.station_km[silent] < fk]
                lo, hi = (lower.max() if len(lower) else 0.0), fk
            else:
                base["side"] = "aguas abajo"
                upper = cfg.station_km[silent][cfg.station_km[silent] > fk]
                lo, hi = fk, (upper.min() if len(upper) else cfg.length_km)
            base["km_bound"] = fk
            base["km_lo"], base["km_hi"] = lo, hi
            km = np.nan
            t_emit = min(c["t"] for c in g)          # emission time unknown: use first arrival
            if lo <= 0.0 or hi >= cfg.length_km:
                # the interval reaches a pump/terminal station: cannot rule out an operation
                origin = "Ambiguo (borde o fuga cercana)"
        events.append(dict(base, t_emit=t_emit, km=km, spread_s=spread, origin=origin))
    return events


# Shared preparation: instrument health + twin + gap filling
def prepare(sim: SimResult, friction_scale=1.0, offsets=None):
    cfg = sim.cfg
    p_clean, bad, frozen, inst_alerts = instrument_health(sim)
    twin = Twin(cfg, friction_scale)
    t60, q60, p60, rho_cells = twin.inputs(sim, p_clean)
    p_out60 = p60[:, -1] * 1e5
    model60 = twin.station_model(q60, rho_cells, p_out60)
    off = np.zeros(len(cfg.stations)) if offsets is None else offsets
    # bad samples (spikes, frozen transmitter) are replaced by the twin estimate
    p_used = p_clean.copy()
    if bad.any():
        for s in np.where(bad.any(axis=0))[0]:
            est = np.interp(sim.t, t60, model60[:, s] + off[s])
            p_used[bad[:, s], s] = est[bad[:, s]]
    rho_mean_f = np.interp(sim.t, t60, rho_cells.mean(axis=1))
    return dict(p_clean=p_clean, p_used=p_used, bad=bad, frozen=frozen, inst_alerts=inst_alerts,
                twin=twin, t60=t60, q60=q60, p60=p60, rho_cells=rho_cells, p_out60=p_out60,
                model60=model60, rho_mean_f=rho_mean_f)


# Calibration on the commissioning period
def calibrate(sim: SimResult, commissioning_s=86400) -> Calibration:
    cfg = sim.cfg
    c = commissioning_s
    pr = prepare(sim)
    twin, t60, q60, p60, rho_cells = pr["twin"], pr["t60"], pr["q60"], pr["p60"], pr["rho_cells"]
    m = t60 < c
    p_out = pr["p_out60"][m]

    def cost(fs):
        pred = twin.station_model(q60[m], rho_cells[m], p_out, fs)
        return np.mean((p60[m, :-1] - pred[:, :-1]) ** 2)

    fs = minimize_scalar(cost, bounds=(0.8, 1.2), method="bounded").x
    twin.fs = fs
    offsets = (p60[m] - twin.station_model(q60[m], rho_cells[m], p_out)).mean(axis=0)

    e, lp = balance_signal(sim, pr["p_used"], pr["rho_mean_f"])
    bias = float(np.mean(e[:c]))
    d = (e - bias)[:c]
    thr = []
    for w, fl in zip(BAL_WINDOWS, BAL_FLOOR):
        # noise level of each window, measured outside line-pack transients
        rm = rolling_mean(d, w)
        lp_rm = np.abs(np.nan_to_num(rolling_mean(lp[:c], w)))
        quiet = lp_rm < np.nanpercentile(lp_rm, 80)
        thr.append(max(BAL_Z * np.nanstd(rm[quiet]), fl * cfg.q_nom_m3s))

    D = step_statistic(pr["p_used"])
    sig = 1.4826 * np.median(np.abs(D[60:c] - np.median(D[60:c], axis=0)), axis=0)
    return Calibration(friction_scale=float(fs), station_offsets=offsets, flow_bias=bias,
                       bal_thresholds=np.array(thr), npw_sigma=sig, commissioning_s=c)


# Full monitoring run
def twin_localize(twin, r_row, q, rho_row, p_out_pa, usable):
    grid = np.arange(0.5, twin.cfg.length_km - 0.4, 0.1)
    g = twin.leak_signature(q, rho_row, p_out_pa, grid)[:, usable]
    r = r_row[usable]
    num = (g * r[None, :]).sum(axis=1)
    den = (g * g).sum(axis=1)
    qhat = np.maximum(num / den, 0.0)
    cost = ((r[None, :] - qhat[:, None] * g) ** 2).sum(axis=1)
    i = int(np.argmin(cost))
    return float(grid[i]), float(qhat[i])


OP_ORIGINS = {"Estación de bombeo": "Transitorio originado en la estación de bombeo",
              "Terminal": "Transitorio originado en el terminal (válvula de contrapresión)",
              "Una estación": "Escalón visto en un solo transmisor (ruido local)"}


def run_monitoring(sim: SimResult, calib: Calibration, compute_dashboard=True):
    cfg = sim.cfg
    pr = prepare(sim, calib.friction_scale, calib.station_offsets)
    twin, t60, q60, p60, rho_cells, p_out60 = (pr[k] for k in ("twin", "t60", "q60", "p60", "rho_cells", "p_out60"))
    bad = pr["bad"]
    resid60 = p60 - pr["model60"] - calib.station_offsets
    bad60 = block_mean(bad.astype(float), TWIN_STEP) > 0

    # ---- volume balance
    e, lp = balance_signal(sim, pr["p_used"], pr["rho_mean_f"])
    d = e - calib.flow_bias
    rms, thr_eff, bal = balance_alarms(d, calib.bal_thresholds, cfg.q_nom_m3s, lp)

    # ---- pressure waves
    D = step_statistic(pr["p_used"])
    bad_w = bad.copy()
    for s in range(bad.shape[1]):                      # also ignore 40 s after a bad sample
        for _, b in runs(bad[:, s]):
            bad_w[b:b + 40, s] = True
    onsets = npw_onsets(D, calib.npw_sigma, bad_w)
    waves = npw_events(onsets, cfg)

    # ---- consolidate leak alarms
    alarms = []
    used_bal = set()
    for w in waves:
        common = dict(t_det=w["t_alarm"], t_emit=w["t_emit"], metodo="Onda de presión", km_onda=w["km"],
                      n_estaciones=w["n_st"], amp_bar=w["amp"], origen_onda=w["origin"],
                      km_lo=w.get("km_lo", np.nan), km_hi=w.get("km_hi", np.nan))
        if w["origin"] in OP_ORIGINS:
            alarms.append(dict(common, estado="Rechazada", tipo="Evento operacional", motivo=OP_ORIGINS[w["origin"]]))
            continue
        bound = ""
        if "km_lo" in w:
            bound = f"; frente incompleto, origen entre km {w['km_lo']:.0f} y {w['km_hi']:.0f}"
        ambiguous = w["origin"].startswith("Ambiguo")
        match = None
        for j, b in enumerate(bal):
            if j not in used_bal and w["t_emit"] - 60 <= b["t_det"] <= w["t_emit"] + 900:
                match = j
                break
        if match is not None:
            used_bal.add(match)
            b = bal[match]
            alarms.append(dict(common, t_bal=b["t_det"], t_end=b["t_end"], bal_window=b["window_s"], estado="Confirmada", tipo="Fuga",
                               metodo="Onda de presión + Balance", size_frac=b["size_frac"],
                               motivo="Onda con origen interior seguida de desbalance de volumen" + bound))
        elif ambiguous:
            alarms.append(dict(common, estado="Rechazada", tipo="Evento operacional",
                               motivo="Frente incompleto compatible con un transitorio de borde, sin desbalance" + bound))
        else:
            alarms.append(dict(common, estado="No confirmada", tipo="Posible fuga",
                               motivo="Onda con origen interior sin desbalance posterior" + bound))
    for j, b in enumerate(bal):
        if j in used_bal:
            continue
        alarms.append(dict(t_det=b["t_det"], t_bal=b["t_det"], t_end=b["t_end"], bal_window=b["window_s"], estado="Confirmada", tipo="Fuga",
                           metodo="Balance de volumen", size_frac=b["size_frac"], km_onda=np.nan,
                           motivo=f"Desbalance sobre umbral (ventana {b['window_s']//60} min)"))

    # twin localisation for every leak alarm (10-min average after the balance alarm)
    usable_all = np.arange(len(cfg.stations) - 1)          # terminal pressure is a model input
    for al in alarms:
        al.setdefault("km_onda", np.nan)
        if al["tipo"] != "Fuga":
            al["km_final"] = al["km_onda"]
            continue
        tb = al.get("t_bal", al["t_det"])
        i0 = int(tb // TWIN_STEP)
        i1 = min(i0 + 10, len(t60))
        if i1 - i0 < 3:
            al["km_final"] = al["km_onda"]
            continue
        r = resid60[i0:i1].mean(axis=0)
        ok = ~bad60[i0:i1].any(axis=0)
        if TWIN_BASELINE and "t_emit" in al:
            # The pressure wave dates the leak start, so the residual just before it
            # can be subtracted: removes slowly varying model mismatch (batches in
            # the line, flow rate). Not done for balance-only alarms, where the start
            # is unknown and a gradual leak would contaminate the baseline.
            j1 = int((al["t_emit"] - 300) // TWIN_STEP)
            j0 = j1 - 60
            if j0 >= 0:
                r = r - resid60[j0:j1].mean(axis=0)
                ok &= ~bad60[j0:j1].any(axis=0)
        usable = np.array([s for s in usable_all if ok[s]])
        km, qhat = twin_localize(twin, r, float(q60[i0:i1].mean()), rho_cells[i0:i1].mean(axis=0),
                                 float(p_out60[i0:i1].mean()), usable)
        al["km_gemelo"] = km
        al["size_twin_frac"] = qhat / cfg.q_nom_m3s
        al["km_final"] = al["km_onda"] if not np.isnan(al["km_onda"]) else km

    out = dict(alarms=alarms, waves=waves, onsets=onsets, balance_alarms=bal,
               instrument_alerts=pr["inst_alerts"], d=d, lp=lp, rms=rms, thr_eff=thr_eff)
    if not compute_dashboard:
        return out

    nodes60 = twin.node_model(q60, rho_cells, p_out60)
    out.update(t60=t60, q60=q60, p60=p60, model60=pr["model60"] + calib.station_offsets, resid60=resid60,
               bad60=bad60, rho_cells=rho_cells, nodes60=nodes60, twin=twin, p_used=pr["p_used"])
    out["envelope"] = envelope_events(cfg, t60, q60, p60, nodes60, bad60)
    out["batches"] = batch_tracking(sim)
    return out


def merged_runs(mask, gap):
    """runs() but joining runs separated by fewer than `gap` samples."""
    rs = runs(mask)
    merged = []
    for a, b in rs:
        if merged and a - merged[-1][1] < gap:
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    return merged


def envelope_events(cfg, t60, q60, p60, nodes60, bad60, gap=15):
    ev = []
    maop = np.array(cfg.maop_bar)
    hi = (p60 > cfg.maop_alert_frac * maop[None, :]) & ~bad60
    for s in range(p60.shape[1]):
        for a, b in merged_runs(hi[:, s], gap):
            pk = p60[a:b, s].max()
            ev.append(dict(start=t60[a] - 30, end=t60[b - 1] + 30, kind="Presión sobre 90% MAOP", station=s,
                           km=cfg.station_km[s], peak=float(pk),
                           detail=f"Máx {pk:.1f} bar ({pk/maop[s]*100:.1f}% MAOP {maop[s]} bar)"))
    v = q60 / cfg.area
    for a, b in merged_runs(v < cfg.v_margin * cfg.v_deposition, gap):
        ev.append(dict(start=t60[a] - 30, end=t60[b - 1] + 30, kind="Velocidad cercana a deposición", station=None,
                       km=np.nan, peak=float(v[a:b].min()),
                       detail=f"Mín {v[a:b].min():.2f} m/s (alerta bajo {cfg.v_margin*cfg.v_deposition:.2f} m/s)"))
    pmin = nodes60.min(axis=1)
    for a, b in merged_runs(pmin < cfg.min_pressure_bar, gap):
        k = a + int(np.argmin(pmin[a:b]))
        ev.append(dict(start=t60[a] - 30, end=t60[b - 1] + 30, kind="Riesgo de flujo parcial (slack)", station=None,
                       km=float(np.argmin(nodes60[k]) * cfg.cell_m / 1000), peak=float(pmin[a:b].min()),
                       detail=f"Presión mínima en línea {pmin[a:b].min():.1f} bar"))
    return ev


def batch_tracking(sim: SimResult):
    cfg = sim.cfg
    thr = 0.5 * (cfg.rho_water + cfg.rho_slurry)
    rin = median_filter(sim.rho_in_meas, 31)
    rout = median_filter(sim.rho_out_meas, 31)
    V = np.concatenate([[0.0], np.cumsum(np.maximum(sim.q_in_meas, 0))[:-1]])
    line_vol = cfg.area * cfg.length_m
    rows = []
    for k, (a, b) in enumerate(runs(rin < thr)):
        target = V[a] + line_vol
        pred = int(np.searchsorted(V, target)) if target < V[-1] else None
        arr = None
        cand = np.where(rout[a:] < thr)[0]
        if len(cand):
            arr = a + int(cand[0])
        rows.append(dict(batch=f"B-{k+1:02d}", launch=a, end=b, duration_s=b - a, pred_arrival=pred,
                         arrival=arr))
    return rows
