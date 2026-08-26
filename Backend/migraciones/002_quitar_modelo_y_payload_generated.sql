ALTER TABLE dbanalitica.chat_interaccion
    DROP COLUMN IF EXISTS modelo,
    DROP COLUMN IF EXISTS payload_generated;
