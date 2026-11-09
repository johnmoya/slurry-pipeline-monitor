-- Vistas de apoyo (dialecto SQLite; en PostgreSQL cambiar date(x) por CAST(x AS DATE)).
-- Se cargan automáticamente en data/ducto_monitoreo.sqlite.

-- Resumen diario de operación: volumen, energía, intensidad energética y alertas
DROP VIEW IF EXISTS v_resumen_diario;
CREATE VIEW v_resumen_diario AS
WITH op AS (
    SELECT date(fecha_hora)                   AS fecha,
           SUM(caudal_entrada_m3h) / 60.0      AS volumen_bombeado_m3,
           SUM(solidos_t)                      AS solidos_t,
           SUM(energia_kwh)                    AS energia_kwh,
           SUM(emisiones_tco2e)                AS emisiones_tco2e,
           MIN(velocidad_ms)                   AS velocidad_min_ms,
           MAX(desbalance_60min_pct)           AS desbalance_60min_max_pct
    FROM fact_operacion_min
    GROUP BY date(fecha_hora)
),
al AS (
    SELECT date(fecha_hora_alarma) AS fecha,
           SUM(CASE WHEN tipo = 'Fuga' AND estado = 'Confirmada' THEN 1 ELSE 0 END) AS fugas_confirmadas,
           SUM(es_falsa_alarma)                                                     AS falsas_alarmas,
           SUM(CASE WHEN estado = 'Rechazada' THEN 1 ELSE 0 END)                    AS transitorios_rechazados
    FROM fact_alarmas
    GROUP BY date(fecha_hora_alarma)
)
SELECT op.fecha,
       ROUND(op.volumen_bombeado_m3, 0)                   AS volumen_bombeado_m3,
       ROUND(op.solidos_t, 0)                             AS solidos_t,
       ROUND(op.energia_kwh, 0)                           AS energia_kwh,
       ROUND(op.energia_kwh / NULLIF(op.solidos_t, 0), 2) AS kwh_por_t,
       ROUND(op.emisiones_tco2e, 2)                       AS emisiones_tco2e,
       ROUND(op.velocidad_min_ms, 2)                      AS velocidad_min_ms,
       COALESCE(al.fugas_confirmadas, 0)                  AS fugas_confirmadas,
       COALESCE(al.falsas_alarmas, 0)                     AS falsas_alarmas,
       COALESCE(al.transitorios_rechazados, 0)            AS transitorios_rechazados
FROM op
LEFT JOIN al ON al.fecha = op.fecha;

-- Desempeño del sistema de detección por método y tamaño de fuga (Monte Carlo)
DROP VIEW IF EXISTS v_desempeno_metodos;
CREATE VIEW v_desempeno_metodos AS
SELECT metodo,
       tipo_fuga,
       tamano_pct,
       COUNT(*)                                   AS escenarios,
       ROUND(AVG(detectada) * 100, 1)             AS prob_deteccion_pct,
       ROUND(AVG(tiempo_deteccion_min), 1)        AS tiempo_medio_min,
       ROUND(AVG(error_ubicacion_km), 2)          AS error_medio_km
FROM fact_mc_desempeno
GROUP BY metodo, tipo_fuga, tamano_pct;

-- Salud del gemelo digital: residuo medio absoluto por estación y día
DROP VIEW IF EXISTS v_residuo_gemelo;
CREATE VIEW v_residuo_gemelo AS
SELECT date(fecha_hora)                AS fecha,
       id_estacion,
       ROUND(AVG(ABS(residuo_bar)), 3) AS residuo_abs_medio_bar,
       ROUND(MAX(ABS(residuo_bar)), 3) AS residuo_abs_max_bar,
       SUM(1 - dato_valido)            AS minutos_sin_dato_valido
FROM fact_presion_min
GROUP BY date(fecha_hora), id_estacion;
