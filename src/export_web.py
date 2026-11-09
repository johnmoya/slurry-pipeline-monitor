"""Builds the self-contained web dashboard (docs/index.html) from data/powerbi/*.csv.

    python -m src.export_web

The page loads ECharts from cdnjs; all data is embedded as JSON.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
CSV = ROOT / "data" / "powerbi"
TEMPLATE = ROOT / "web" / "dashboard.html"
STEP = 3  # minutes per point in the time series

# Values from src/config.py that the page shows as reference lines
CFG = dict(v_alert=1.43, v_dep=1.30, p_min=3.0, maop_frac=0.90, length_km=80.0,
           emission_factor=0.30, q_nom_m3h=198.0)


def read(name, **kw):
    return pd.read_csv(CSV / f"{name}.csv", **kw)


def minutes(ts, start):
    return ((pd.to_datetime(ts) - start).dt.total_seconds() / 60).round(2)


def r(x, d=2):
    """Round and replace NaN by None (JSON null)."""
    a = np.round(np.asarray(x, dtype=float), d)
    return [None if np.isnan(v) else float(v) for v in a]


def clean(v):
    if isinstance(v, (float, np.floating)):
        return None if np.isnan(v) else round(float(v), 3)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.bool_,)):
        return bool(v)
    return v


def records(df):
    return [{k: clean(v) for k, v in row.items()} for row in df.to_dict("records")]


def build():
    est = read("dim_estacion")
    perfil = read("dim_perfil")
    fecha = read("dim_fecha")
    op = read("fact_operacion_min", parse_dates=["fecha_hora"])
    pres = read("fact_presion_min", parse_dates=["fecha_hora"])
    alarmas = read("fact_alarmas")
    fugas = read("fact_fugas_reales")
    score = read("fact_scorecard_fugas")
    eventos = read("fact_eventos_operacionales")
    env = read("fact_alertas_envolvente")
    inst = read("fact_alertas_instrumentos")
    bat = read("fact_batches")
    ondas = read("fact_ondas")
    lleg = read("fact_llegadas_onda")
    zoom = read("fact_presion_seg_fugas")
    mc = read("fact_mc_desempeno")
    free = read("fact_mc_sin_fuga")
    comp = read("fact_comparacion_balance")

    start = op.fecha_hora.min()
    sids = list(est.id_estacion)

    # time series (STEP-minute means)
    g = np.arange(len(op)) // STEP
    agg = op.groupby(g).agg(
        t=("fecha_hora", "first"), q_in=("caudal_entrada_m3h", "mean"), q_out=("caudal_salida_m3h", "mean"),
        d10=("desbalance_10min_pct", "mean"), th10=("umbral_10min_pct", "mean"),
        d60=("desbalance_60min_pct", "mean"), th60=("umbral_60min_pct", "mean"),
        rho_in=("densidad_entrada_kgm3", "mean"), rho_out=("densidad_salida_kgm3", "mean"),
        v=("velocidad_ms", "min"), pmin=("presion_min_linea_bar", "min"))
    ts = dict(step=STEP, t0=0,
              q_in=r(agg.q_in, 1), q_out=r(agg.q_out, 1), d10=r(agg.d10, 3), th10=r(agg.th10, 3),
              d60=r(agg.d60, 3), th60=r(agg.th60, 3), rho_in=r(agg.rho_in, 0), rho_out=r(agg.rho_out, 0),
              v=r(agg.v, 3), pmin=r(agg.pmin, 1))
    pw = pres.pivot_table(index="fecha_hora", columns="id_estacion", values="presion_bar")[sids]
    pw = pw.groupby(np.arange(len(pw)) // STEP).mean()
    p = [r(pw[s], 1) for s in sids]

    # daily summary
    op["dia"] = op.fecha_hora.dt.date.astype(str)
    al_day = alarmas.assign(dia=pd.to_datetime(alarmas.fecha_hora_alarma).dt.date.astype(str))
    pres["dia"] = pres.fecha_hora.dt.date.astype(str)
    days = []
    for i, row in fecha.iterrows():
        d = str(row.fecha)
        o = op[op.dia == d]
        a = al_day[al_day.dia == d]
        pr = pres[pres.dia == d]
        kwh = o.energia_kwh.sum()
        sol = o.solidos_t.sum()
        days.append(dict(
            fecha=d, dia=int(row.dia_operacion), periodo=row.periodo,
            vol=round(o.caudal_entrada_m3h.sum() / 60, 0), solidos=round(sol, 0),
            mwh=round(kwh / 1000, 2), kwh_t=round(kwh / sol, 2) if sol else None,
            tco2=round(o.emisiones_tco2e.sum(), 2), vmin=round(o.velocidad_ms.min(), 2),
            fugas=int(((a.tipo == "Fuga") & (a.estado == "Confirmada")).sum()),
            falsas=int(a.es_falsa_alarma.sum()), rechazados=int((a.estado == "Rechazada").sum()),
            disp=round(pr.dato_valido.mean() * 100, 2),
        ))

    # residual heat map (daily mean |residual| per station)
    heat = (pres.assign(a=pres.residuo_bar.abs()).groupby(["dia", "id_estacion"]).a.mean().reset_index())
    day_idx = {d["fecha"]: k for k, d in enumerate(days)}
    resid = [[day_idx[x.dia], sids.index(x.id_estacion), round(x.a, 3)] for x in heat.itertuples()]

    # leaks: scorecard + zoom + arrivals + twin residual
    res_w = pres.pivot_table(index="fecha_hora", columns="id_estacion", values="residuo_bar")[sids]
    leaks = []
    for f in fugas.itertuples():
        sc = score[score.fuga == f.id_fuga].iloc[0]
        t_start = pd.Timestamp(f.inicio)
        al = alarmas[(alarmas.id_fuga_real == f.id_fuga)].sort_values("fecha_hora_alarma")
        first = al.iloc[0] if len(al) else None
        z = zoom[zoom.id_fuga_real == f.id_fuga].pivot_table(index="segundos_rel", columns="id_estacion",
                                                              values="presion_bar")[sids]
        base = z[z.index < -20].mean()
        zr = z - base
        # wave arrivals (interior origin, emitted within 10 min of the true start)
        ow = ondas.assign(te=pd.to_datetime(ondas.fecha_hora_emision))
        ow = ow[ow.origen.str.startswith("Interior") & (ow.te >= t_start - pd.Timedelta(seconds=60))
                & (ow.te <= t_start + pd.Timedelta(seconds=600))]
        arrivals, wave = [], None
        if len(ow):
            w = ow.iloc[0]
            off = (w.te - t_start).total_seconds()
            ll = lleg[lleg.id_onda == w.id_onda]
            arrivals = [dict(id=x.id_estacion, km=x.km, s=round(off + x.segundos_desde_emision, 2))
                        for x in ll.itertuples()]
            wave = dict(km=clean(w.km_origen), t_emit=round(off, 2), origen=w.origen)
        # twin residual: 10-min mean after the balance confirmation
        firma = None
        if first is not None and isinstance(first.fecha_hora_confirmacion, str):
            tc = pd.Timestamp(first.fecha_hora_confirmacion).floor("min")
            rr = res_w.loc[tc: tc + pd.Timedelta(minutes=9)].mean()
            if wave is not None:  # baseline before the leak, as in detection.py
                rr = rr - res_w.loc[t_start - pd.Timedelta(minutes=65): t_start - pd.Timedelta(minutes=6)].mean()
            firma = r(rr.values, 3)
        leaks.append(dict(
            id=f.id_fuga, tipo=f.tipo, km=f.km, tam=f.tamano_pct, dur=f.duracion_min, vol=f.volumen_perdido_m3,
            inicio=(t_start - start).total_seconds() / 60, fin=(pd.Timestamp(f.fin) - start).total_seconds() / 60,
            metodo=sc.metodo, estado=sc.estado, t_alarma=clean(sc.t_alarma_min), t_conf=clean(sc.t_confirmacion_min),
            km_onda=clean(sc.km_onda), km_gemelo=clean(sc.km_gemelo), km_final=clean(sc.km_final),
            err=clean(sc.error_final_km), tam_est=clean(sc.tamano_est_pct),
            km_lo=clean(first.km_rango_desde) if first is not None else None,
            km_hi=clean(first.km_rango_hasta) if first is not None else None,
            motivo=first.motivo if first is not None else "",
            zoom=dict(s=[int(x) for x in zr.index], p=[r(zr[s], 3) for s in sids]),
            arrivals=arrivals, wave=wave, firma=firma,
        ))

    # tables
    al = alarmas.copy()
    al["t"] = minutes(al.fecha_hora_alarma, start)
    al_cols = ["id_alarma", "t", "tipo", "estado", "metodo", "motivo", "km_estimado", "km_rango_desde",
               "km_rango_hasta", "tamano_est_pct", "n_estaciones", "amplitud_bar", "id_fuga_real", "km_real",
               "error_km", "tiempo_alarma_min", "es_falsa_alarma"]
    eventos["t"] = minutes(eventos.fecha_hora, start)
    env["t"] = minutes(env.inicio, start)
    env["t_fin"] = minutes(env.fin, start)
    inst["t"] = minutes(inst.inicio, start)
    for c in ("lanzamiento", "llegada_estimada", "llegada_real"):
        bat[c + "_t"] = minutes(bat[c], start)

    # Monte Carlo
    mcg = (mc.groupby(["metodo", "tipo_fuga", "tamano_pct"])
           .apply(lambda x: pd.Series(dict(
               n=len(x), pdet=x.detectada.mean() * 100,
               t_med=x.loc[x.detectada == 1, "tiempo_deteccion_min"].median(),
               err_med=x.error_ubicacion_km.median(),
               err_p90=x.error_ubicacion_km.quantile(0.9))), include_groups=False)
           .reset_index())
    mdl = []
    for (m, k), x in mcg.groupby(["metodo", "tipo_fuga"]):
        ok = x[x.pdet >= 90].tamano_pct
        mdl.append(dict(metodo=m, tipo=k, min_pct=float(ok.min()) if len(ok) else None))

    data = dict(
        start=start.strftime("%Y-%m-%dT%H:%M:%S"), cfg=CFG,
        stations=[dict(id=x.id_estacion, name=x.nombre, km=x.km, elev=x.elevacion_m, maop=x.maop_bar,
                       alerta=x.alerta_maop_bar) for x in est.itertuples()],
        ts=ts, p=p, days=days, resid=resid, leaks=leaks,
        alarms=records(al[al_cols].sort_values("t")),
        events=records(eventos[["id_evento", "t", "tipo", "descripcion"]]),
        envelope=records(env[["id_alerta", "t", "t_fin", "tipo", "id_estacion", "km", "valor_pico", "detalle",
                              "duracion_min"]]),
        instruments=records(inst[["id_alerta", "t", "id_estacion", "tipo", "detalle", "duracion_min"]]),
        batches=records(bat[["id_batch", "lanzamiento_t", "duracion_min", "llegada_estimada_t", "llegada_real_t",
                             "tiempo_transito_h", "error_min"]]),
        profile=dict(km=r(perfil.km, 1), elev=r(perfil.elevacion_m, 1), p_slurry=r(perfil.presion_nominal_pulpa_bar, 1),
                     p_water=r(perfil.presion_nominal_agua_bar, 1), maop=r(perfil.maop_bar, 0)),
        mc=records(mcg.rename(columns={"tipo_fuga": "tipo", "tamano_pct": "tam"})), mdl=mdl,
        mc_free=dict(dias=float(free.horas.sum() / 24), falsas=int(free.falsas_alarmas.sum()),
                     confirmadas=int(free.detalle_falsas.fillna("").str.count(r"\(Confirmada\)").sum()),
                     rechazados=int(free.eventos_operacionales_rechazados.sum())),
        mc_n=int(mc.escenario.nunique()),
        comparison=records(comp),
    )
    return data


def render(data):
    """Returns (head, body): the template split at <!--HEAD-END-->, with the data embedded."""
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    tpl = TEMPLATE.read_text(encoding="utf-8")
    head, body = tpl.split("<!--HEAD-END-->", 1)
    return head.strip(), body.replace("__DATA__", payload).strip()


SKELETON = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
{head}
</head>
<body>
{body}
</body>
</html>
"""


def main(artifact_path=None):
    """Writes docs/index.html (GitHub Pages). Optionally also a skeleton-less copy for previews."""
    head, body = render(build())
    out = ROOT / "docs" / "index.html"
    out.parent.mkdir(exist_ok=True)
    out.write_text(SKELETON.format(head=head, body=body), encoding="utf-8")
    if artifact_path:
        Path(artifact_path).write_text(head + "\n" + body, encoding="utf-8")
    return out, out.stat().st_size


if __name__ == "__main__":
    path, n = main(sys.argv[1] if len(sys.argv) > 1 else None)
    print(f"Dashboard: {path} ({n / 1024:.0f} KB)")
