-- Dos ajustes sobre chat_interaccion.
--
-- 001 se deja como está: describe lo que se aplicó ese día. Una base nueva corre
-- los dos archivos en orden y llega al mismo sitio.

-- 1. Fuera `modelo` y `payload_generated`.
--
-- Consecuencia asumida: al revisar un caso viejo ya no se podrá distinguir «el
-- error sigue vivo» de «los datos de abajo cambiaron», porque no queda registro
-- de con qué payload se respondió.
ALTER TABLE dbanalitica.chat_interaccion
    DROP COLUMN IF EXISTS modelo,
    DROP COLUMN IF EXISTS payload_generated;

-- 2. `votado_en` -> `calificado_en`.
--
-- «Calificar» es el verbo que usa la interfaz, y el nombre viejo no decía que
-- esto guarda el instante del clic en el pulgar, distinto de `creado_en`, que es
-- cuándo respondió el asistente.
--
-- La restricción chat_interaccion_voto_fechado sigue el cambio sola: Postgres
-- reescribe su expresión al renombrar la columna.
ALTER TABLE dbanalitica.chat_interaccion
    RENAME COLUMN votado_en TO calificado_en;
