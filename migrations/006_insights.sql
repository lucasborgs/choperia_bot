-- ============================================================
-- Choperia Bot — Migration 006: Insights e Alertas
-- Aplicar via: Supabase > SQL Editor > colar e executar
-- Feature: .specs/features/insights-e-alertas
-- ============================================================

-- ------------------------------------------------------------
-- Metas mensais de receita por categoria de produto
-- Categoria é texto livre normalizado (lower + unaccent no handler).
-- Chave composta: (categoria, mes_referencia) — mes_referencia
-- é sempre date_trunc('month', ...), nunca usa corte operacional.
-- ------------------------------------------------------------
CREATE TABLE metas_mensais (
    categoria       TEXT          NOT NULL,
    mes_referencia  DATE          NOT NULL,
    valor_mensal    DECIMAL(10,2) NOT NULL CHECK (valor_mensal > 0),
    atualizada_em   TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    PRIMARY KEY (categoria, mes_referencia)
);

CREATE INDEX idx_metas_mensais_mes ON metas_mensais (mes_referencia);

-- ------------------------------------------------------------
-- Categoria persistente por produto (entre dias)
-- Não usa FK para produtos_dia porque o cardápio é re-criado
-- diariamente; a validação de existência é feita em runtime.
-- ------------------------------------------------------------
CREATE TABLE categoria_produto (
    produto_nome    TEXT        NOT NULL,
    categoria       TEXT        NOT NULL,
    atualizada_em   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_categoria_produto_nome ON categoria_produto (lower(produto_nome));
CREATE INDEX idx_categoria_produto_categoria  ON categoria_produto (categoria);
