# Guía para armar el dashboard en Power BI

Tiempo estimado: 3 a 5 horas para las 4 páginas principales.

## 1. Cargar los datos

**Opción A, CSV (la más rápida):** Obtener datos → Carpeta → `data/powerbi/` → Combinar no; cargar cada archivo como tabla. En Power Query, revisar que las columnas `fecha_hora`, `inicio`, `fin`, `lanzamiento`, `llegada_*` queden como *Fecha/hora* y `fecha` como *Fecha* (configuración regional: Inglés (Estados Unidos) al cambiar tipo, porque los decimales vienen con punto).

**Opción B, SQL:** cargar `sql/schema.sql` en PostgreSQL o SQL Server, importar los CSV y conectar Power BI a esa base. La base SQLite `data/ducto_monitoreo.sqlite` ya trae las tablas y tres vistas (`v_resumen_diario`, `v_desempeno_metodos`, `v_residuo_gemelo`) por si quieres conectarte vía ODBC.

## 2. Modelo (estrella)

| Dimensión (lado 1) | Hechos (lado muchos) | Columna |
|---|---|---|
| `dim_fecha` | `fact_operacion_min`, `fact_presion_min`, `fact_alarmas` | `fecha` |
| `dim_estacion` | `fact_presion_min`, `fact_alertas_envolvente`, `fact_alertas_instrumentos`, `fact_llegadas_onda`, `fact_presion_seg_fugas` | `id_estacion` |
| `fact_fugas_reales` | `fact_alarmas` (`id_fuga_real`), `fact_presion_seg_fugas` | `id_fuga` |
| `fact_ondas` | `fact_llegadas_onda` | `id_onda` |

`dim_perfil`, las tablas `fact_mc_*`, `fact_comparacion_balance`, `fact_batches` y `fact_scorecard_fugas` quedan sin relación.

Marcar `dim_fecha` como tabla de fechas. Filtro cruzado en una sola dirección. Ordenar `dim_estacion[nombre]` por `orden`.

Pegar las medidas de `medidas.dax` en una tabla `_Medidas`.

## 3. Páginas

### Página 1 · Resumen operacional
Pregunta que responde: *¿cómo está operando el ducto?*
- Tarjetas: Volumen Bombeado m3, Sólidos Transportados t, Fugas Confirmadas, Falsas Alarmas, Transitorios Rechazados, Estado del Balance.
- Línea: `caudal_entrada_m3h` y `caudal_salida_m3h` por `fecha_hora`.
- Línea: `presion_bar` por `fecha_hora`, leyenda `id_estacion` (sugerencia: segmentador de estación para no saturar).
- Tabla: `fact_eventos_operacionales` (bitácora del día).
- Segmentador de `dim_fecha[fecha]`; filtrar `periodo = Operación monitoreada` por defecto.

### Página 2 · Perfil hidráulico y envolvente
Pregunta: *¿estamos dentro de los límites de diseño?*
- Gráfico de líneas con `dim_perfil[km]` en el eje X: `elevacion_m` (eje propio en un visual separado), `presion_nominal_pulpa_bar`, `presion_nominal_agua_bar`, `maop_bar`. Evitar ejes Y dobles: elevación y presión en dos visuales apilados con el mismo eje X.
- Columnas: Presión Máxima bar y `alerta_maop_bar` por estación.
- Línea: `velocidad_ms` con línea constante en `velocidad_alerta_ms`.
- Tabla: `fact_alertas_envolvente`.

### Página 3 · Integridad: detección de fugas
Pregunta: *¿hay una fuga, dónde y qué tan grande?*
- Línea: `desbalance_60min_pct` y `umbral_60min_pct` (y los de 10 min) por `fecha_hora`.
- Tabla de alarmas: `fecha_hora_alarma`, `estado`, `metodo`, `km_estimado`, `tamano_est_pct`, `motivo`. Formato condicional por `estado` (Confirmada / No confirmada / Rechazada) con ícono, no solo color.
- Dispersión "dónde": eje X `km_estimado`, eje Y `fecha_hora_alarma`; segunda serie con `km_real` (validación de la simulación).
- Drill-through desde la tabla hacia la página 4 usando `id_fuga_real`.

### Página 4 · Detalle de fuga (drill-through)
Pregunta: *¿qué vio cada método?*
- Línea: `fact_presion_seg_fugas[presion_bar]` por `segundos_rel`, leyenda `id_estacion`. Muestra la onda llegando a cada transmisor.
- Dispersión: `fact_llegadas_onda[km]` vs `segundos_desde_emision` (la "V" que ubica la fuga).
- Tarjeta: medida `Resumen de la Alarma`.
- Tabla: `fact_scorecard_fugas` filtrada por la fuga.

### Página 5 · Desempeño del sistema de detección
Pregunta: *¿qué tan confiable es el sistema?* (estilo API RP 1130: sensibilidad, confiabilidad, exactitud, robustez)
- Líneas: Probabilidad de Detección % por `tamano_pct`, leyenda `metodo`; segmentador `tipo_fuga`.
- Líneas: Tiempo de Detección Mediana min y Error de Ubicación Mediana km por `tamano_pct`.
- Tarjetas: Fuga Mínima Detectable (90%) % por método; Falsas Alarmas por Día (sin fuga).
- Barras: `fact_comparacion_balance` (sin compensar vs compensado).

### Página 6 · Salud de instrumentos y gemelo digital (opcional)
- Matriz: filas `id_estacion`, columnas `fecha`, valor Residuo Absoluto Medio bar, formato condicional en escala de un solo color.
- Tarjeta: Disponibilidad de Transmisores %.
- Tablas: `fact_alertas_instrumentos` y `fact_batches` (llegada estimada vs real).

### Página 7 · Energía y emisiones (opcional)
- Columnas diarias: Energía MWh; línea: Intensidad Energética kWh/t.
- Tarjeta: Emisiones tCO2e. **El factor de emisión del código es referencial: reemplazarlo por el factor oficial vigente del sistema eléctrico antes de mostrar cifras.**

## 4. Buenas prácticas que conviene mostrar
- Todas las medidas en una tabla `_Medidas` con carpetas.
- Nada de columnas calculadas para lo que es una medida.
- Un solo eje Y por visual.
- Títulos de visual escritos como pregunta o conclusión ("El balance detectó las 4 fugas sin falsas alarmas").
- Una página "Acerca de" con supuestos y limitaciones (copiar del README).
