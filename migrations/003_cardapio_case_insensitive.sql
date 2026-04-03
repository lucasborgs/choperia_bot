-- ============================================================
-- Migration 003: Cardápio case-insensitive
-- O UNIQUE constraint original (nome, data_venda) diferencia
-- maiúsculas de minúsculas. "Pilsen" e "pilsen" eram tratados
-- como produtos distintos. Esta migration corrige isso.
-- ============================================================

-- Remove o constraint antigo
ALTER TABLE produtos_dia DROP CONSTRAINT IF EXISTS produtos_dia_nome_data_venda_key;

-- Cria novo índice unique case-insensitive
CREATE UNIQUE INDEX IF NOT EXISTS idx_produtos_dia_nome_lower_data
    ON produtos_dia (lower(nome), data_venda);
