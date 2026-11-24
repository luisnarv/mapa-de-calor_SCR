-- Matriz de recomendación de alcance: una fila por NIC de dbanalitica.recomendacion_aire_scr.
--
-- La llena cada día Modelos/Recomendacion Alcance/correr_matriz.py, que la reemplaza entera dentro
-- de una sola transacción: quien la consulta ve la de ayer o la de hoy, nunca media. El backend
-- solo la lee (/api/v1/matriz).
--
-- Un NIC que no se recomienda queda igual en la tabla, con `motivo_exclusion` y las cifras en nulo:
-- así el servicio puede decir por qué, en vez de responder que no existe.

CREATE TABLE IF NOT EXISTS dbanalitica.matriz_alcance (
    nic                            text PRIMARY KEY,
    -- Último cierre de historico_mo que conocía el modelo: los "días sin orden" se miden contra esto.
    calculado_al                   date        NOT NULL,
    generado_en                    timestamptz NOT NULL DEFAULT now(),
    motivo_exclusion               text,

    territorio                     text,
    zona                           text,
    municipio                      text,
    barrio                         text,

    -- Con la Brigada Pesada y un técnico típico: sirve para priorizar NICs, no para elegir brigada.
    probabilidad_efectividad       numeric(4, 1),
    pronostico                     text,
    alcance_recomendado            text,
    pct_recomendado                numeric(4, 1),
    segundo_alcance                text,
    pct_segundo                    numeric(4, 1),
    ventaja_pp                     numeric(4, 1),
    ventaja_sobre_pesada_pp        numeric(4, 1),
    -- {"Brigada Pesada": 63.2, ...}: en JSON para que una canasta nueva no obligue a migrar.
    pct_por_alcance                jsonb,

    prob_pago_con_intervencion     numeric(4, 1),
    prob_no_pago_con_intervencion  numeric(4, 1),
    prob_pago_sin_intervencion     numeric(4, 1),
    -- El servicio de propensión no conocía el NIC: se le puso la mediana.
    sin_propension                 boolean,

    deuda_ultima_suspension        numeric,
    canasta_ultima_orden           text,
    resultado_ultima_orden         text,
    dias_sin_orden                 integer,
    ordenes_previas                integer,

    CONSTRAINT matriz_alcance_excluido_sin_cifras
        CHECK (motivo_exclusion IS NULL OR probabilidad_efectividad IS NULL),
    CONSTRAINT matriz_alcance_recomendado_con_cifras
        CHECK (motivo_exclusion IS NOT NULL OR probabilidad_efectividad IS NOT NULL)
);
