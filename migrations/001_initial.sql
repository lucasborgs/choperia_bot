-- ============================================================
-- Choperia Bot — Migration 001: Schema inicial
-- Aplicar via: Supabase > SQL Editor > colar e executar
-- ============================================================

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- ------------------------------------------------------------
-- Cardápio do dia
-- Limpo a cada evento via definir_cardapio (DELETE + INSERT)
-- ------------------------------------------------------------
CREATE TABLE produtos_dia (
    id           UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    nome         TEXT        NOT NULL,
    preco        DECIMAL(10,2) NOT NULL CHECK (preco > 0),
    data_venda   DATE        NOT NULL DEFAULT CURRENT_DATE,
    UNIQUE (nome, data_venda)
);

CREATE INDEX idx_produtos_dia_data ON produtos_dia (data_venda);

-- ------------------------------------------------------------
-- Comandas
-- Um cliente não pode ter duas comandas abertas ao mesmo tempo.
-- ------------------------------------------------------------
CREATE TABLE comandas (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    nome_cliente     TEXT        NOT NULL,
    status           TEXT        NOT NULL DEFAULT 'aberta'
                                 CHECK (status IN ('aberta', 'paga')),
    data_criacao     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    data_fechamento  TIMESTAMPTZ
);

CREATE INDEX idx_comandas_nome_status ON comandas (nome_cliente, status);
CREATE INDEX idx_comandas_data        ON comandas (data_criacao);

-- Garante no banco: sem duas comandas abertas para o mesmo cliente
CREATE UNIQUE INDEX idx_comandas_unica_aberta
    ON comandas (nome_cliente)
    WHERE status = 'aberta';

-- ------------------------------------------------------------
-- Itens das comandas
-- ------------------------------------------------------------
CREATE TABLE itens_comanda (
    id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    comanda_id      UUID        NOT NULL REFERENCES comandas(id) ON DELETE CASCADE,
    produto_nome    TEXT        NOT NULL,
    quantidade      INT         NOT NULL CHECK (quantidade > 0),
    valor_unitario  DECIMAL(10,2) NOT NULL CHECK (valor_unitario > 0),
    valor_total     DECIMAL(10,2) NOT NULL,
    criado_em       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_itens_comanda_id ON itens_comanda (comanda_id);

-- ------------------------------------------------------------
-- Pagamentos (parciais ou totais)
-- A comanda só é marcada 'paga' quando saldo_devedor = 0
-- ------------------------------------------------------------
CREATE TABLE pagamentos (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    comanda_id  UUID        NOT NULL REFERENCES comandas(id) ON DELETE CASCADE,
    valor       DECIMAL(10,2) NOT NULL CHECK (valor > 0),
    criado_em   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_pagamentos_comanda_id ON pagamentos (comanda_id);

-- ------------------------------------------------------------
-- View: saldo atual por comanda
-- Usada por consultar_total, listar_comandas e pagar_conta
-- ------------------------------------------------------------
CREATE VIEW v_saldo_comandas AS
SELECT
    c.id,
    c.nome_cliente,
    c.status,
    c.data_criacao,
    COALESCE(i.total_consumido, 0)                              AS total_consumido,
    COALESCE(p.total_pago, 0)                                   AS total_pago,
    COALESCE(i.total_consumido, 0) - COALESCE(p.total_pago, 0) AS saldo_devedor
FROM comandas c
LEFT JOIN (
    SELECT comanda_id, SUM(valor_total) AS total_consumido
    FROM itens_comanda
    GROUP BY comanda_id
) i ON i.comanda_id = c.id
LEFT JOIN (
    SELECT comanda_id, SUM(valor) AS total_pago
    FROM pagamentos
    GROUP BY comanda_id
) p ON p.comanda_id = c.id;
