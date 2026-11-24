-- Todas las columnas de dbanalitica.recomendacion_aire_scr más la matriz de alcance, por NIC.
--
-- Es una vista y no columnas copiadas en matriz_alcance a propósito: lo de recomendacion_aire_scr
-- (deuda, última orden, causal...) sale siempre al día, mientras que la matriz es la foto de la
-- corrida diaria. Si se copiara, la deuda o la última gestión quedarían con la foto de la madrugada.
--
-- Ojo: Postgres expande `v.*` al crear la vista. Una columna que se le agregue después a
-- recomendacion_aire_scr NO aparece sola: hay que volver a correr este archivo. Y como esta vista
-- depende de aquella, recrear recomendacion_aire_scr con DROP ... CASCADE borra esta también.
--
-- Territorio, zona y municipio vienen de recomendacion_aire_scr; los de la matriz no se repiten.
-- Un NIC nuevo en la vista que todavía no pasó por la corrida diaria sale con la matriz en nulo y
-- `calculado_al` nulo: se puntúa en la próxima corrida.

CREATE OR REPLACE VIEW dbanalitica.matriz_alcance_completa AS
SELECT
    v.*,
    m.calculado_al,
    m.generado_en,
    m.motivo_exclusion,
    m.barrio,
    m.probabilidad_efectividad,
    m.pronostico,
    m.alcance_recomendado,
    m.pct_recomendado,
    m.segundo_alcance,
    m.pct_segundo,
    m.ventaja_pp,
    m.ventaja_sobre_pesada_pp,
    m.pct_por_alcance,
    m.prob_pago_con_intervencion,
    m.prob_no_pago_con_intervencion,
    m.prob_pago_sin_intervencion,
    m.sin_propension,
    m.deuda_ultima_suspension,
    m.canasta_ultima_orden,
    m.resultado_ultima_orden,
    m.dias_sin_orden,
    m.ordenes_previas
FROM dbanalitica.recomendacion_aire_scr v
LEFT JOIN dbanalitica.matriz_alcance m ON m.nic = v.nic::text;
