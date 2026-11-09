"""End-to-end run: simulate -> calibrate -> monitor -> evaluate -> export -> dashboard.

    python run_all.py            # full run (~1-2 min)
    python run_all.py --rapido   # smaller Monte Carlo for a quick check
"""
import argparse
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from src.config import PipelineConfig
from src.detection import calibrate, rolling_mean, run_monitoring
from src.evaluation import (false_alarm_count, leak_scorecard, match_alarms, monte_carlo,
                            uncompensated_comparison)
from src.export_bi import dim_tables, fact_events, fact_minute, mc_tables, write_all
from src.figures import (fig_compensation, fig_gradual, fig_montecarlo, fig_overview, fig_profile,
                         fig_wave)
from src.export_web import main as export_dashboard
from src.scenarios import main_scenario, t_
from src.simulator import simulate

ROOT = Path(__file__).parent


def main(fast=False):
    t_start = time.time()
    cfg = PipelineConfig()

    print("1/7 Simulando 14 días de SCADA a 1 segundo...")
    T, events, leaks, faults = main_scenario()
    sim = simulate(cfg, T, events, leaks, faults, seed=42)

    print("2/7 Calibrando con el día de comisionamiento...")
    calib = calibrate(sim)

    print("3/7 Ejecutando monitoreo (gemelo, balance, ondas, envolvente, batches)...")
    out = run_monitoring(sim, calib)
    match_alarms(out["alarms"], leaks)
    score = leak_scorecard(out["alarms"], leaks)
    comparison, _ = uncompensated_comparison(sim, out, calib)

    print("4/7 Monte Carlo de desempeño...")
    reps, n_free = (6, 5) if fast else (25, 30)
    mc, free = monte_carlo(cfg, calib, reps=reps, n_free=n_free)

    print("5/7 Exportando tablas para Power BI...")
    est, perfil, fecha = dim_tables(cfg, sim)
    presion, operacion = fact_minute(cfg, sim, out, calib)
    tables = dict(dim_estacion=est, dim_perfil=perfil, dim_fecha=fecha,
                  fact_presion_min=presion, fact_operacion_min=operacion)
    sc = score.copy()
    for c in ("t_alarma_s", "t_confirmacion_s"):
        sc[c.replace("_s", "_min")] = (sc[c] / 60).round(2)
    sc = sc.drop(columns=["t_alarma_s", "t_confirmacion_s"]).round(3)
    tables.update(fact_events(cfg, sim, out, sc))
    tables.update(mc_tables(mc, free, comparison))
    csv_dir, db = write_all(tables, ROOT)

    print("6/7 Generando figuras...")
    figs = ROOT / "figures"
    figs.mkdir(exist_ok=True)
    fig_profile(cfg, perfil, figs / "01_perfil_ducto.png")
    fig_overview(cfg, sim, out, figs / "02_resumen_14_dias.png")
    fig_wave(cfg, sim, out, leaks[0], figs / "03_onda_presion_F-01.png")
    alarm_f02 = next(a for a in out["alarms"] if a.get("fuga_real") == "F-02")
    fig_gradual(cfg, sim, out, leaks[1], alarm_f02, figs / "04_fuga_gradual_F-02.png")
    fig_montecarlo(tables["fact_mc_desempeno"], figs / "05_montecarlo_desempeno.png")
    e_raw = sim.q_in_meas - sim.q_out_meas
    raw10 = rolling_mean(e_raw - np.mean(e_raw[:calib.commissioning_s]), 600)
    fig_compensation(cfg, sim, out, raw10, figs / "06_compensacion_line_pack.png", t_(8, 13))

    print("7/7 Construyendo el dashboard web (docs/index.html)...")
    dash_path, _ = export_dashboard()

    # summary
    mcd = tables["fact_mc_desempeno"]
    summary = dict(
        fugas_simuladas=len(leaks),
        fugas_detectadas=int(score.detectada.sum()),
        falsas_alarmas_14_dias=false_alarm_count(out["alarms"]),
        transitorios_operacionales_rechazados=int(sum(a["estado"] == "Rechazada" for a in out["alarms"])),
        falsas_alarmas_sin_compensar=int(comparison.falsas_alarmas.iloc[0]),
        mc_escenarios_con_fuga=int(mcd.escenario.nunique()),
        mc_dias_sin_fuga=float(free.horas.sum() / 24),
        mc_falsas_alarmas_sin_fuga=int(free.falsas_alarmas.sum()),
        friccion_calibrada=round(calib.friction_scale * cfg.friction_true, 5),
        umbrales_balance_pct=[round(x, 3) for x in (calib.bal_thresholds / cfg.q_nom_m3s * 100)],
        segundos=round(time.time() - t_start, 1),
    )
    (ROOT / "data" / "resumen_ejecucion.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    pd.set_option("display.width", 160)
    print("\nFugas del escenario de 14 días:")
    print(score[["fuga", "tipo", "km_real", "tamano_real_pct", "detectada", "metodo", "t_alarma_s",
                 "km_final", "error_final_km", "tamano_est_pct"]].round(2).to_string(index=False))
    print("\nMonte Carlo (probabilidad de detección %):")
    print((mcd.groupby(["metodo", "tipo_fuga", "tamano_pct"]).detectada.mean().unstack() * 100).round(0).to_string())
    print("\n" + json.dumps(summary, indent=2, ensure_ascii=False))
    print(f"\nTablas: {csv_dir}\nBase SQLite: {db}\nFiguras: {figs}\nDashboard: {dash_path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--rapido", action="store_true")
    main(fast=ap.parse_args().rapido)
