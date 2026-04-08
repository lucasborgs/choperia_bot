-- Adiciona coluna de método de pagamento (crédito, débito, pix, dinheiro)
ALTER TABLE pagamentos ADD COLUMN IF NOT EXISTS metodo TEXT;
