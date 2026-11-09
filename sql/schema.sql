-- Esquema generado por src/export_bi.py (ANSI SQL / PostgreSQL).
-- Las mismas tablas están cargadas en data/ducto_monitoreo.sqlite.

CREATE TABLE dim_estacion (
    id_estacion VARCHAR(200),
    nombre VARCHAR(200),
    km DOUBLE PRECISION,
    elevacion_m DOUBLE PRECISION,
    maop_bar INTEGER,
    alerta_maop_bar DOUBLE PRECISION,
    orden INTEGER,
    PRIMARY KEY (id_estacion)
);

CREATE TABLE dim_perfil (
    km DOUBLE PRECISION,
    elevacion_m DOUBLE PRECISION,
    presion_nominal_pulpa_bar DOUBLE PRECISION,
    presion_nominal_agua_bar DOUBLE PRECISION,
    linea_gradiente_hidraulico_m DOUBLE PRECISION,
    maop_bar INTEGER,
    PRIMARY KEY (km)
);

CREATE TABLE dim_fecha (
    fecha DATE,
    anio INTEGER,
    mes INTEGER,
    dia INTEGER,
    dia_semana VARCHAR(200),
    dia_operacion INTEGER,
    periodo VARCHAR(200),
    PRIMARY KEY (fecha)
);

CREATE TABLE fact_presion_min (
    fecha_hora TIMESTAMP,
    fecha DATE,
    id_estacion VARCHAR(200),
    presion_bar DOUBLE PRECISION,
    presion_modelo_bar DOUBLE PRECISION,
    residuo_bar DOUBLE PRECISION,
    dato_valido INTEGER
);

CREATE TABLE fact_operacion_min (
    fecha_hora TIMESTAMP,
    fecha DATE,
    caudal_entrada_m3h DOUBLE PRECISION,
    caudal_salida_m3h DOUBLE PRECISION,
    desbalance_10min_pct DOUBLE PRECISION,
    umbral_10min_pct DOUBLE PRECISION,
    desbalance_60min_pct DOUBLE PRECISION,
    umbral_60min_pct DOUBLE PRECISION,
    densidad_entrada_kgm3 DOUBLE PRECISION,
    densidad_salida_kgm3 DOUBLE PRECISION,
    velocidad_ms DOUBLE PRECISION,
    velocidad_alerta_ms DOUBLE PRECISION,
    velocidad_deposicion_ms DOUBLE PRECISION,
    presion_min_linea_bar DOUBLE PRECISION,
    km_presion_min DOUBLE PRECISION,
    potencia_bombeo_kw DOUBLE PRECISION,
    energia_kwh DOUBLE PRECISION,
    solidos_t DOUBLE PRECISION,
    emisiones_tco2e DOUBLE PRECISION,
    fuga_real_m3h_simulacion DOUBLE PRECISION
);

CREATE TABLE fact_eventos_operacionales (
    id_evento VARCHAR(200),
    fecha_hora TIMESTAMP,
    tipo VARCHAR(200),
    descripcion VARCHAR(200),
    PRIMARY KEY (id_evento)
);

CREATE TABLE fact_fugas_reales (
    id_fuga VARCHAR(200),
    inicio TIMESTAMP,
    fin TIMESTAMP,
    tipo VARCHAR(200),
    km DOUBLE PRECISION,
    tamano_pct DOUBLE PRECISION,
    duracion_min DOUBLE PRECISION,
    volumen_perdido_m3 DOUBLE PRECISION,
    PRIMARY KEY (id_fuga)
);

CREATE TABLE fact_alarmas (
    id_alarma VARCHAR(200),
    fecha_hora_alarma TIMESTAMP,
    fecha_hora_confirmacion TIMESTAMP,
    fecha_hora_fin TIMESTAMP,
    fecha DATE,
    tipo VARCHAR(200),
    estado VARCHAR(200),
    metodo VARCHAR(200),
    motivo VARCHAR(200),
    origen_onda VARCHAR(200),
    km_onda DOUBLE PRECISION,
    km_gemelo DOUBLE PRECISION,
    km_estimado DOUBLE PRECISION,
    km_rango_desde DOUBLE PRECISION,
    km_rango_hasta DOUBLE PRECISION,
    tamano_est_pct DOUBLE PRECISION,
    n_estaciones DOUBLE PRECISION,
    amplitud_bar DOUBLE PRECISION,
    id_fuga_real VARCHAR(200),
    km_real DOUBLE PRECISION,
    error_km DOUBLE PRECISION,
    tiempo_alarma_min DOUBLE PRECISION,
    tiempo_confirmacion_min DOUBLE PRECISION,
    es_falsa_alarma INTEGER,
    PRIMARY KEY (id_alarma)
);

CREATE TABLE fact_alertas_envolvente (
    id_alerta VARCHAR(200),
    inicio TIMESTAMP,
    fin TIMESTAMP,
    tipo VARCHAR(200),
    id_estacion VARCHAR(200),
    km DOUBLE PRECISION,
    valor_pico DOUBLE PRECISION,
    detalle VARCHAR(200),
    duracion_min DOUBLE PRECISION,
    PRIMARY KEY (id_alerta)
);

CREATE TABLE fact_alertas_instrumentos (
    id_alerta VARCHAR(200),
    inicio TIMESTAMP,
    fin TIMESTAMP,
    id_estacion VARCHAR(200),
    tipo VARCHAR(200),
    detalle VARCHAR(200),
    duracion_min DOUBLE PRECISION,
    PRIMARY KEY (id_alerta)
);

CREATE TABLE fact_batches (
    id_batch VARCHAR(200),
    lanzamiento TIMESTAMP,
    fin_lanzamiento TIMESTAMP,
    duracion_min DOUBLE PRECISION,
    llegada_estimada TIMESTAMP,
    llegada_real TIMESTAMP,
    tiempo_transito_h DOUBLE PRECISION,
    error_min DOUBLE PRECISION,
    PRIMARY KEY (id_batch)
);

CREATE TABLE fact_ondas (
    id_onda VARCHAR(200),
    fecha_hora_emision TIMESTAMP,
    origen VARCHAR(200),
    km_origen DOUBLE PRECISION,
    n_estaciones INTEGER,
    amplitud_max_bar DOUBLE PRECISION,
    PRIMARY KEY (id_onda)
);

CREATE TABLE fact_llegadas_onda (
    id_onda VARCHAR(200),
    id_estacion VARCHAR(200),
    km DOUBLE PRECISION,
    segundos_desde_emision DOUBLE PRECISION,
    amplitud_bar DOUBLE PRECISION
);

CREATE TABLE fact_presion_seg_fugas (
    id_fuga_real VARCHAR(200),
    fecha_hora TIMESTAMP,
    segundos_rel INTEGER,
    id_estacion VARCHAR(200),
    presion_bar DOUBLE PRECISION
);

CREATE TABLE fact_scorecard_fugas (
    fuga VARCHAR(200),
    tipo VARCHAR(200),
    km_real DOUBLE PRECISION,
    tamano_real_pct DOUBLE PRECISION,
    detectada BOOLEAN,
    estado VARCHAR(200),
    metodo VARCHAR(200),
    km_onda DOUBLE PRECISION,
    km_gemelo DOUBLE PRECISION,
    km_final DOUBLE PRECISION,
    tamano_est_pct DOUBLE PRECISION,
    error_onda_km DOUBLE PRECISION,
    error_gemelo_km DOUBLE PRECISION,
    error_final_km DOUBLE PRECISION,
    tamano_real_en_alarma_pct DOUBLE PRECISION,
    t_alarma_min DOUBLE PRECISION,
    t_confirmacion_min DOUBLE PRECISION
);

CREATE TABLE fact_mc_desempeno (
    escenario VARCHAR(200),
    tipo_fuga VARCHAR(200),
    tamano_pct DOUBLE PRECISION,
    km_real DOUBLE PRECISION,
    metodo VARCHAR(200),
    detectada INTEGER,
    tiempo_deteccion_min DOUBLE PRECISION,
    error_ubicacion_km DOUBLE PRECISION,
    falsas_alarmas INTEGER
);

CREATE TABLE fact_mc_sin_fuga (
    escenario VARCHAR(200),
    horas DOUBLE PRECISION,
    falsas_alarmas INTEGER,
    detalle_falsas VARCHAR(200),
    eventos_operacionales_rechazados INTEGER
);

CREATE TABLE fact_comparacion_balance (
    variante VARCHAR(200),
    falsas_alarmas INTEGER,
    dias DOUBLE PRECISION,
    por_dia DOUBLE PRECISION
);
