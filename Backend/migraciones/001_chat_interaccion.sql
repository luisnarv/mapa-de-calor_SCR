-- Feedback del chat: una fila por interacción (pregunta + respuesta), con la
-- traza que la produjo y el voto del usuario si lo hubo.
--
-- Va en `dbanalitica` y no en `public` porque usr_analitica no tiene CREATE en
-- public. Es el mismo esquema del que el ETL lee historico_mo.
--
-- Se aplica a mano. Sin Alembic mientras el esquema sea esta sola tabla.

CREATE TABLE IF NOT EXISTS dbanalitica.chat_interaccion (
    id                UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    conversacion_id   UUID        NOT NULL,
    n_interaccion     SMALLINT    NOT NULL CHECK (n_interaccion >= 1),

    pregunta          TEXT        NOT NULL,
    respuesta         TEXT        NOT NULL,
    vista             JSONB,
    traza             JSONB       NOT NULL DEFAULT '[]'::jsonb,
    modelo            TEXT        NOT NULL,
    payload_generated DATE,
    voto              TEXT        CHECK (voto IN ('util', 'inutil')),
    motivo            TEXT        CHECK (motivo IN (
                                      'dato_incorrecto',
                                      'no_entendio',
                                      'filtro_incorrecto',
                                      'mal_redactado'
                                  )),
    comentario        TEXT,
    creado_en         TIMESTAMPTZ NOT NULL DEFAULT now(),
    votado_en         TIMESTAMPTZ,

    CONSTRAINT chat_interaccion_posicion_unica
        UNIQUE (conversacion_id, n_interaccion),

    -- La fecha del voto existe si y solo si hay voto: al retirarlo se limpian
    -- los dos, y así «votado_en no nulo» siempre significa que hay opinión.
    CONSTRAINT chat_interaccion_voto_fechado
        CHECK ((voto IS NULL) = (votado_en IS NULL)),

    -- El motivo solo se pide en el pulgar abajo (el modal de la UI); un motivo
    -- colgado de un voto útil sería un dato que nadie sabría interpretar.
    CONSTRAINT chat_interaccion_motivo_solo_si_inutil
        CHECK (motivo IS NULL OR voto = 'inutil')
);

-- El triaje siempre pregunta por lo que salió mal, que es una fracción mínima
-- de las filas: el índice parcial se mantiene chico aunque la tabla crezca.
CREATE INDEX IF NOT EXISTS chat_interaccion_malas_idx
    ON dbanalitica.chat_interaccion (votado_en DESC, motivo)
    WHERE voto = 'inutil';

-- Para el corte por fechas del reporte semanal.
CREATE INDEX IF NOT EXISTS chat_interaccion_creado_idx
    ON dbanalitica.chat_interaccion (creado_en DESC);

COMMENT ON TABLE dbanalitica.chat_interaccion IS
    'Feedback del asistente del tablero SCR: una fila por pregunta/respuesta.';
