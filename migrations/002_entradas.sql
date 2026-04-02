-- ============================================================
-- Choperia Bot — Migration 002: Entradas e Configuração
-- Aplicar via: Supabase > SQL Editor > colar e executar
-- ============================================================

-- ------------------------------------------------------------
-- Configuração de chopps (conversão barril → doses de 400ml)
-- Apenas produtos vendidos em barris precisam de configuração.
-- Demais produtos: comprado e vendido na mesma unidade.
-- ------------------------------------------------------------
CREATE TABLE configuracao_produto (
    id            UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    nome          TEXT          NOT NULL UNIQUE,
    perda_pct     DECIMAL       NOT NULL DEFAULT 10.0,  -- % de perda no processo
    atualizado_em TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

-- ------------------------------------------------------------
-- Entradas (compras de insumos)
-- ------------------------------------------------------------
CREATE TABLE entradas (
    id             UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    produto_nome   TEXT          NOT NULL,
    unidade        TEXT          NOT NULL,              -- ex: "barril", "saco", "caixa"
    quantidade     DECIMAL       NOT NULL CHECK (quantidade > 0),
    litros         DECIMAL,                             -- capacidade em litros (só para barris)
    valor_unitario DECIMAL(10,2) NOT NULL CHECK (valor_unitario > 0),
    valor_total    DECIMAL(10,2) NOT NULL,
    fornecedor     TEXT,
    criado_em      TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_entradas_data    ON entradas (criado_em);
CREATE INDEX idx_entradas_produto ON entradas (produto_nome);
