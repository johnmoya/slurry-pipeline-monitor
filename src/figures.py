"""Static figures for the README (light theme, reference palette)."""

import matplotlib
import matplotlib.ticker

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from .scenarios import DAY, H

# reference palette (light)
SURF = "#fcfcfb"
INK = "#0b0b0b"
INK2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
CAT = ["#2a78d6", "#eb6834", "#1baf7a"]           # methods: onda, balance, gemelo
CRIT = "#d03b3b"
WARN = "#fab219"
# ordinal blue ramp for the 8 transmitters (upstream light -> downstream dark)
RAMP = ["#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6", "#256abf", "#184f95", "#0d366b"]

plt.rcParams.update({
    "figure.facecolor": SURF, "axes.facecolor": SURF, "savefig.facecolor": SURF,
    "axes.edgecolor": AXIS, "axes.labelcolor": INK2, "xtick.color": MUTED, "ytick.color": MUTED,
    "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
    "axes.spines.top": False, "axes.spines.right": False,
    "font.family": "DejaVu Sans", "font.size": 9.5, "axes.titlesize": 11, "axes.titleweight": "bold",
    "axes.titlecolor": INK, "axes.titlelocation": "left", "legend.frameon": False,
    "lines.linewidth": 1.4,
})


def _days(x):
    return np.asarray(x) / DAY


def fig_profile(cfg, perfil, path):
    fig, ax = plt.subplots(2, 1, figsize=(9.5, 5.4), sharex=True, gridspec_kw=dict(height_ratios=[1, 1.3]))
    ax[0].fill_between(perfil.km, perfil.elevacion_m, perfil.elevacion_m.min() - 50, color="#cde2fb", lw=0)
    ax[0].plot(perfil.km, perfil.elevacion_m, color=RAMP[5], lw=2)
    ax[0].set_ylabel("Elevación (m s.n.m.)")
    ax[0].set_ylim(perfil.elevacion_m.min() - 50, perfil.elevacion_m.max() + 90)
    ax[0].set_title("Perfil del ducto de concentrado (caso sintético, 80 km)")
    for _, r in enumerate(cfg.stations):
        km = r[2]
        ax[0].axvline(km, color=AXIS, lw=0.8, zorder=0)
        ax[0].annotate(r[0], (km, cfg.elevation(km)), textcoords="offset points", xytext=(0, 7),
                       ha="center", fontsize=8, color=INK2)
    ax[1].step(perfil.km, perfil.maop_bar, where="post", color=CRIT, lw=1.4, label="MAOP por tramo")
    ax[1].plot(perfil.km, perfil.presion_nominal_pulpa_bar, color=RAMP[5], lw=2, label="Presión nominal, pulpa 1.600 kg/m³")
    ax[1].plot(perfil.km, perfil.presion_nominal_agua_bar, color=RAMP[1], lw=2, label="Presión nominal, agua")
    ax[1].axhline(cfg.min_pressure_bar, color=MUTED, lw=1, ls="--")
    ax[1].text(1, cfg.min_pressure_bar + 3, "Presión mínima para mantener la línea llena", color=MUTED, fontsize=8)
    ax[1].set_ylabel("Presión (bar)")
    ax[1].set_xlabel("km")
    ax[1].legend(loc="upper right", fontsize=8.5)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def fig_overview(cfg, sim, out, path):
    t60 = out["t60"]
    fig, ax = plt.subplots(3, 1, figsize=(11, 8.2), sharex=True, gridspec_kw=dict(height_ratios=[1.5, 1.1, 0.8]))
    for s, sid in enumerate(cfg.station_ids):
        ax[0].plot(_days(t60), out["p60"][:, s], color=RAMP[s], lw=1, label=f"{sid} (km {cfg.station_km[s]:.0f})")
    ax[0].set_ylabel("Presión (bar)")
    ax[0].set_title("14 días de operación: presiones, balance de volumen y batches")
    ax[0].legend(ncol=4, fontsize=7.5, loc="upper center", bbox_to_anchor=(0.5, -0.02))

    d60 = out["rms"][2] / cfg.q_nom_m3s * 100
    th60 = out["thr_eff"][2] / cfg.q_nom_m3s * 100
    d10 = out["rms"][1] / cfg.q_nom_m3s * 100
    th10 = out["thr_eff"][1] / cfg.q_nom_m3s * 100
    step = 30
    tt = _days(sim.t[::step])
    ax[1].plot(tt, d10[::step], color=CAT[1], lw=0.9, label="Desbalance 10 min")
    ax[1].plot(tt, th10[::step], color=CAT[1], lw=0.8, ls="--", alpha=0.7, label="Umbral 10 min")
    ax[1].plot(tt, d60[::step], color=CAT[0], lw=1.3, label="Desbalance 60 min")
    ax[1].plot(tt, th60[::step], color=CAT[0], lw=0.8, ls="--", alpha=0.7, label="Umbral 60 min")
    ax[1].set_ylabel("% del caudal nominal")
    ax[1].set_ylim(-1.5, 5.6)
    ax[1].legend(ncol=4, fontsize=7.5, loc="lower center", bbox_to_anchor=(0.5, -0.32))

    n = len(t60) * 60
    ax[2].plot(_days(t60), sim.rho_in_meas[:n].reshape(-1, 60).mean(axis=1),
               color=RAMP[2], lw=1.2, label="Densidad entrada")
    ax[2].plot(_days(t60), sim.rho_out_meas[:n].reshape(-1, 60).mean(axis=1),
               color=RAMP[7], lw=1.2, label="Densidad terminal")
    ax[2].set_ylabel("kg/m³")
    ax[2].set_xlabel("Día de operación (día 0 = comisionamiento)")
    ax[2].legend(ncol=2, fontsize=7.5, loc="lower left")

    for a in ax:
        a.axvspan(0, 1, color="#f0efec", zorder=0, lw=0)
    ax[0].text(0.5, ax[0].get_ylim()[1], "Comisionamiento", ha="center", va="top", fontsize=8, color=MUTED)
    for lk in sim.leaks:
        for a in ax:
            a.axvspan(lk.start_s / DAY, lk.end_s / DAY, color=CRIT, alpha=0.18, lw=0)
        ax[1].annotate(f"{lk.leak_id}\n{lk.size_frac*100:.1f}% · km {lk.km:.0f}", (lk.start_s / DAY, 5.5),
                       fontsize=7.5, color=CRIT, ha="center", va="top")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def fig_wave(cfg, sim, out, leak, path):
    """Sudden leak: pressure steps arriving at each transmitter and the origin fit."""
    w = min((x for x in out["waves"] if abs(x["t_emit"] - leak.start_s) < 120), key=lambda x: abs(x["t_emit"] - leak.start_s))
    t0 = leak.start_s
    lo, hi = t0 - 60, t0 + 150
    tt = sim.t[lo:hi] - t0
    fig, ax = plt.subplots(1, 2, figsize=(11, 4.4), gridspec_kw=dict(width_ratios=[1.35, 1]))
    for s in range(len(cfg.stations)):
        p = sim.p_meas[lo:hi, s]
        base = p[:55].mean()
        ax[0].plot(tt, p - base, color=RAMP[s], lw=1.1, label=f"{cfg.station_ids[s]} km {cfg.station_km[s]:.0f}")
    ax[0].axvline(0, color=CRIT, lw=1, ls="--")
    ax[0].text(1, ax[0].get_ylim()[1] * 0.9 if ax[0].get_ylim()[1] > 0 else 0.1, "inicio de la fuga", color=CRIT, fontsize=8)
    ax[0].set_xlabel("Segundos desde el inicio de la fuga")
    ax[0].set_ylabel("Presión relativa (bar)")
    ax[0].set_title(f"{leak.leak_id}: la onda negativa llega primero a los transmisores cercanos")
    ax[0].legend(fontsize=7.5, ncol=2, loc="lower left")

    ons = [o for o in out["onsets"] if o["station"] in w["stations"] and abs(o["t"] - w["t_emit"]) < 120]
    xs = np.array([cfg.station_km[o["station"]] for o in ons])
    ts_ = np.array([o["t"] - t0 for o in ons])
    grid = np.linspace(0, cfg.length_km, 400)
    model = (w["t_emit"] - t0) + np.abs(grid - w["km"]) * 1000 / cfg.wave_speed
    ax[1].plot(grid, model, color=MUTED, lw=1.2, label="Ajuste: llegada = t₀ + |x − x₀| / a")
    ax[1].scatter(xs, ts_, s=46, color=CAT[0], edgecolor=SURF, linewidth=1.5, zorder=3, label="Llegada detectada")
    ax[1].axvline(leak.km, color=CRIT, lw=1, ls="--")
    ax[1].axvline(w["km"], color=CAT[0], lw=1)
    ax[1].text(w["km"] + 1, ax[1].get_ylim()[1] * 0.92, f"estimado km {w['km']:.1f}\nreal km {leak.km:.1f}",
               fontsize=8, color=INK2, va="top")
    ax[1].set_xlabel("km")
    ax[1].set_ylabel("Segundos desde el inicio de la fuga")
    ax[1].set_title("Ubicación por tiempos de llegada")
    ax[1].legend(fontsize=7.5, loc="lower right")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def fig_gradual(cfg, sim, out, leak, alarm, path):
    """Gradual leak: no wave; balance catches it, twin residuals locate it."""
    t0, t1 = leak.start_s - 2 * H, leak.end_s + H
    tt = (sim.t[t0:t1] - leak.start_s) / 3600
    fig, ax = plt.subplots(1, 2, figsize=(11, 4.4), gridspec_kw=dict(width_ratios=[1.35, 1]))
    for k, lab, col in ((1, "10 min", CAT[1]), (2, "60 min", CAT[0])):
        ax[0].plot(tt[::10], (out["rms"][k][t0:t1] / cfg.q_nom_m3s * 100)[::10], color=col, lw=1.3, label=f"Desbalance {lab}")
        ax[0].plot(tt[::10], (out["thr_eff"][k][t0:t1] / cfg.q_nom_m3s * 100)[::10], color=col, lw=0.8, ls="--", label=f"Umbral {lab}")
    ax[0].plot(tt[::10], (sim.q_leak_true[t0:t1] / cfg.q_nom_m3s * 100)[::10], color=INK2, lw=1, ls=":", label="Fuga real (simulación)")
    td = (alarm["t_bal"] - leak.start_s) / 3600
    ax[0].axvline(td, color=CRIT, lw=1)
    ax[0].text(td + 0.05, 1.7, f"alarma\n+{td*60:.0f} min", color=CRIT, fontsize=8)
    ax[0].set_xlabel("Horas desde el inicio de la fuga")
    ax[0].set_ylabel("% del caudal nominal")
    ax[0].set_title(f"{leak.leak_id} (gradual): sin onda de presión, la detecta el balance")
    ax[0].legend(fontsize=7.5, loc="upper left")

    i0 = int(alarm["t_bal"] // 60)
    r = out["resid60"][i0:i0 + 10].mean(axis=0)
    km = cfg.station_km
    ax[1].axhline(0, color=AXIS, lw=0.8)
    ax[1].plot(km[:-1], r[:-1], "o", color=CAT[2], ms=7, mec=SURF, mew=1.5, label="Residuo medido − gemelo", zorder=3)
    # fitted signature
    twin = out["twin"]
    q = float(out["q60"][i0:i0 + 10].mean())
    rho = out["rho_cells"][i0:i0 + 10].mean(axis=0)
    pout = float(out["p60"][i0:i0 + 10, -1].mean() * 1e5)
    grid = np.array([alarm["km_gemelo"]])
    g = twin.leak_signature(q, rho, pout, grid)[0]
    qhat = alarm["size_twin_frac"] * cfg.q_nom_m3s
    ax[1].plot(km, qhat * g, color=MUTED, lw=1.2, label="Firma ajustada de la fuga")
    ax[1].axvline(leak.km, color=CRIT, lw=1, ls="--")
    ax[1].axvline(alarm["km_gemelo"], color=CAT[2], lw=1)
    ax[1].text(alarm["km_gemelo"] + 1.5, ax[1].get_ylim()[0] * 0.6, f"estimado km {alarm['km_gemelo']:.1f}\nreal km {leak.km:.1f}",
               fontsize=8, color=INK2)
    ax[1].set_xlabel("km")
    ax[1].set_ylabel("bar")
    ax[1].set_title("Ubicación por quiebre del gradiente hidráulico")
    ax[1].legend(fontsize=7.5, loc="lower right")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def fig_montecarlo(mc, path):
    """Colour = method, line style = leak type (solid sudden, dashed gradual)."""
    fig, ax = plt.subplots(1, 3, figsize=(12, 4.2))
    styles = {"repentina": "-", "gradual": "--"}

    def line(a, sub, col, label, stat):
        g = sub.groupby("tamano_pct")
        y = stat(g)
        a.plot(y.index, y.values, styles[sub.tipo_fuga.iloc[0]], marker="o", color=col, ms=6, mec=SURF, mew=1.5,
               label=label)

    for kind in ("repentina", "gradual"):
        for m, col in (("Onda de presión", CAT[0]), ("Balance de volumen", CAT[1])):
            sub = mc[(mc.tipo_fuga == kind) & (mc.metodo == m)]
            line(ax[0], sub, col, f"{m}, {kind}", lambda g: g.detectada.mean() * 100)
            if sub.detectada.any():
                line(ax[1], sub[sub.detectada == 1], col, f"{m}, {kind}", lambda g: g.tiempo_deteccion_min.median())
        for m, col in (("Onda de presión", CAT[0]), ("Gemelo digital (gradiente)", CAT[2])):
            sub = mc[(mc.tipo_fuga == kind) & (mc.metodo == m) & mc.error_ubicacion_km.notna()]
            if len(sub):
                line(ax[2], sub, col, f"{m}, {kind}", lambda g: g.error_ubicacion_km.median())
    ax[0].set_title("Probabilidad de detección")
    ax[0].set_ylabel("% de escenarios detectados")
    ax[0].set_ylim(-5, 105)
    ax[1].set_title("Tiempo de detección (mediana)")
    ax[1].set_ylabel("minutos (escala log)")
    ax[1].set_yscale("log")
    ax[1].yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:g}"))
    ax[2].set_title("Error de ubicación (mediana)")
    ax[2].set_ylabel("km")
    for a in ax:
        a.set_xlabel("Tamaño de la fuga (% del caudal nominal)")
        a.legend(fontsize=7.2)
    fig.suptitle("Monte Carlo: color = método · línea continua = fuga repentina, segmentada = gradual",
                 x=0.01, ha="left", fontsize=10, color=INK2)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def fig_compensation(cfg, sim, out, bal_raw_d, path, t_center):
    lo, hi = t_center - 1 * H, t_center + 3 * H
    tt = (sim.t[lo:hi] - t_center) / 60
    fig, ax = plt.subplots(figsize=(9.5, 3.8))
    raw10 = bal_raw_d[lo:hi] / cfg.q_nom_m3s * 100
    comp10 = out["rms"][1][lo:hi] / cfg.q_nom_m3s * 100
    th = out["thr_eff"][1][lo:hi] / cfg.q_nom_m3s * 100
    ax.plot(tt[::5], raw10[::5], color=MUTED, lw=1.3, label="Sin compensar line pack")
    ax.plot(tt[::5], comp10[::5], color=CAT[1], lw=1.5, label="Compensado")
    ax.plot(tt[::5], th[::5], color=CAT[1], lw=0.9, ls="--", label="Umbral adaptativo (10 min)")
    ax.set_xlabel("Minutos desde el cambio de caudal (+10%)")
    ax.set_ylabel("Desbalance 10 min (% nominal)")
    ax.set_title("Un aumento de caudal llena el ducto (line pack): sin compensar parece una fuga")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
