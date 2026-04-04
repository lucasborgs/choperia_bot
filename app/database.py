from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

import json

import asyncpg

from app.config import settings

_BRT = ZoneInfo("America/Sao_Paulo")


def _hoje() -> date:
    """Retorna o dia operacional (06:00–05:59). Antes das 6h conta como dia anterior."""
    agora = datetime.now(_BRT)
    if agora.hour < 6:
        return (agora - timedelta(days=1)).date()
    return agora.date()

_pool: asyncpg.Pool | None = None


# ------------------------------------------------------------------
# Ciclo de vida
# ------------------------------------------------------------------

async def _init_connection(conn: asyncpg.Connection) -> None:
    """Configura cada conexão do pool para usar fuso horário do Brasil."""
    await conn.execute("SET timezone = 'America/Sao_Paulo'")


async def init_db() -> None:
    global _pool
    _pool = await asyncpg.create_pool(
        dsn=settings.DATABASE_URL,
        min_size=1,
        max_size=5,
        statement_cache_size=0,
        init=_init_connection,
    )


async def close_db() -> None:
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("Pool não inicializado. Chame init_db() primeiro.")
    return _pool


# ------------------------------------------------------------------
# produtos_dia
# ------------------------------------------------------------------

async def limpar_e_inserir_cardapio(
    itens: list[dict],  # [{"produto": str, "preco": float}]
) -> list[asyncpg.Record]:
    """Adiciona/atualiza itens no cardápio do dia (não remove os existentes)."""
    hoje = _hoje()
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = []
            for item in itens:
                nome = item["produto"].strip()
                preco = float(item["preco"])
                # Remove versão anterior (case-insensitive) e insere nova
                await conn.execute(
                    "DELETE FROM produtos_dia WHERE data_venda = $1 AND lower(nome) = lower($2)",
                    hoje, nome,
                )
                row = await conn.fetchrow(
                    "INSERT INTO produtos_dia (nome, preco, data_venda) VALUES ($1, $2, $3) RETURNING nome, preco",
                    nome, preco, hoje,
                )
                rows.append(row)
    return rows


async def buscar_cardapio_hoje() -> list[asyncpg.Record]:
    pool = get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(
            "SELECT nome, preco FROM produtos_dia WHERE data_venda = $1 ORDER BY nome",
            _hoje(),
        )


async def buscar_preco_produto(nome: str) -> Decimal | None:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT preco FROM produtos_dia WHERE data_venda = $1 AND lower(nome) = lower($2)",
            _hoje(),
            nome,
        )
    return row["preco"] if row else None


async def remover_produto_cardapio(nome: str) -> bool:
    """Remove um produto do cardápio de hoje. Retorna True se encontrou e removeu."""
    pool = get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM produtos_dia WHERE data_venda = $1 AND lower(nome) = lower($2)",
            _hoje(),
            nome,
        )
    return result != "DELETE 0"


# ------------------------------------------------------------------
# comandas
# ------------------------------------------------------------------

async def buscar_comandas_abertas_por_nome(nome: str) -> list[asyncpg.Record]:
    """Retorna todas as comandas abertas cujo nome contém a string buscada."""
    pool = get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT id, nome_cliente, data_criacao
            FROM comandas
            WHERE status = 'aberta' AND lower(nome_cliente) ILIKE lower($1)
            ORDER BY data_criacao
            """,
            f"%{nome}%",
        )


async def criar_comanda(nome_cliente: str) -> UUID:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "INSERT INTO comandas (nome_cliente) VALUES ($1) RETURNING id",
            nome_cliente,
        )
    return row["id"]


async def buscar_ou_criar_comanda(nome_cliente: str) -> UUID:
    """Retorna o id da comanda aberta exata (match exato de nome) ou cria uma nova."""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id FROM comandas WHERE status = 'aberta' AND lower(nome_cliente) = lower($1)",
            nome_cliente,
        )
        if row:
            return row["id"]
        # Cria nova comanda (funciona mesmo se já existe uma 'paga' com o mesmo nome)
        new_row = await conn.fetchrow(
            "INSERT INTO comandas (nome_cliente) VALUES ($1) RETURNING id",
            nome_cliente,
        )
        return new_row["id"]


async def renomear_cliente(comanda_id: UUID, novo_nome: str) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE comandas SET nome_cliente = $1 WHERE id = $2",
            novo_nome,
            comanda_id,
        )


async def fechar_comanda(comanda_id: UUID) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE comandas
            SET status = 'paga', data_fechamento = NOW()
            WHERE id = $1
            """,
            comanda_id,
        )


# ------------------------------------------------------------------
# itens_comanda
# ------------------------------------------------------------------

async def inserir_itens(
    comanda_id: UUID,
    itens: list[dict],  # [{"produto": str, "quantidade": int, "valor_unitario": Decimal}]
) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.executemany(
            """
            INSERT INTO itens_comanda (comanda_id, produto_nome, quantidade, valor_unitario, valor_total)
            VALUES ($1, $2, $3, $4, $5)
            """,
            [
                (
                    comanda_id,
                    i["produto"],
                    i["quantidade"],
                    i["valor_unitario"],
                    i["quantidade"] * i["valor_unitario"],
                )
                for i in itens
            ],
        )


async def remover_item(comanda_id: UUID, produto_nome: str, quantidade: int) -> bool:
    """
    Remove 'quantidade' unidades de um produto da comanda.
    Deleta o item se quantidade restante <= 0.
    Retorna True se o item foi encontrado.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT id, quantidade FROM itens_comanda
                WHERE comanda_id = $1 AND lower(produto_nome) = lower($2)
                ORDER BY criado_em DESC
                LIMIT 1
                """,
                comanda_id,
                produto_nome,
            )
            if not row:
                return False

            nova_qtd = row["quantidade"] - quantidade
            if nova_qtd <= 0:
                await conn.execute("DELETE FROM itens_comanda WHERE id = $1", row["id"])
            else:
                await conn.execute(
                    """
                    UPDATE itens_comanda
                    SET quantidade = $1, valor_total = $1 * valor_unitario
                    WHERE id = $2
                    """,
                    nova_qtd,
                    row["id"],
                )
    return True


async def buscar_itens_comanda(comanda_id: UUID) -> list[asyncpg.Record]:
    pool = get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT produto_nome, SUM(quantidade) AS quantidade, valor_unitario,
                   SUM(valor_total) AS valor_total
            FROM itens_comanda
            WHERE comanda_id = $1
            GROUP BY produto_nome, valor_unitario
            ORDER BY produto_nome
            """,
            comanda_id,
        )


# ------------------------------------------------------------------
# pagamentos
# ------------------------------------------------------------------

async def registrar_pagamento(comanda_id: UUID, valor: Decimal) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO pagamentos (comanda_id, valor) VALUES ($1, $2)",
            comanda_id,
            valor,
        )


async def registrar_pagamento_e_fechar(comanda_id: UUID, valor: Decimal) -> Decimal:
    """Registra pagamento e fecha a comanda se quitada. Retorna o novo saldo devedor."""
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO pagamentos (comanda_id, valor) VALUES ($1, $2)",
                comanda_id, valor,
            )
            row = await conn.fetchrow(
                """
                SELECT
                    COALESCE(i.total, 0) - COALESCE(p.total, 0) AS saldo
                FROM comandas c
                LEFT JOIN (
                    SELECT comanda_id, SUM(valor_total) AS total
                    FROM itens_comanda WHERE comanda_id = $1
                    GROUP BY comanda_id
                ) i ON i.comanda_id = c.id
                LEFT JOIN (
                    SELECT comanda_id, SUM(valor) AS total
                    FROM pagamentos WHERE comanda_id = $1
                    GROUP BY comanda_id
                ) p ON p.comanda_id = c.id
                WHERE c.id = $1
                """,
                comanda_id,
            )
            novo_saldo = row["saldo"]
            if novo_saldo <= 0:
                await conn.execute(
                    "UPDATE comandas SET status = 'paga', data_fechamento = NOW() WHERE id = $1",
                    comanda_id,
                )
    return novo_saldo


# ------------------------------------------------------------------
# v_saldo_comandas (view)
# ------------------------------------------------------------------

async def buscar_saldo(comanda_id: UUID) -> asyncpg.Record | None:
    pool = get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT * FROM v_saldo_comandas WHERE id = $1",
            comanda_id,
        )


async def listar_comandas_abertas() -> list[asyncpg.Record]:
    pool = get_pool()
    async with pool.acquire() as conn:
        return await conn.fetch(
            """
            SELECT nome_cliente, total_consumido, total_pago, saldo_devedor, data_criacao
            FROM v_saldo_comandas
            WHERE status = 'aberta'
            ORDER BY data_criacao
            """
        )


# ------------------------------------------------------------------
# entradas
# ------------------------------------------------------------------

async def inserir_entradas(
    itens: list[dict],
    fornecedor: str | None,
) -> list[dict]:
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = []
            for item in itens:
                qtd = float(item["quantidade"])
                preco = float(item["preco_unitario"])
                litros = float(item["litros"]) if item.get("litros") else None
                row = await conn.fetchrow(
                    """
                    INSERT INTO entradas
                        (produto_nome, unidade, quantidade, litros, valor_unitario, valor_total, fornecedor)
                    VALUES ($1, $2, $3, $4, $5, $6, $7)
                    RETURNING produto_nome, unidade, quantidade, litros, valor_unitario, valor_total, fornecedor
                    """,
                    item["produto"], item["unidade"], qtd, litros,
                    preco, qtd * preco, fornecedor,
                )
                rows.append(dict(row))
    return rows


async def remover_ultima_entrada(produto: str | None = None) -> dict | None:
    """Remove a entrada mais recente (opcionalmente filtrada por produto). Retorna a entrada removida."""
    pool = get_pool()
    async with pool.acquire() as conn:
        if produto:
            row = await conn.fetchrow(
                """
                DELETE FROM entradas
                WHERE id = (
                    SELECT id FROM entradas
                    WHERE lower(produto_nome) = lower($1)
                    ORDER BY criado_em DESC LIMIT 1
                )
                RETURNING produto_nome, unidade, quantidade, litros, valor_unitario, valor_total, fornecedor
                """,
                produto,
            )
        else:
            row = await conn.fetchrow(
                """
                DELETE FROM entradas
                WHERE id = (
                    SELECT id FROM entradas ORDER BY criado_em DESC LIMIT 1
                )
                RETURNING produto_nome, unidade, quantidade, litros, valor_unitario, valor_total, fornecedor
                """,
            )
    return dict(row) if row else None


# ------------------------------------------------------------------
# configuracao_produto
# ------------------------------------------------------------------

async def buscar_configuracao_produto(nome: str) -> dict | None:
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT nome, perda_pct FROM configuracao_produto WHERE lower(nome) = lower($1)",
            nome,
        )
    return dict(row) if row else None


async def upsert_configuracao_produto(nome: str, perda_pct: float) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO configuracao_produto (nome, perda_pct)
            VALUES ($1, $2)
            ON CONFLICT (nome) DO UPDATE SET perda_pct = $2, atualizado_em = NOW()
            """,
            nome, perda_pct,
        )


# ------------------------------------------------------------------
# Dashboard
# ------------------------------------------------------------------

async def buscar_entradas_dashboard(de: date, ate: date) -> list[dict]:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT produto_nome, unidade, quantidade, litros,
                   valor_unitario, valor_total, fornecedor, criado_em
            FROM entradas
            WHERE criado_em::date BETWEEN $1 AND $2
            ORDER BY criado_em DESC
            """,
            de, ate,
        )
    return [dict(r) for r in rows]


async def buscar_saidas_dashboard(de: date, ate: date) -> list[dict]:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT i.produto_nome, i.quantidade, i.valor_unitario,
                   i.valor_total, c.nome_cliente, i.criado_em
            FROM itens_comanda i
            JOIN comandas c ON c.id = i.comanda_id
            WHERE i.criado_em::date BETWEEN $1 AND $2
            ORDER BY i.criado_em DESC
            """,
            de, ate,
        )
    return [dict(r) for r in rows]


async def buscar_estoque_resumo() -> list[dict]:
    """Agrega entradas vs saídas por produto, calculando doses para chopps."""
    _ML_POR_DOSE = 400
    pool = get_pool()
    async with pool.acquire() as conn:
        entradas = await conn.fetch(
            """
            SELECT produto_nome,
                   SUM(quantidade)                          AS qtd_comprada,
                   SUM(COALESCE(litros * quantidade, 0))    AS litros_comprados,
                   SUM(valor_total)                         AS custo_total
            FROM entradas
            GROUP BY produto_nome
            ORDER BY produto_nome
            """
        )
        saidas = await conn.fetch(
            """
            SELECT produto_nome,
                   SUM(quantidade)   AS qtd_vendida,
                   SUM(valor_total)  AS receita_total
            FROM itens_comanda
            GROUP BY produto_nome
            """
        )
        configs = await conn.fetch(
            "SELECT nome, perda_pct FROM configuracao_produto"
        )

    configs_map = {r["nome"].lower(): float(r["perda_pct"]) for r in configs}
    saidas_map = {r["produto_nome"].lower(): dict(r) for r in saidas}

    resultado = []
    for e in entradas:
        nome = e["produto_nome"]
        s = saidas_map.get(nome.lower(), {})
        litros = float(e["litros_comprados"] or 0)
        perda = configs_map.get(nome.lower(), 10.0)
        fator = 1 - perda / 100

        doses_compradas = int(litros * 1000 / _ML_POR_DOSE * fator) if litros > 0 else None
        doses_vendidas = int(float(s.get("qtd_vendida", 0))) if litros > 0 else None

        resultado.append({
            "produto": nome,
            "qtd_comprada": float(e["qtd_comprada"]),
            "litros_comprados": litros,
            "custo_total": float(e["custo_total"]),
            "qtd_vendida": float(s.get("qtd_vendida", 0)),
            "receita_total": float(s.get("receita_total", 0)),
            "doses_compradas": doses_compradas,
            "doses_vendidas": doses_vendidas,
            "perda_pct": perda if litros > 0 else None,
        })
    return resultado


async def buscar_fluxo_caixa(de: date, ate: date) -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        custo = await conn.fetchval(
            "SELECT COALESCE(SUM(valor_total), 0) FROM entradas WHERE criado_em::date BETWEEN $1 AND $2",
            de, ate,
        )
        vendido = await conn.fetchval(
            """
            SELECT COALESCE(SUM(i.valor_total), 0)
            FROM itens_comanda i
            JOIN comandas c ON c.id = i.comanda_id
            WHERE c.data_criacao::date BETWEEN $1 AND $2
            """,
            de, ate,
        )
        recebido = await conn.fetchval(
            "SELECT COALESCE(SUM(valor), 0) FROM pagamentos WHERE criado_em::date BETWEEN $1 AND $2",
            de, ate,
        )
    return {
        "custo_total": float(custo),
        "vendido_total": float(vendido),
        "recebido_total": float(recebido),
        "lucro_bruto": float(vendido) - float(custo),
        "a_receber": float(vendido) - float(recebido),
    }


# ------------------------------------------------------------------
# Relatório do dia
# ------------------------------------------------------------------

async def relatorio_dia() -> dict:
    hoje = _hoje()
    pool = get_pool()
    async with pool.acquire() as conn:
        # Total por produto
        por_produto = await conn.fetch(
            """
            SELECT i.produto_nome,
                   SUM(i.quantidade)   AS quantidade_total,
                   SUM(i.valor_total)  AS receita
            FROM itens_comanda i
            JOIN comandas c ON c.id = i.comanda_id
            WHERE c.data_criacao::date = $1
            GROUP BY i.produto_nome
            ORDER BY receita DESC
            """,
            hoje,
        )

        # Totais gerais (subqueries para evitar multiplicação por JOIN)
        totais = await conn.fetchrow(
            """
            SELECT
                COUNT(DISTINCT c.id) AS total_comandas,
                COALESCE(SUM(i_sum.total), 0) AS total_vendido,
                COALESCE(SUM(p_sum.total), 0) AS total_recebido
            FROM comandas c
            LEFT JOIN (
                SELECT comanda_id, SUM(valor_total) AS total
                FROM itens_comanda GROUP BY comanda_id
            ) i_sum ON i_sum.comanda_id = c.id
            LEFT JOIN (
                SELECT comanda_id, SUM(valor) AS total
                FROM pagamentos GROUP BY comanda_id
            ) p_sum ON p_sum.comanda_id = c.id
            WHERE c.data_criacao::date = $1
            """,
            hoje,
        )

    return {
        "por_produto": [dict(r) for r in por_produto],
        "total_comandas": totais["total_comandas"],
        "total_vendido": totais["total_vendido"],
        "total_recebido": totais["total_recebido"],
        "total_pendente": totais["total_vendido"] - totais["total_recebido"],
    }
