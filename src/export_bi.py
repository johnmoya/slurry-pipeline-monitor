"""Star-schema tables for Power BI (CSV + SQLite) and the generic DDL."""
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from .config import G, PipelineConfig
from .detection import TWIN_STEP
from .hydraulics import Pipe
from .scenarios import START

METHOD_NAMES = {"onda": "Onda de presión", "balance": "Balance de volumen", "gemelo": "Gemelo digital (gradiente)"}


def ts(seconds):
    """Seconds since START -> pandas Timestamp (rounded to the second)."""
    if seconds is None or (isinstance(seconds, float) and np.isnan(seconds)):
        return pd.NaT
    return pd.Timestamp(START) + pd.to_timedelta(np.round(seconds), unit="s")


def ts_vec(seconds):
    return pd.Timestamp(START) + pd.to_timedelta(np.round(np.asarray(seconds, float)), unit="s")


def dim_tables(cfg: PipelineConfig, sim):
    est = pd.DataFrame({
        "id_estacion": cfg.station_ids,
        "nombre": [s[1] for s in cfg.stations],
        "km": cfg.station_km,
        "elevacion_m": cfg.elevation(cfg.station_km).round(1),
        "maop_bar": cfg.maop_bar,
        "alerta_maop_bar": np.round(np.array(cfg.maop_bar) * cfg.maop_alert_frac, 1),
        "orden": np.arange(1, len(cfg.stations) + 1),
    })

    pipe = Pipe(cfg)
    q = np.full(pipe.n_cells, cfg.q_nom_m3s)
    p_s = pipe.node_pressures(q, np.full(pipe.n_cells, cfg.rho_slurry), cfg.p_out_nom_bar * 1e5) / 1e5
    p_w = pipe.node_pressures(q, np.full(pipe.n_cells, cfg.rho_water), cfg.p_out_nom_bar * 1e5) / 1e5
    km = pipe.x_nodes / 1000
    maop_step = np.array(cfg.maop_bar)[np.searchsorted(cfg.station_km, km, side="right") - 1]
    perfil = pd.DataFrame({
        "km": km,
        "elevacion_m": pipe.z_nodes.round(1),
        "presion_nominal_pulpa_bar": p_s.round(2),
        "presion_nominal_agua_bar": p_w.round(2),
        "linea_gradiente_hidraulico_m": (pipe.z_nodes + p_s * 1e5 / (cfg.rho_slurry * G)).round(1),
        "maop_bar": maop_step,
    })

    days = pd.date_range(START, periods=int(np.ceil(len(sim.t) / 86400)), freq="D")
    nombres = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
    fecha = pd.DataFrame({
        "fecha": days.date,
        "anio": days.year, "mes": days.month, "dia": days.day,
        "dia_semana": [nombres[d.weekday()] for d in days],
        "dia_operacion": np.arange(len(days)),
        "periodo": ["Comisionamiento" if i == 0 else "Operación monitoreada" for i in range(len(days))],
    })
    return est, perfil, fecha


def fact_minute(cfg, sim, out, calib):
    t60 = out["t60"]
    n = len(t60)
    stamp = ts_vec(t60 - TWIN_STEP / 2)
    # pressures
    rows = []
    for s, sid in enumerate(cfg.station_ids):
        rows.append(pd.DataFrame({
            "fecha_hora": stamp, "fecha": stamp.date, "id_estacion": sid,
            "presion_bar": out["p60"][:, s].round(3),
            "presion_modelo_bar": out["model60"][:, s].round(3),
            "residuo_bar": out["resid60"][:, s].round(3),
            "dato_valido": (~out["bad60"][:, s]).astype(int),
        }))
    presion = pd.concat(rows, ignore_index=True)

    # operation
    qn = cfg.q_nom_m3s

    def per_min(x):
        m = (len(x) // TWIN_STEP) * TWIN_STEP
        return np.asarray(x)[:m].reshape(-1, TWIN_STEP)

    def at_min_end(x):
        return per_min(x)[:, -1]

    d10 = at_min_end(out["rms"][1]) / qn * 100
    d60 = at_min_end(out["rms"][2]) / qn * 100
    th10 = at_min_end(out["thr_eff"][1]) / qn * 100
    th60 = at_min_end(out["thr_eff"][2]) / qn * 100
    q_in = per_min(sim.q_in_meas).mean(axis=1)
    q_out = per_min(sim.q_out_meas).mean(axis=1)
    rho_in = per_min(sim.rho_in_meas).mean(axis=1)
    rho_out = per_min(sim.rho_out_meas).mean(axis=1)
    v = q_in / cfg.area
    p_dis = out["p60"][:, 0]
    kw = q_in * p_dis * 1e5 / cfg.pump_efficiency / 1000
    kwh = kw / 60
    cv = np.clip(cfg.solids_vol_frac(rho_in), 0, None)
    solids_t = q_in * 60 * cv * cfg.rho_solids / 1000
    nodes = out["nodes60"]
    pmin = nodes.min(axis=1)
    kmin = np.argmin(nodes, axis=1) * cfg.cell_m / 1000
    leak_true = per_min(sim.q_leak_true).mean(axis=1) * 3600
    op = pd.DataFrame({
        "fecha_hora": stamp,
        "fecha": stamp.date,
        "caudal_entrada_m3h": (q_in * 3600).round(2),
        "caudal_salida_m3h": (q_out * 3600).round(2),
        "desbalance_10min_pct": np.round(d10, 3),
        "umbral_10min_pct": np.round(th10, 3),
        "desbalance_60min_pct": np.round(d60, 3),
        "umbral_60min_pct": np.round(th60, 3),
        "densidad_entrada_kgm3": rho_in.round(1),
        "densidad_salida_kgm3": rho_out.round(1),
        "velocidad_ms": v.round(3),
        "velocidad_alerta_ms": round(cfg.v_margin * cfg.v_deposition, 3),
        "velocidad_deposicion_ms": cfg.v_deposition,
        "presion_min_linea_bar": pmin.round(2),
        "km_presion_min": kmin.round(1),
        "potencia_bombeo_kw": kw.round(1),
        "energia_kwh": kwh.round(3),
        "solidos_t": solids_t.round(3),
        "emisiones_tco2e": (kwh / 1000 * cfg.emission_factor_t_mwh).round(5),
        "fuga_real_m3h_simulacion": leak_true.round(3),
    })
    op = op.iloc[:n]
    return presion, op


def fact_events(cfg, sim, out, scorecard):
    ev = pd.DataFrame([dict(id_evento=f"EV-{i+1:03d}", fecha_hora=ts(e.t_s), tipo=e.kind.replace("_", " ").capitalize(),
                            descripcion=e.description) for i, e in enumerate(sorted(sim.events, key=lambda e: e.t_s))])

    leaks = pd.DataFrame([dict(
        id_fuga=lk.leak_id, inicio=ts(lk.start_s), fin=ts(lk.end_s), tipo=lk.kind.capitalize(), km=lk.km,
        tamano_pct=lk.size_frac * 100, duracion_min=lk.duration_s / 60,
        volumen_perdido_m3=round(float(lk.flow(sim.t, cfg.q_nom_m3s).sum()), 1)) for lk in sim.leaks])

    al_rows = []
    for i, a in enumerate(sorted(out["alarms"], key=lambda a: a["t_det"])):
        lk = next((l for l in sim.leaks if l.leak_id == a.get("fuga_real")), None)
        km_final = a.get("km_final", np.nan)
        al_rows.append(dict(
            id_alarma=f"AL-{i+1:03d}",
            fecha_hora_alarma=ts(a["t_det"]),
            fecha_hora_confirmacion=ts(a["t_bal"]) if a.get("estado") == "Confirmada" and "t_bal" in a else pd.NaT,
            fecha_hora_fin=ts(a["t_end"]) if "t_end" in a else pd.NaT,
            fecha=ts(a["t_det"]).date(),
            tipo=a["tipo"], estado=a["estado"], metodo=a["metodo"], motivo=a.get("motivo", ""),
            origen_onda=a.get("origen_onda", ""),
            km_onda=a.get("km_onda", np.nan), km_gemelo=a.get("km_gemelo", np.nan), km_estimado=km_final,
            km_rango_desde=a.get("km_lo", np.nan), km_rango_hasta=a.get("km_hi", np.nan),
            tamano_est_pct=a.get("size_frac", np.nan) * 100 if "size_frac" in a else np.nan,
            n_estaciones=a.get("n_estaciones", np.nan), amplitud_bar=a.get("amp_bar", np.nan),
            id_fuga_real=a.get("fuga_real"),
            km_real=lk.km if lk else np.nan,
            error_km=abs(km_final - lk.km) if lk and not np.isnan(km_final) else np.nan,
            tiempo_alarma_min=(a["t_det"] - lk.start_s) / 60 if lk else np.nan,
            tiempo_confirmacion_min=(a["t_bal"] - lk.start_s) / 60 if lk and "t_bal" in a else np.nan,
            es_falsa_alarma=int(a["tipo"] in ("Fuga", "Posible fuga") and lk is None),
        ))
    alarmas = pd.DataFrame(al_rows)
    num = ["km_onda", "km_gemelo", "km_estimado", "km_rango_desde", "km_rango_hasta", "tamano_est_pct",
           "amplitud_bar", "error_km", "tiempo_alarma_min", "tiempo_confirmacion_min"]
    alarmas[num] = alarmas[num].astype(float).round(3)

    env = pd.DataFrame([dict(id_alerta=f"EN-{i+1:03d}", inicio=ts(e["start"]), fin=ts(e["end"]), tipo=e["kind"],
                             id_estacion=cfg.station_ids[e["station"]] if e["station"] is not None else None,
                             km=e["km"], valor_pico=round(e["peak"], 2), detalle=e["detail"],
                             duracion_min=round((e["end"] - e["start"]) / 60, 1))
                        for i, e in enumerate(sorted(out["envelope"], key=lambda e: e["start"]))])

    inst = pd.DataFrame([dict(id_alerta=f"IN-{i+1:03d}", inicio=ts(e["start"]), fin=ts(e["end"]),
                              id_estacion=cfg.station_ids[e["station"]], tipo=e["kind"], detalle=e["detail"],
                              duracion_min=round((e["end"] - e["start"]) / 60, 1))
                         for i, e in enumerate(out["instrument_alerts"])])

    bat = pd.DataFrame([dict(id_batch=b["batch"], lanzamiento=ts(b["launch"]), fin_lanzamiento=ts(b["end"]),
                             duracion_min=round(b["duration_s"] / 60, 1),
                             llegada_estimada=ts(b["pred_arrival"]) if b["pred_arrival"] else pd.NaT,
                             llegada_real=ts(b["arrival"]) if b["arrival"] else pd.NaT,
                             tiempo_transito_h=round((b["arrival"] - b["launch"]) / 3600, 2) if b["arrival"] else np.nan,
                             error_min=round((b["pred_arrival"] - b["arrival"]) / 60, 1)
                             if b["pred_arrival"] and b["arrival"] else np.nan)
                        for b in out["batches"]])

    # pressure-wave arrivals (for the "V" chart of arrival time vs km)
    waves, arrivals = [], []
    for i, w in enumerate(sorted(out["waves"], key=lambda w: w["t_emit"])):
        wid = f"ON-{i+1:03d}"
        waves.append(dict(id_onda=wid, fecha_hora_emision=ts(w["t_emit"]), origen=w["origin"],
                          km_origen=round(w["km"], 2) if not np.isnan(w["km"]) else np.nan,
                          n_estaciones=w["n_st"], amplitud_max_bar=round(w["amp"], 3)))
    ons = sorted(out["onsets"], key=lambda o: o["t"])
    for i, w in enumerate(sorted(out["waves"], key=lambda w: w["t_emit"])):
        for o in ons:
            if o["station"] in w["stations"] and abs(o["t"] - w["t_emit"]) <= cfg.length_m / cfg.wave_speed + 25:
                arrivals.append(dict(id_onda=f"ON-{i+1:03d}", id_estacion=cfg.station_ids[o["station"]],
                                     km=cfg.station_km[o["station"]],
                                     segundos_desde_emision=round(o["t"] - w["t_emit"], 2),
                                     amplitud_bar=round(o["amp"], 3)))
    ondas = pd.DataFrame(waves)
    llegadas = pd.DataFrame(arrivals).drop_duplicates(["id_onda", "id_estacion"])

    # 1-second pressure around the start of each simulated leak (drill-through page).
    # Centred on the true start so every leak (wave-detected or not) is comparable.
    zoom = []
    for lk in sim.leaks:
        t0 = int(lk.start_s)
        lo, hi = max(t0 - 120, 0), min(t0 + 300, len(sim.t))
        for s, sid in enumerate(cfg.station_ids):
            zoom.append(pd.DataFrame({
                "id_fuga_real": lk.leak_id, "fecha_hora": ts_vec(sim.t[lo:hi]),
                "segundos_rel": sim.t[lo:hi] - t0, "id_estacion": sid,
                "presion_bar": sim.p_meas[lo:hi, s]}))
    zoom = pd.concat(zoom, ignore_index=True) if zoom else pd.DataFrame()

    return dict(fact_eventos_operacionales=ev, fact_fugas_reales=leaks, fact_alarmas=alarmas,
                fact_alertas_envolvente=env, fact_alertas_instrumentos=inst, fact_batches=bat,
                fact_ondas=ondas, fact_llegadas_onda=llegadas, fact_presion_seg_fugas=zoom,
                fact_scorecard_fugas=scorecard)


def mc_tables(mc, free, comparison):
    mc = mc.copy()
    mc["metodo"] = mc["metodo"].map(METHOD_NAMES)
    mc["detectada"] = mc["detectada"].astype(int)
    for c in ("tiempo_deteccion_min", "error_ubicacion_km"):
        mc[c] = mc[c].astype(float).round(3)
    return dict(fact_mc_desempeno=mc, fact_mc_sin_fuga=free, fact_comparacion_balance=comparison)
PRIMARY_KEYS = {"dim_estacion": "id_estacion", "dim_perfil": "km", "dim_fecha": "fecha",
                "fact_alarmas": "id_alarma", "fact_fugas_reales": "id_fuga", "fact_eventos_operacionales": "id_evento",
                "fact_alertas_envolvente": "id_alerta", "fact_alertas_instrumentos": "id_alerta",
                "fact_batches": "id_batch", "fact_ondas": "id_onda"}


def ddl(tables):
    lines = ["-- Esquema generado por src/export_bi.py (ANSI SQL / PostgreSQL).",
             "-- Las mismas tablas están cargadas en data/ducto_monitoreo.sqlite.", ""]
    for name, df in tables.items():
        cols = []
        for c, dt in df.dtypes.items():
            if pd.api.types.is_datetime64_any_dtype(dt):
                typ = "TIMESTAMP"
            elif pd.api.types.is_bool_dtype(dt):
                typ = "BOOLEAN"
            elif pd.api.types.is_integer_dtype(dt):
                typ = "INTEGER"
            elif pd.api.types.is_float_dtype(dt):
                typ = "DOUBLE PRECISION"
            else:
                typ = "VARCHAR(200)"
            if c in ("fecha",) and name != "dim_fecha":
                typ = "DATE"
            if name == "dim_fecha" and c == "fecha":
                typ = "DATE"
            cols.append(f"    {c} {typ}")
        if name in PRIMARY_KEYS:
            cols.append(f"    PRIMARY KEY ({PRIMARY_KEYS[name]})")
        lines.append(f"CREATE TABLE {name} (\n" + ",\n".join(cols) + "\n);\n")
    return "\n".join(lines)


def write_all(tables, root: Path):
    csv_dir = root / "data" / "powerbi"
    csv_dir.mkdir(parents=True, exist_ok=True)
    db = root / "data" / "ducto_monitoreo.sqlite"
    if db.exists():
        db.unlink()
    con = sqlite3.connect(db)
    for name, df in tables.items():
        out = df.copy()
        for c in out.columns:
            if pd.api.types.is_datetime64_any_dtype(out[c]):
                out[c] = out[c].dt.strftime("%Y-%m-%d %H:%M:%S")
        out.to_csv(csv_dir / f"{name}.csv", index=False, encoding="utf-8")
        out.to_sql(name, con, index=False)
    views = (root / "sql" / "vistas.sql").read_text(encoding="utf-8")
    con.executescript(views)
    con.commit()
    con.close()
    (root / "sql" / "schema.sql").write_text(ddl(tables), encoding="utf-8")
    return csv_dir, db
