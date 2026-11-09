"""Scoring the monitoring layer against ground truth.

Metrics follow the spirit of API RP 1130 (computational pipeline monitoring):
sensitivity (smallest leak detected), reliability (false alarms),
accuracy (location and size estimates) and robustness (behaviour during
transients and instrument faults).
"""
import numpy as np
import pandas as pd

from .config import PipelineConfig
from .detection import balance_alarms, run_monitoring
from .scenarios import random_scenario
from .simulator import simulate

LEAK_TYPES = ("Fuga", "Posible fuga")


def match_alarms(alarms, leaks, pre_s=120, post_s=600):
    """Assign each leak-type alarm to the leak it belongs to (or to none)."""
    for al in alarms:
        al["fuga_real"] = None
        if al["tipo"] not in LEAK_TYPES:
            continue
        for lk in leaks:
            if lk.start_s - pre_s <= al["t_det"] <= lk.end_s + post_s:
                al["fuga_real"] = lk.leak_id
                break
    return alarms


def leak_scorecard(alarms, leaks, waves=None, balance=None):
    rows = []
    for lk in leaks:
        mine = [a for a in alarms if a.get("fuga_real") == lk.leak_id]
        row = dict(fuga=lk.leak_id, tipo=lk.kind, km_real=lk.km, tamano_real_pct=lk.size_frac * 100,
                   detectada=bool(mine))
        if mine:
            a = min(mine, key=lambda x: x["t_det"])
            row.update(
                estado=a["estado"], metodo=a["metodo"],
                t_alarma_s=a["t_det"] - lk.start_s,
                t_confirmacion_s=(a.get("t_bal", np.nan) - lk.start_s) if a["estado"] == "Confirmada" else np.nan,
                km_onda=a.get("km_onda", np.nan), km_gemelo=a.get("km_gemelo", np.nan),
                km_final=a.get("km_final", np.nan),
                tamano_est_pct=a.get("size_frac", np.nan) * 100,
            )
            row["error_onda_km"] = abs(row["km_onda"] - lk.km)
            row["error_gemelo_km"] = abs(row["km_gemelo"] - lk.km)
            row["error_final_km"] = abs(row["km_final"] - lk.km)
            # true leak size at the moment the balance alarm was raised
            tb = a.get("t_bal", a["t_det"])
            frac = np.clip((tb - lk.start_s) / max(lk.ramp_s, 1), 0, 1)
            row["tamano_real_en_alarma_pct"] = lk.size_frac * frac * 100
        rows.append(row)
    return pd.DataFrame(rows)


def false_alarm_count(alarms):
    return sum(1 for a in alarms if a["tipo"] in LEAK_TYPES and a.get("fuga_real") is None)


# Per-method detection in one run (used by the Monte Carlo study)
def per_method(out, leak, a_speed):
    res = {}
    # pressure wave: an interior (or incomplete-front) event after the leak start
    w_ok = [w for w in out["waves"] if w["origin"].startswith("Interior")
            and leak.start_s - 60 <= w["t_emit"] <= leak.start_s + 600]
    if w_ok:
        w = min(w_ok, key=lambda x: x["t_emit"])
        res["onda"] = dict(det=True, t=w["t_alarm"] - leak.start_s,
                           err=abs(w["km"] - leak.km) if not np.isnan(w["km"]) else np.nan)
    else:
        res["onda"] = dict(det=False, t=np.nan, err=np.nan)
    b_ok = [b for b in out["balance_alarms"] if leak.start_s - 60 <= b["t_det"] <= leak.end_s + 600]
    if b_ok:
        b = min(b_ok, key=lambda x: x["t_det"])
        al = [a for a in out["alarms"] if a.get("t_bal") == b["t_det"]]
        km_tw = al[0].get("km_gemelo", np.nan) if al else np.nan
        res["balance"] = dict(det=True, t=b["t_det"] - leak.start_s, err=np.nan)
        res["gemelo"] = dict(det=not np.isnan(km_tw), t=b["t_det"] - leak.start_s + 600,
                             err=abs(km_tw - leak.km) if not np.isnan(km_tw) else np.nan)
    else:
        res["balance"] = dict(det=False, t=np.nan, err=np.nan)
        res["gemelo"] = dict(det=False, t=np.nan, err=np.nan)
    return res


def monte_carlo(cfg: PipelineConfig, calib, sizes=(0.005, 0.01, 0.02, 0.03, 0.05),
                kinds=("repentina", "gradual"), reps=20, n_free=20, seed=2026, log=print):
    rng = np.random.default_rng(seed)
    rows = []
    k = 0
    for kind in kinds:
        for size in sizes:
            for r in range(reps):
                T, ev, leaks, faults = random_scenario(rng, size, kind, idx=k)
                sim = simulate(cfg, T, ev, leaks, faults, seed=int(rng.integers(1e9)))
                out = run_monitoring(sim, calib, compute_dashboard=False)
                match_alarms(out["alarms"], leaks)
                lk = leaks[0]
                pm = per_method(out, lk, cfg.wave_speed)
                fa = false_alarm_count(out["alarms"])
                for method, v in pm.items():
                    rows.append(dict(escenario=lk.leak_id, tipo_fuga=kind, tamano_pct=size * 100, km_real=lk.km,
                                     metodo=method, detectada=v["det"], tiempo_deteccion_min=v["t"] / 60,
                                     error_ubicacion_km=v["err"], falsas_alarmas=fa))
                k += 1
        log(f"  Monte Carlo {kind}: {len(sizes) * reps} escenarios")
    # leak-free runs -> false-alarm rate
    free = []
    for i in range(n_free):
        T, ev, leaks, faults = random_scenario(rng, 0, "repentina", duration_s=24 * 3600, leak=False, idx=i)
        sim = simulate(cfg, T, ev, leaks, faults, seed=int(rng.integers(1e9)))
        out = run_monitoring(sim, calib, compute_dashboard=False)
        fa = [a for a in out["alarms"] if a["tipo"] in LEAK_TYPES]
        free.append(dict(escenario=f"SF-{i:02d}", horas=T / 3600,
                         falsas_alarmas=len(fa),
                         detalle_falsas="; ".join(f"{a['metodo']} ({a['estado']}): {a['motivo']}" for a in fa),
                         eventos_operacionales_rechazados=sum(1 for a in out["alarms"] if a["estado"] == "Rechazada")))
    log(f"  Monte Carlo sin fuga: {n_free} días simulados")
    return pd.DataFrame(rows), pd.DataFrame(free)


def uncompensated_comparison(sim, out, calib):
    """Same thresholds, no line-pack compensation: how many false alarms?"""
    cfg = sim.cfg
    e_raw = sim.q_in_meas - sim.q_out_meas
    bias = float(np.mean(e_raw[: calib.commissioning_s]))
    _, _, bal_raw = balance_alarms(e_raw - bias, calib.bal_thresholds, cfg.q_nom_m3s, lp=None)
    leaks = sim.leaks

    def is_false(t):
        return not any(lk.start_s - 120 <= t <= lk.end_s + 600 for lk in leaks)

    days = (len(sim.t) - calib.commissioning_s) / 86400
    raw_false = sum(1 for b in bal_raw if b["t_det"] >= calib.commissioning_s and is_false(b["t_det"]))
    comp_false = sum(1 for b in out["balance_alarms"] if b["t_det"] >= calib.commissioning_s and is_false(b["t_det"]))
    return pd.DataFrame([
        dict(variante="Sin compensar line pack", falsas_alarmas=raw_false, dias=days, por_dia=raw_false / days),
        dict(variante="Compensado + umbral adaptativo", falsas_alarmas=comp_false, dias=days, por_dia=comp_false / days),
    ]), bal_raw
