-- ============================================================
-- Choperia Bot — Dados sintéticos para teste do dashboard
-- ⚠️  TEMPORÁRIO — apague com: DELETE FROM ... (ver final do arquivo)
-- Aplicar via: Supabase > SQL Editor > colar e executar
-- ============================================================

-- ------------------------------------------------------------
-- Configuração de produtos (chopp)
-- ------------------------------------------------------------
INSERT INTO configuracao_produto (nome, perda_pct) VALUES
  ('IPA',    8.0),
  ('Pilsen', 10.0),
  ('Weiss',  12.0)
ON CONFLICT (nome) DO UPDATE SET perda_pct = EXCLUDED.perda_pct, atualizado_em = NOW();

-- ------------------------------------------------------------
-- Entradas (compras de insumos) — últimos 30 dias
-- ------------------------------------------------------------
INSERT INTO entradas (produto_nome, unidade, quantidade, litros, valor_unitario, valor_total, fornecedor, criado_em) VALUES
  ('IPA',    'barril', 2, 50, 180.00, 360.00, 'Distribuidora Silva', NOW() - INTERVAL '25 days'),
  ('Pilsen', 'barril', 3, 50, 150.00, 450.00, 'Distribuidora Silva', NOW() - INTERVAL '20 days'),
  ('Weiss',  'barril', 1, 30, 130.00, 130.00, 'Cervejaria Artesanal', NOW() - INTERVAL '18 days'),
  ('Gelo',   'saco',  10, NULL, 18.00,  180.00, 'Gelo do Zé',          NOW() - INTERVAL '15 days'),
  ('IPA',    'barril', 1, 50, 180.00, 180.00, 'Distribuidora Silva', NOW() - INTERVAL '10 days'),
  ('Pilsen', 'barril', 2, 50, 150.00, 300.00, 'Distribuidora Silva', NOW() - INTERVAL '8 days'),
  ('Gelo',   'saco',   8, NULL, 18.00,  144.00, 'Gelo do Zé',          NOW() - INTERVAL '5 days'),
  ('Carvão', 'saco',   4, NULL, 22.00,   88.00, NULL,                  NOW() - INTERVAL '3 days'),
  ('Weiss',  'barril', 2, 30, 130.00, 260.00, 'Cervejaria Artesanal', NOW() - INTERVAL '1 day');

-- ------------------------------------------------------------
-- Cardápio (hoje)
-- ------------------------------------------------------------
INSERT INTO produtos_dia (nome, preco, data_venda) VALUES
  ('IPA',    14.00, CURRENT_DATE),
  ('Pilsen', 10.00, CURRENT_DATE),
  ('Weiss',  12.00, CURRENT_DATE),
  ('Gelo',    5.00, CURRENT_DATE)
ON CONFLICT DO NOTHING;

-- ------------------------------------------------------------
-- Comandas + itens + pagamentos
-- ------------------------------------------------------------
-- Comanda 1 — João (fechada, paga)
WITH c1 AS (
  INSERT INTO comandas (nome_cliente, status, data_criacao, data_fechamento)
  VALUES ('João', 'paga', NOW() - INTERVAL '20 days', NOW() - INTERVAL '19 days')
  RETURNING id
)
INSERT INTO itens_comanda (comanda_id, produto_nome, quantidade, valor_unitario, valor_total, criado_em)
SELECT id, 'IPA',    4, 14.00, 56.00, NOW() - INTERVAL '20 days' FROM c1 UNION ALL
SELECT id, 'Pilsen', 2, 10.00, 20.00, NOW() - INTERVAL '20 days' FROM c1;

WITH c1_pay AS (SELECT id FROM comandas WHERE nome_cliente = 'João' AND status = 'paga' ORDER BY data_criacao LIMIT 1)
INSERT INTO pagamentos (comanda_id, valor, criado_em)
SELECT id, 76.00, NOW() - INTERVAL '19 days' FROM c1_pay;

-- Comanda 2 — Maria (fechada, paga)
WITH c2 AS (
  INSERT INTO comandas (nome_cliente, status, data_criacao, data_fechamento)
  VALUES ('Maria', 'paga', NOW() - INTERVAL '15 days', NOW() - INTERVAL '14 days')
  RETURNING id
)
INSERT INTO itens_comanda (comanda_id, produto_nome, quantidade, valor_unitario, valor_total, criado_em)
SELECT id, 'Weiss',  3, 12.00, 36.00, NOW() - INTERVAL '15 days' FROM c2 UNION ALL
SELECT id, 'Pilsen', 5, 10.00, 50.00, NOW() - INTERVAL '15 days' FROM c2;

WITH c2_pay AS (SELECT id FROM comandas WHERE nome_cliente = 'Maria' AND status = 'paga' ORDER BY data_criacao LIMIT 1)
INSERT INTO pagamentos (comanda_id, valor, criado_em)
SELECT id, 86.00, NOW() - INTERVAL '14 days' FROM c2_pay;

-- Comanda 3 — Pedro (fechada, paga parcial)
WITH c3 AS (
  INSERT INTO comandas (nome_cliente, status, data_criacao, data_fechamento)
  VALUES ('Pedro', 'paga', NOW() - INTERVAL '10 days', NOW() - INTERVAL '9 days')
  RETURNING id
)
INSERT INTO itens_comanda (comanda_id, produto_nome, quantidade, valor_unitario, valor_total, criado_em)
SELECT id, 'IPA',    6, 14.00, 84.00, NOW() - INTERVAL '10 days' FROM c3 UNION ALL
SELECT id, 'Gelo',   2,  5.00, 10.00, NOW() - INTERVAL '10 days' FROM c3;

WITH c3_pay AS (SELECT id FROM comandas WHERE nome_cliente = 'Pedro' AND status = 'paga' ORDER BY data_criacao LIMIT 1)
INSERT INTO pagamentos (comanda_id, valor, criado_em)
SELECT id, 94.00, NOW() - INTERVAL '9 days' FROM c3_pay;

-- Comanda 4 — Ana (aberta hoje)
WITH c4 AS (
  INSERT INTO comandas (nome_cliente, status, data_criacao)
  VALUES ('Ana', 'aberta', NOW() - INTERVAL '2 hours')
  RETURNING id
)
INSERT INTO itens_comanda (comanda_id, produto_nome, quantidade, valor_unitario, valor_total, criado_em)
SELECT id, 'Pilsen', 3, 10.00, 30.00, NOW() - INTERVAL '2 hours' FROM c4 UNION ALL
SELECT id, 'IPA',    2, 14.00, 28.00, NOW() - INTERVAL '1 hour'  FROM c4;

WITH c4_pay AS (SELECT id FROM comandas WHERE nome_cliente = 'Ana' AND status = 'aberta' ORDER BY data_criacao LIMIT 1)
INSERT INTO pagamentos (comanda_id, valor, criado_em)
SELECT id, 20.00, NOW() - INTERVAL '30 minutes' FROM c4_pay;

-- Comanda 5 — Carlos (aberta hoje, sem pagamento)
WITH c5 AS (
  INSERT INTO comandas (nome_cliente, status, data_criacao)
  VALUES ('Carlos', 'aberta', NOW() - INTERVAL '1 hour')
  RETURNING id
)
INSERT INTO itens_comanda (comanda_id, produto_nome, quantidade, valor_unitario, valor_total, criado_em)
SELECT id, 'Weiss',  4, 12.00, 48.00, NOW() - INTERVAL '1 hour' FROM c5 UNION ALL
SELECT id, 'Pilsen', 2, 10.00, 20.00, NOW() - INTERVAL '45 minutes' FROM c5;

-- ============================================================
-- Para remover todos os dados de teste, execute:
-- ============================================================
-- DELETE FROM pagamentos WHERE criado_em > NOW() - INTERVAL '31 days';
-- DELETE FROM itens_comanda WHERE criado_em > NOW() - INTERVAL '31 days';
-- DELETE FROM comandas WHERE nome_cliente IN ('João','Maria','Pedro','Ana','Carlos');
-- DELETE FROM entradas WHERE criado_em > NOW() - INTERVAL '31 days';
-- DELETE FROM configuracao_produto WHERE nome IN ('IPA','Pilsen','Weiss');
-- DELETE FROM produtos_dia WHERE data_venda = CURRENT_DATE;
