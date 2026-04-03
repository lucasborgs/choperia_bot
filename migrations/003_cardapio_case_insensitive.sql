-- ============================================================
-- Migration 003: Case-insensitive constraints
-- "Pilsen"/"pilsen" e "Tito"/"tito" eram tratados como
-- entidades distintas. Esta migration corrige ambos.
-- ============================================================

-- 1. Cardápio: UNIQUE (nome, data_venda) → (lower(nome), data_venda)
ALTER TABLE produtos_dia DROP CONSTRAINT IF EXISTS produtos_dia_nome_data_venda_key;

CREATE UNIQUE INDEX IF NOT EXISTS idx_produtos_dia_nome_lower_data
    ON produtos_dia (lower(nome), data_venda);

-- 2. Comandas: UNIQUE (nome_cliente) WHERE aberta → (lower(nome_cliente)) WHERE aberta
DROP INDEX IF EXISTS idx_comandas_unica_aberta;

CREATE UNIQUE INDEX idx_comandas_unica_aberta
    ON comandas (lower(nome_cliente))
    WHERE status = 'aberta';
