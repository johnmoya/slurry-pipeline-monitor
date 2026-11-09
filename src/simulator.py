"""Synthetic SCADA data for a slurry pipeline, 1-second resolution.

Physics, deliberately simplified (see README "Simplificaciones"):

* Quasi-steady pressure profile (Darcy-Weisbach + elevation + batch tracking),
  solved every `dt_coarse_s` seconds.
* Disturbances reach each transmitter after a travel time distance / a.
* Fast flow changes (pump changeover, sudden leaks) add a Joukowsky-type
  pressure wave  dp = rho * a * dv  (half of it each way for a leak), which
  decays with distance and relaxes towards the new steady state.
* Line pack: outlet flow differs from inlet flow while the mean line
  pressure changes (dV/dt = V / (rho a^2) dP/dt).
"""
from dataclasses import dataclass, field

import numpy as np
from scipy.signal import lfilter

from .config import PipelineConfig
from .hydraulics import Pipe, block_mean, track_density


# Scenario definition
@dataclass
class OpEvent:
    t_s: int
    kind: str            # cambio_caudal | cambio_bomba | batch_agua | ajuste_valvula
    value: float = 0.0   # new flow fraction / new back-pressure (bar) / batch duration (s)
    ramp_s: int = 300

    @property
    def description(self) -> str:
        if self.kind == "cambio_caudal":
            return f"Cambio de caudal a {self.value*100:.0f}% del nominal (rampa {self.ramp_s//60} min)"
        if self.kind == "cambio_bomba":
            return "Rotación de bomba de desplazamiento positivo (caída breve de caudal)"
        if self.kind == "batch_agua":
            return f"Lanzamiento de batch de agua ({self.value/3600:.1f} h)"
        if self.kind == "ajuste_valvula":
            return f"Ajuste de contrapresión terminal a {self.value:.0f} bar"
        return self.kind


@dataclass
class Leak:
    leak_id: str
    start_s: int
    km: float
    size_frac: float     # fraction of nominal flow
    kind: str            # repentina | gradual
    duration_s: int
    ramp_s: int = 2      # opening time (sudden) or growth time (gradual)

    @property
    def end_s(self) -> int:
        return self.start_s + self.duration_s

    def flow(self, t, q_nom):
        """Leak flow (m3/s) over the time vector t."""
        q = np.zeros_like(t, dtype=float)
        ramp = max(self.ramp_s, 1)
        frac = np.clip((t - self.start_s) / ramp, 0.0, 1.0)
        active = (t >= self.start_s) & (t < self.end_s)
        q[active] = self.size_frac * q_nom * frac[active]
        # isolation: the leak is closed over 60 s
        closing = (t >= self.end_s) & (t < self.end_s + 60)
        q[closing] = self.size_frac * q_nom * (1 - (t[closing] - self.end_s) / 60.0) * frac[closing]
        return q


@dataclass
class Faults:
    spikes: list = field(default_factory=list)      # (station_idx, t_s, +bar)
    flatlines: list = field(default_factory=list)   # (station_idx, t_start, t_end)


@dataclass
class SimResult:
    cfg: PipelineConfig
    t: np.ndarray
    events: list
    leaks: list
    faults: Faults
    # truth
    q_in_true: np.ndarray
    q_out_true: np.ndarray
    q_leak_true: np.ndarray
    rho_in_true: np.ndarray
    p_out_true: np.ndarray
    p_true_bar: np.ndarray        # (T, n_stations)
    # measurements (what SCADA sees)
    p_meas: np.ndarray            # bar
    q_in_meas: np.ndarray         # m3/s
    q_out_meas: np.ndarray
    rho_in_meas: np.ndarray
    rho_out_meas: np.ndarray


# Helpers
def ema_init(x, tau):
    """EMA started at the first value (avoids a start-up transient)."""
    a = 1.0 / tau
    zi = np.array([(1 - a) * x[0]])
    y, _ = lfilter([a], [1, -(1 - a)], x, zi=zi)
    return y


def highpass(x, tau):
    return x - ema_init(x, tau)


def ar1(rng, n, sigma, tau):
    """Zero-mean AR(1) noise with stationary std = sigma."""
    phi = np.exp(-1.0 / tau)
    e = rng.normal(0, sigma * np.sqrt(1 - phi ** 2), n)
    y, _ = lfilter([1], [1, -phi], e, zi=np.array([rng.normal(0, sigma) * phi]))
    return y


def build_inputs(cfg: PipelineConfig, T: int, events, rng):
    t = np.arange(T)
    q_frac = np.ones(T)
    rho_in = np.full(T, cfg.rho_slurry)
    p_out = np.full(T, cfg.p_out_nom_bar)
    dip = np.zeros(T)

    for ev in sorted(events, key=lambda e: e.t_s):
        t0 = ev.t_s
        if t0 >= T:
            continue
        if ev.kind == "cambio_caudal":
            start = q_frac[t0]
            r = ev.ramp_s
            seg = slice(t0, min(t0 + r, T))
            q_frac[seg] = start + (ev.value - start) * (np.arange(seg.stop - seg.start) + 1) / r
            q_frac[min(t0 + r, T):] = ev.value
        elif ev.kind == "cambio_bomba":
            # trapezoidal 6 % dip: 5 s down, 20 s hold, 5 s up
            shape = np.concatenate([np.linspace(0, 1, 5), np.ones(20), np.linspace(1, 0, 5)])
            seg = slice(t0, min(t0 + len(shape), T))
            dip[seg] = np.maximum(dip[seg], 0.06 * shape[: seg.stop - seg.start])
        elif ev.kind == "batch_agua":
            d = int(ev.value)
            trans = 60
            seg = slice(t0, min(t0 + d, T))
            rho_in[seg] = cfg.rho_water
            # smooth interfaces
            for a, b, r0, r1 in ((t0, t0 + trans, cfg.rho_slurry, cfg.rho_water),
                                 (t0 + d, t0 + d + trans, cfg.rho_water, cfg.rho_slurry)):
                s = slice(a, min(b, T))
                n = s.stop - s.start
                if n > 0:
                    rho_in[s] = r0 + (r1 - r0) * np.linspace(0, 1, trans)[:n]
        elif ev.kind == "ajuste_valvula":
            start = p_out[t0]
            r = 20
            seg = slice(t0, min(t0 + r, T))
            p_out[seg] = start + (ev.value - start) * (np.arange(seg.stop - seg.start) + 1) / r
            p_out[min(t0 + r, T):] = ev.value

    q_true = cfg.q_nom_m3s * q_frac * (1 - dip)
    q_true = q_true * (1 + ar1(rng, T, cfg.q_process_frac, cfg.q_process_tau_s))
    return t, q_true, rho_in, p_out


# Main simulation
def simulate(cfg: PipelineConfig, T: int, events, leaks, faults: Faults = None, seed: int = 0) -> SimResult:
    rng = np.random.default_rng(seed)
    faults = faults or Faults()
    dtc = cfg.dt_coarse_s
    T = (T // dtc) * dtc
    pipe = Pipe.true_pipe(cfg)
    a = cfg.wave_speed_true
    lam = cfg.wave_atten_km * 1000.0
    xs = cfg.station_m
    L = cfg.length_m
    A = cfg.area

    t, q_in, rho_in, p_out_bar = build_inputs(cfg, T, events, rng)
    tc = np.arange(0, T, dtc) + dtc / 2.0          # coarse time stamps (block centres)
    q_c = block_mean(q_in, dtc)

    # batch tracking (true)
    rho_cells = track_density(tc, t.astype(float), q_in, rho_in, pipe.x_mid, A, cfg.rho_slurry)

    # base steady profile, no leaks, zero back-pressure (added separately)
    q_cells = np.repeat(q_c[:, None], pipe.n_cells, axis=1)
    p_base_c = pipe.station_pressures(q_cells, rho_cells, np.zeros(len(tc)))

    p = np.zeros((T, len(xs)))
    hp_q = highpass(q_in, cfg.relax_s)
    for s, x in enumerate(xs):
        d_in = x / a
        p[:, s] += np.interp(t - d_in, tc, p_base_c[:, s])
        p[:, s] += np.interp(t - (L - x) / a, t, p_out_bar) * 1e5
        # surge from fast inlet-flow changes (pump changeover, pulsation)
        p[:, s] += cfg.rho_slurry * a / A * np.interp(t - d_in, t, hp_q) * np.exp(-x / lam)

    # leaks
    q_leak_total = np.zeros(T)
    for lk in leaks:
        ql = lk.flow(t, cfg.q_nom_m3s)
        q_leak_total += ql
        ql_c = block_mean(ql, dtc)
        active = np.where(ql_c > 0)[0]
        dp_c = np.zeros((len(tc), len(xs)))
        if len(active):
            qcl = pipe.leak_flow_cells(q_c[active], lk.km, ql_c[active])
            dp_c[active] = pipe.station_pressures(qcl, rho_cells[active], np.zeros(len(active))) - p_base_c[active]
        hp_l = highpass(ql, cfg.relax_s)
        cell = min(int(lk.km * 1000 / cfg.cell_m), pipe.n_cells - 1)
        rho_leak = np.interp(t, tc, rho_cells[:, cell])
        for s, x in enumerate(xs):
            dist = abs(x - lk.km * 1000.0)
            steady = np.interp(t - dist / a, tc, dp_c[:, s])
            p[:, s] += ema_init(steady, cfg.relax_s)
            wave = rho_leak * a / (2 * A) * np.interp(t - dist / a, t, hp_l) * np.exp(-dist / lam)
            p[:, s] -= wave

    p_true_bar = p / 1e5

    # line pack -> outlet flow
    w = np.gradient(xs) / np.gradient(xs).sum()           # trapezoid-like weights
    p_mean = (p * w).sum(axis=1)
    rho_mean = rho_cells.mean(axis=1)
    rho_mean_f = np.interp(t, tc, rho_mean)
    dPdt = np.gradient(ema_init(p_mean, 3.0))
    line_pack_rate = (A * L) / (rho_mean_f * a ** 2) * dPdt
    q_out = q_in - q_leak_total - line_pack_rate
    rho_out = np.interp(t, tc, rho_cells[:, -1])

    # measurements
    p_meas = p_true_bar + rng.normal(0, cfg.p_noise_bar, p_true_bar.shape)
    p_meas = np.round(p_meas, 2)                           # transmitter resolution 0.01 bar
    for s_idx, ts, amp in faults.spikes:
        if ts < T:
            p_meas[ts, s_idx] += amp
    for s_idx, t0, t1 in faults.flatlines:
        if t0 < T:
            p_meas[t0:min(t1, T), s_idx] = p_meas[t0, s_idx]

    qn = cfg.q_nom_m3s
    q_in_meas = (q_in + cfg.q_in_bias_frac * qn + rng.normal(0, cfg.q_noise_frac * qn, T)
                 + ar1(rng, T, cfg.q_drift_frac * qn, cfg.q_drift_tau_s))
    q_out_meas = (q_out + cfg.q_out_bias_frac * qn + rng.normal(0, cfg.q_noise_frac * qn, T)
                  + ar1(rng, T, cfg.q_drift_frac * qn, cfg.q_drift_tau_s))
    rho_in_meas = rho_in + rng.normal(0, cfg.rho_noise, T)
    rho_out_meas = rho_out + rng.normal(0, cfg.rho_noise, T)

    return SimResult(cfg=cfg, t=t, events=list(events), leaks=list(leaks), faults=faults,
                     q_in_true=q_in, q_out_true=q_out, q_leak_true=q_leak_total,
                     rho_in_true=rho_in, p_out_true=p_out_bar, p_true_bar=p_true_bar,
                     p_meas=p_meas, q_in_meas=q_in_meas, q_out_meas=q_out_meas,
                     rho_in_meas=rho_in_meas, rho_out_meas=rho_out_meas)
