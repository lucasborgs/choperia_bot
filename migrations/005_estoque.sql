-- ============================================================
-- Choperia Bot — Migration 005: Estoque e Categorias
-- Aplicar via: Supabase > SQL Editor > colar e executar
-- Feature: .specs/features/estoque-e-categorias
-- ============================================================

-- ------------------------------------------------------------
-- Itens de estoque (fonte da verdade de saldo e capital congelado)
-- Categoria = tipo/acondicionamento do bem comprado (barril, copo...),
-- não a matéria-prima interna. Lista fixa de 8 valores.
-- ------------------------------------------------------------
CREATE TABLE itens_estoque (
    id             UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    nome           TEXT          NOT NULL,
    unidade        TEXT          NOT NULL CHECK (unidade IN ('L','ml','kg','g','un')),
    qtd            DECIMAL(12,3) NOT NULL DEFAULT 0,
    custo_unitario DECIMAL(10,2) NOT NULL DEFAULT 0 CHECK (custo_unitario >= 0),
    categoria      TEXT          NOT NULL CHECK (categoria IN
                                  ('malte','lúpulo','embalagem pet','copo','barril','garrafa','petiscos','gelo')),
    criado_em      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    atualizado_em  TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_itens_estoque_nome_unico ON itens_estoque (lower(nome));
CREATE INDEX idx_itens_estoque_categoria ON itens_estoque (categoria);

-- ------------------------------------------------------------
-- Mapeamento produto (cardápio) -> item de estoque
-- Declara quanto de um insumo cada unidade vendida consome.
-- Ex: "Chopp 300" consome 0.3 L de "Barril Pilsen".
-- ------------------------------------------------------------
CREATE TABLE mapeamento_produto_estoque (
    id                  UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    produto_nome        TEXT          NOT NULL,
    item_estoque_id     UUID          NOT NULL REFERENCES itens_estoque(id) ON DELETE CASCADE,
    consumo_por_unidade DECIMAL(10,3) NOT NULL CHECK (consumo_por_unidade > 0),
    unidade             TEXT          NOT NULL,
    atualizado_em       TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_mpe_produto ON mapeamento_produto_estoque (lower(produto_nome));
CREATE INDEX idx_mpe_item ON mapeamento_produto_estoque (item_estoque_id);

-- ------------------------------------------------------------
-- Lotes de estoque (ciclo compra -> consumo -> fechamento)
-- qtd_ajustes = soma signed dos deltas de atualizar_estoque.
-- Fórmula de perda (ao fechar):
--   perda = qtd_comprada + qtd_ajustes - qtd_vendida - qtd_atual
-- ------------------------------------------------------------
CREATE TABLE lotes_estoque (
    id              UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    item_estoque_id UUID          NOT NULL REFERENCES itens_estoque(id) ON DELETE CASCADE,
    abertura        TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    fechamento      TIMESTAMPTZ,
    qtd_comprada    DECIMAL(12,3) NOT NULL DEFAULT 0,
    qtd_vendida     DECIMAL(12,3) NOT NULL DEFAULT 0,
    qtd_ajustes     DECIMAL(12,3) NOT NULL DEFAULT 0,
    qtd_restante    DECIMAL(12,3),
    perda_qtd       DECIMAL(12,3),
    perda_pct       DECIMAL(5,2)
);

CREATE INDEX idx_lotes_item ON lotes_estoque (item_estoque_id, fechamento);
CREATE UNIQUE INDEX idx_lotes_aberto_unico
    ON lotes_estoque (item_estoque_id) WHERE fechamento IS NULL;

-- ------------------------------------------------------------
-- Fechamentos do dia operacional (idempotência da baixa consolidada)
-- Primeira invocação de relatorio_dia no dia cria a linha e dispara baixa.
-- Invocações seguintes leem o snapshot.
-- ------------------------------------------------------------
CREATE TABLE fechamentos_dia (
    data_venda        DATE          PRIMARY KEY,
    fechado_em        TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    total_vendido     DECIMAL(10,2),
    total_recebido    DECIMAL(10,2),
    total_entradas    DECIMAL(10,2),
    produtos_sem_mapa JSONB
);

-- ------------------------------------------------------------
-- Liga entradas a itens de estoque (FK opcional)
-- NULL quando a entrada não bateu com nenhum item cadastrado.
-- ON DELETE SET NULL preserva histórico contábil.
-- ------------------------------------------------------------
ALTER TABLE entradas
    ADD COLUMN IF NOT EXISTS item_estoque_id UUID
    REFERENCES itens_estoque(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_entradas_item_estoque ON entradas (item_estoque_id);
