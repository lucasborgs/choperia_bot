from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

import json

import asyncpg

from app.config import settings

_BRT = ZoneInfo("America/Sao_Paulo")

_ML_POR_DOSE = 400

_CONVERSOES_UNIDADE: dict[tuple[str, str], Decimal] = {
    ("L", "ml"): Decimal("1000"),
    ("ml", "L"): Decimal("0.001"),
    ("kg", "g"): Decimal("1000"),
    ("g", "kg"): Decimal("0.001"),
}


def _converter_unidade(qtd: Decimal, de: str, para: str) -> Decimal | None:
    """Converte qtd entre unidades compatíveis. Retorna None se incompatível."""
    if de == para:
        return qtd
    fator = _CONVERSOES_UNIDADE.get((de, para))
    if fator is None:
        return None
    return qtd * fator


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


async def _garantir_cardapio_hoje(conn) -> None:
    """Se não há cardápio para hoje, copia do último dia que teve."""
    hoje = _hoje()
    existe = await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM produtos_dia WHERE data_venda = $1)",
        hoje,
    )
    if existe:
        return
    # Busca a data mais recente com cardápio
    ultima = await conn.fetchval(
        "SELECT MAX(data_venda) FROM produtos_dia WHERE data_venda < $1",
        hoje,
    )
    if ultima is None:
        return
    await conn.execute(
        """
        INSERT INTO produtos_dia (nome, preco, data_venda)
        SELECT nome, preco, $1
        FROM produtos_dia
        WHERE data_venda = $2
        """,
        hoje, ultima,
    )


async def buscar_cardapio_hoje() -> list[asyncpg.Record]:
    pool = get_pool()
    async with pool.acquire() as conn:
        await _garantir_cardapio_hoje(conn)
        return await conn.fetch(
            "SELECT nome, preco FROM produtos_dia WHERE data_venda = $1 ORDER BY nome",
            _hoje(),
        )


async def buscar_preco_produto(nome: str) -> dict | None:
    """Busca preço do produto no cardápio de hoje.
    Retorna {"preco": Decimal, "nome_real": str} ou None.
    Tenta match exato primeiro, depois similaridade contra o cardápio do dia.
    """
    from difflib import SequenceMatcher

    pool = get_pool()
    async with pool.acquire() as conn:
        await _garantir_cardapio_hoje(conn)
        # 1. Match exato (case-insensitive)
        row = await conn.fetchrow(
            "SELECT nome, preco FROM produtos_dia WHERE data_venda = $1 AND lower(nome) = lower($2)",
            _hoje(),
            nome,
        )
        if row:
            return {"preco": row["preco"], "nome_real": row["nome"]}

        # 2. Fallback: similaridade contra cardápio do dia
        cardapio = await conn.fetch(
            "SELECT nome, preco FROM produtos_dia WHERE data_venda = $1",
            _hoje(),
        )

    melhor_match = None
    melhor_ratio = 0.0
    for item in cardapio:
        ratio = SequenceMatcher(None, nome.lower(), item["nome"].lower()).ratio()
        if ratio > melhor_ratio:
            melhor_ratio = ratio
            melhor_match = item

    if melhor_match and melhor_ratio >= 0.6:
        return {"preco": melhor_match["preco"], "nome_real": melhor_match["nome"]}

    return None


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
    """Retorna comandas abertas: prioriza match exato, senão busca parcial."""
    pool = get_pool()
    async with pool.acquire() as conn:
        # 1. Match exato (case-insensitive)
        exatas = await conn.fetch(
            """
            SELECT id, nome_cliente, data_criacao
            FROM comandas
            WHERE status = 'aberta' AND lower(nome_cliente) = lower($1)
            ORDER BY data_criacao
            """,
            nome,
        )
        if exatas:
            return exatas
        # 2. Fallback: busca parcial
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
                    SET quantidade = $1::int, valor_total = $1::numeric * valor_unitario
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

async def registrar_pagamento(comanda_id: UUID, valor: Decimal, metodo: str | None = None) -> None:
    pool = get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO pagamentos (comanda_id, valor, metodo) VALUES ($1, $2, $3)",
            comanda_id,
            valor,
            metodo,
        )


async def registrar_pagamento_e_fechar(comanda_id: UUID, valor: Decimal, metodo: str | None = None) -> Decimal:
    """Registra pagamento e fecha a comanda se quitada. Retorna o novo saldo devedor."""
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO pagamentos (comanda_id, valor, metodo) VALUES ($1, $2, $3)",
                comanda_id, valor, metodo,
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

def _qtd_storage_entrada(
    qtd: Decimal,
    unidade_entrada: str,
    litros: Decimal | None,
    unidade_storage: str,
) -> Decimal | None:
    """Converte quantidade da entrada para unidade do item de estoque.

    Regra spec §Edge: se item é em L e entrada traz 'litros' (capacidade por
    unidade comprada, ex: 1 barril × 50 L), usa `quantidade * litros`.
    Caso contrário aplica conversão canônica de unidades.
    Retorna None se incompatível.
    """
    if unidade_storage == "L" and litros is not None:
        return qtd * litros
    return _converter_unidade(qtd, unidade_entrada, unidade_storage)


async def inserir_entradas(
    itens: list[dict],
    fornecedor: str | None,
) -> list[dict]:
    """Registra entradas (compras) e, quando o produto bate com itens_estoque,
    incrementa qtd + atualiza custo (último preço) + acumula no lote aberto.

    Raise ValueError se a entrada bate com um item cujo par de unidades é
    incompatível (ex: entrada em kg para item em L).
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = []
            for item in itens:
                qtd = Decimal(str(item["quantidade"]))
                preco = Decimal(str(item["preco_unitario"]))
                litros = Decimal(str(item["litros"])) if item.get("litros") else None
                unidade_entrada = item["unidade"]

                estoque = await conn.fetchrow(
                    "SELECT id, unidade FROM itens_estoque WHERE lower(nome) = lower($1)",
                    item["produto"],
                )
                item_estoque_id = None
                if estoque is not None:
                    qtd_storage = _qtd_storage_entrada(
                        qtd, unidade_entrada, litros, estoque["unidade"],
                    )
                    if qtd_storage is None:
                        raise ValueError(
                            f"Unidade _{unidade_entrada}_ incompatível com o estoque "
                            f"cadastrado em _{estoque['unidade']}_."
                        )
                    item_estoque_id = estoque["id"]
                    # qtd sempre incrementa
                    await conn.execute(
                        """
                        UPDATE itens_estoque
                        SET qtd = qtd + $1, atualizado_em = NOW()
                        WHERE id = $2
                        """,
                        qtd_storage, item_estoque_id,
                    )
                    # custo só se preço > 0 (edge case: brinde preserva custo anterior)
                    if preco > 0 and qtd_storage > 0:
                        custo_storage = (qtd * preco) / qtd_storage
                        await conn.execute(
                            "UPDATE itens_estoque SET custo_unitario = $1 WHERE id = $2",
                            custo_storage, item_estoque_id,
                        )
                    # acumula no lote aberto
                    lote_id = await _recomputar_lote(conn, item_estoque_id)
                    await conn.execute(
                        """
                        UPDATE lotes_estoque
                        SET qtd_comprada = qtd_comprada + $1
                        WHERE id = $2
                        """,
                        qtd_storage, lote_id,
                    )

                row = await conn.fetchrow(
                    """
                    INSERT INTO entradas
                        (produto_nome, unidade, quantidade, litros,
                         valor_unitario, valor_total, fornecedor, item_estoque_id)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    RETURNING produto_nome, unidade, quantidade, litros,
                              valor_unitario, valor_total, fornecedor
                    """,
                    item["produto"], unidade_entrada, qtd, litros,
                    preco, qtd * preco, fornecedor, item_estoque_id,
                )
                rows.append(dict(row))
    return rows


async def remover_ultima_entrada(produto: str | None = None) -> dict | None:
    """Remove a entrada mais recente (opcionalmente filtrada por produto).

    Se a entrada tinha item_estoque_id, reverte incremento de qtd, acumulador
    do lote e (se houver entrada anterior do mesmo item) recalcula o custo
    com base no valor unitário da penúltima entrada.

    Raise ValueError se a entrada pertence a lote fechado (protege histórico).
    Retorna a entrada removida.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            if produto:
                entrada = await conn.fetchrow(
                    """
                    SELECT id, produto_nome, unidade, quantidade, litros,
                           valor_unitario, valor_total, fornecedor,
                           item_estoque_id, criado_em
                    FROM entradas
                    WHERE lower(produto_nome) = lower($1)
                    ORDER BY criado_em DESC LIMIT 1
                    """,
                    produto,
                )
            else:
                entrada = await conn.fetchrow(
                    """
                    SELECT id, produto_nome, unidade, quantidade, litros,
                           valor_unitario, valor_total, fornecedor,
                           item_estoque_id, criado_em
                    FROM entradas
                    ORDER BY criado_em DESC LIMIT 1
                    """
                )
            if entrada is None:
                return None

            if entrada["item_estoque_id"] is not None:
                item = await conn.fetchrow(
                    "SELECT id, unidade FROM itens_estoque WHERE id = $1",
                    entrada["item_estoque_id"],
                )
                if item is not None:
                    lote = await conn.fetchrow(
                        """
                        SELECT id, fechamento FROM lotes_estoque
                        WHERE item_estoque_id = $1
                          AND abertura <= $2
                          AND (fechamento IS NULL OR fechamento >= $2)
                        ORDER BY abertura DESC LIMIT 1
                        """,
                        entrada["item_estoque_id"], entrada["criado_em"],
                    )
                    if lote is not None and lote["fechamento"] is not None:
                        raise ValueError(
                            f"Entrada faz parte do lote fechado em "
                            f"{lote['fechamento'].date()}. Não é possível remover."
                        )

                    qtd = Decimal(str(entrada["quantidade"]))
                    litros = (
                        Decimal(str(entrada["litros"]))
                        if entrada["litros"] is not None else None
                    )
                    qtd_storage = _qtd_storage_entrada(
                        qtd, entrada["unidade"], litros, item["unidade"],
                    )
                    if qtd_storage is not None:
                        await conn.execute(
                            """
                            UPDATE itens_estoque
                            SET qtd = qtd - $1, atualizado_em = NOW()
                            WHERE id = $2
                            """,
                            qtd_storage, entrada["item_estoque_id"],
                        )
                        if lote is not None:
                            await conn.execute(
                                """
                                UPDATE lotes_estoque
                                SET qtd_comprada = qtd_comprada - $1
                                WHERE id = $2
                                """,
                                qtd_storage, lote["id"],
                            )

                    prev = await conn.fetchrow(
                        """
                        SELECT valor_unitario, quantidade, litros, unidade
                        FROM entradas
                        WHERE item_estoque_id = $1 AND id <> $2
                        ORDER BY criado_em DESC LIMIT 1
                        """,
                        entrada["item_estoque_id"], entrada["id"],
                    )
                    if prev is not None:
                        prev_qtd = Decimal(str(prev["quantidade"]))
                        prev_preco = Decimal(str(prev["valor_unitario"]))
                        prev_litros = (
                            Decimal(str(prev["litros"]))
                            if prev["litros"] is not None else None
                        )
                        prev_qtd_storage = _qtd_storage_entrada(
                            prev_qtd, prev["unidade"], prev_litros, item["unidade"],
                        )
                        if (prev_qtd_storage is not None
                                and prev_qtd_storage > 0 and prev_preco > 0):
                            custo_anterior = (prev_qtd * prev_preco) / prev_qtd_storage
                            await conn.execute(
                                "UPDATE itens_estoque SET custo_unitario = $1 WHERE id = $2",
                                custo_anterior, entrada["item_estoque_id"],
                            )

            await conn.execute("DELETE FROM entradas WHERE id = $1", entrada["id"])

    return {
        "produto_nome": entrada["produto_nome"],
        "unidade": entrada["unidade"],
        "quantidade": entrada["quantidade"],
        "litros": entrada["litros"],
        "valor_unitario": entrada["valor_unitario"],
        "valor_total": entrada["valor_total"],
        "fornecedor": entrada["fornecedor"],
    }


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
# itens_estoque (estoque e categorias — migration 005)
# ------------------------------------------------------------------

_CATEGORIAS_ESTOQUE = (
    "malte", "lúpulo", "embalagem pet", "copo",
    "barril", "garrafa", "petiscos", "gelo",
)
_UNIDADES_ESTOQUE = ("L", "ml", "kg", "g", "un")


async def criar_item_estoque(
    nome: str,
    unidade: str,
    qtd_inicial: Decimal,
    custo_unitario: Decimal,
    categoria: str,
) -> dict | None:
    """Cria item de estoque. Retorna None se já existe (case-insensitive)."""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO itens_estoque (nome, unidade, qtd, custo_unitario, categoria)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (lower(nome)) DO NOTHING
            RETURNING id, nome, unidade, qtd, custo_unitario, categoria
            """,
            nome, unidade, qtd_inicial, custo_unitario, categoria,
        )
    return dict(row) if row else None


async def buscar_item_estoque(nome: str) -> dict | None:
    """Match exato case-insensitive. Sem fallback de similaridade (CONCERNS)."""
    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, nome, unidade, qtd, custo_unitario, categoria,
                   criado_em, atualizado_em
            FROM itens_estoque
            WHERE lower(nome) = lower($1)
            """,
            nome,
        )
    return dict(row) if row else None


async def listar_estoque() -> list[dict]:
    """Lista itens agrupáveis por categoria, com valor congelado por linha."""
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, nome, unidade, qtd, custo_unitario, categoria,
                   (qtd * custo_unitario) AS valor
            FROM itens_estoque
            ORDER BY categoria, lower(nome)
            """
        )
    return [dict(r) for r in rows]


async def capital_congelado() -> Decimal:
    """Σ qtd × custo_unitario sobre todos os itens de estoque."""
    pool = get_pool()
    async with pool.acquire() as conn:
        total = await conn.fetchval(
            "SELECT COALESCE(SUM(qtd * custo_unitario), 0) FROM itens_estoque"
        )
    return Decimal(total)


async def _recomputar_lote(conn: asyncpg.Connection, item_id: UUID) -> UUID:
    """Garante que haja um lote aberto para o item. Retorna o id do lote aberto."""
    row = await conn.fetchrow(
        """
        SELECT id FROM lotes_estoque
        WHERE item_estoque_id = $1 AND fechamento IS NULL
        """,
        item_id,
    )
    if row:
        return row["id"]
    row = await conn.fetchrow(
        """
        INSERT INTO lotes_estoque (item_estoque_id)
        VALUES ($1)
        RETURNING id
        """,
        item_id,
    )
    return row["id"]


async def atualizar_estoque(
    nome: str,
    *,
    nova_qtd: Decimal | None = None,
    nova_categoria: str | None = None,
    novo_custo: Decimal | None = None,
) -> dict | None:
    """Update parcial de itens_estoque. Mínimo um campo obrigatório (validado no handler).

    Se nova_qtd informado: calcula delta = nova_qtd - qtd_atual e acumula em
    lotes_estoque.qtd_ajustes do lote aberto (abre lote se necessário).
    Ajuste NÃO conta como perda (separado no fechamento do lote).

    Retorna None se o item não existe. Retorna dict com:
      {anterior: {...}, novo: {...}, delta_qtd, lote_aberto_id}
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            atual = await conn.fetchrow(
                """
                SELECT id, nome, unidade, qtd, custo_unitario, categoria
                FROM itens_estoque
                WHERE lower(nome) = lower($1)
                FOR UPDATE
                """,
                nome,
            )
            if atual is None:
                return None

            anterior = dict(atual)
            campos_sql: list[str] = []
            valores: list = []
            idx = 1

            delta_qtd: Decimal | None = None
            lote_id: UUID | None = None

            if nova_qtd is not None:
                delta_qtd = Decimal(nova_qtd) - Decimal(anterior["qtd"])
                campos_sql.append(f"qtd = ${idx}")
                valores.append(Decimal(nova_qtd))
                idx += 1
                lote_id = await _recomputar_lote(conn, anterior["id"])
                await conn.execute(
                    """
                    UPDATE lotes_estoque
                    SET qtd_ajustes = qtd_ajustes + $1
                    WHERE id = $2
                    """,
                    delta_qtd, lote_id,
                )

            if nova_categoria is not None:
                campos_sql.append(f"categoria = ${idx}")
                valores.append(nova_categoria)
                idx += 1

            if novo_custo is not None:
                campos_sql.append(f"custo_unitario = ${idx}")
                valores.append(Decimal(novo_custo))
                idx += 1

            if not campos_sql:
                return {
                    "anterior": anterior,
                    "novo": anterior,
                    "delta_qtd": None,
                    "lote_aberto_id": None,
                }

            campos_sql.append("atualizado_em = NOW()")
            valores.append(anterior["id"])
            novo = await conn.fetchrow(
                f"""
                UPDATE itens_estoque
                SET {", ".join(campos_sql)}
                WHERE id = ${idx}
                RETURNING id, nome, unidade, qtd, custo_unitario, categoria
                """,
                *valores,
            )

    return {
        "anterior": anterior,
        "novo": dict(novo),
        "delta_qtd": delta_qtd,
        "lote_aberto_id": lote_id,
    }


async def fechar_lote(nome_item: str, forcar: bool = False) -> dict | None:
    """Fecha o lote aberto do item e calcula perda.

    Fórmula (EST-41): perda = qtd_comprada + qtd_ajustes - qtd_vendida - qtd_atual.

    Se qtd_atual > 0 e não `forcar`: retorna {"confirmar": True, ...} sem alterar.
    Senão: preenche fechamento/qtd_restante/perda_qtd/perda_pct e zera itens_estoque.qtd.
    Se qtd_vendida == 0: perda_pct = None + sinal "sem_vendas".
    Retorna None se o item não existe.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            item = await conn.fetchrow(
                """
                SELECT id, nome, unidade, qtd
                FROM itens_estoque
                WHERE lower(nome) = lower($1)
                FOR UPDATE
                """,
                nome_item,
            )
            if item is None:
                return None

            lote = await conn.fetchrow(
                """
                SELECT id, qtd_comprada, qtd_vendida, qtd_ajustes, abertura
                FROM lotes_estoque
                WHERE item_estoque_id = $1 AND fechamento IS NULL
                FOR UPDATE
                """,
                item["id"],
            )
            if lote is None:
                return {
                    "nenhum_lote_aberto": True,
                    "item_nome": item["nome"],
                }

            qtd_atual = Decimal(item["qtd"])
            qtd_comprada = Decimal(lote["qtd_comprada"])
            qtd_vendida = Decimal(lote["qtd_vendida"])
            qtd_ajustes = Decimal(lote["qtd_ajustes"])

            perda = qtd_comprada + qtd_ajustes - qtd_vendida - qtd_atual
            sem_vendas = qtd_vendida == 0
            perda_pct: Decimal | None = None
            if not sem_vendas and qtd_comprada > 0:
                perda_pct = (perda / qtd_comprada) * Decimal("100")

            if qtd_atual > 0 and not forcar:
                return {
                    "confirmar": True,
                    "item_nome": item["nome"],
                    "unidade": item["unidade"],
                    "qtd_atual": qtd_atual,
                    "qtd_comprada": qtd_comprada,
                    "qtd_vendida": qtd_vendida,
                    "qtd_ajustes": qtd_ajustes,
                    "perda": perda,
                    "perda_pct": perda_pct,
                    "sem_vendas": sem_vendas,
                }

            await conn.execute(
                """
                UPDATE lotes_estoque
                SET fechamento   = NOW(),
                    qtd_restante = $1,
                    perda_qtd    = $2,
                    perda_pct    = $3
                WHERE id = $4
                """,
                qtd_atual, perda, perda_pct, lote["id"],
            )
            await conn.execute(
                """
                UPDATE itens_estoque
                SET qtd = 0, atualizado_em = NOW()
                WHERE id = $1
                """,
                item["id"],
            )

    return {
        "confirmar": False,
        "item_nome": item["nome"],
        "unidade": item["unidade"],
        "qtd_comprada": qtd_comprada,
        "qtd_vendida": qtd_vendida,
        "qtd_ajustes": qtd_ajustes,
        "qtd_restante": qtd_atual,
        "perda": perda,
        "perda_pct": perda_pct,
        "sem_vendas": sem_vendas,
    }


# ------------------------------------------------------------------
# mapeamento_produto_estoque
# ------------------------------------------------------------------

async def mapear_produto(
    produto: str,
    item_nome: str,
    consumo: Decimal,
) -> dict | None:
    """Associa produto do cardápio a item de estoque com consumo por unidade.

    UPSERT por lower(produto_nome). Retorna None se item de estoque não existe.
    Retorna dict do mapeamento salvo (com nome do item já resolvido).
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        item = await conn.fetchrow(
            """
            SELECT id, nome, unidade
            FROM itens_estoque
            WHERE lower(nome) = lower($1)
            """,
            item_nome,
        )
        if item is None:
            return None

        row = await conn.fetchrow(
            """
            INSERT INTO mapeamento_produto_estoque
                (produto_nome, item_estoque_id, consumo_por_unidade, unidade)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (lower(produto_nome)) DO UPDATE
                SET item_estoque_id = EXCLUDED.item_estoque_id,
                    consumo_por_unidade = EXCLUDED.consumo_por_unidade,
                    unidade = EXCLUDED.unidade,
                    atualizado_em = NOW()
            RETURNING produto_nome, item_estoque_id, consumo_por_unidade, unidade
            """,
            produto, item["id"], Decimal(consumo), item["unidade"],
        )
    return {
        "produto_nome": row["produto_nome"],
        "item_nome": item["nome"],
        "item_estoque_id": row["item_estoque_id"],
        "consumo_por_unidade": row["consumo_por_unidade"],
        "unidade": row["unidade"],
    }


async def produto_ja_existiu_cardapio(nome: str) -> bool:
    """True se o produto apareceu em produtos_dia em qualquer data (case-insensitive)."""
    pool = get_pool()
    async with pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT EXISTS(SELECT 1 FROM produtos_dia WHERE lower(nome) = lower($1))",
            nome,
        )


async def listar_mapeamentos() -> list[dict]:
    """Lista todos os mapeamentos com nome do item resolvido, ordenados por produto."""
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT m.produto_nome,
                   i.nome          AS item_nome,
                   m.consumo_por_unidade,
                   m.unidade
            FROM mapeamento_produto_estoque m
            JOIN itens_estoque i ON i.id = m.item_estoque_id
            ORDER BY lower(m.produto_nome)
            """
        )
    return [dict(r) for r in rows]


async def remover_mapeamento(produto: str) -> bool:
    """Remove mapeamento pelo nome do produto (case-insensitive). Retorna True se apagou."""
    pool = get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM mapeamento_produto_estoque WHERE lower(produto_nome) = lower($1)",
            produto,
        )
    # asyncpg retorna "DELETE N"
    return result.endswith(" 0") is False


async def consolidar_baixa_estoque(data: date) -> dict:
    """Aplica baixa consolidada de estoque para o dia operacional.

    Fluxo (idempotente via fechamentos_dia):
      1. INSERT fechamentos_dia(data_venda) ON CONFLICT DO NOTHING. Se 0 linhas,
         carrega snapshot persistido e retorna ja_fechado=True.
      2. Agrega itens_comanda do dia por produto_nome.
      3. Para cada produto:
         a. Mapeamento explícito em mapeamento_produto_estoque → usa consumo_por_unidade.
         b. Senão, match único por substring com item unidade L/ml → default 0.4 L.
         c. Senão, acumula em produtos_sem_mapa (JSONB no snapshot).
      4. Decrementa itens_estoque.qtd e acumula lotes_estoque.qtd_vendida.
      5. Saldo negativo permitido + sinalizado em avisos.
      6. Persiste totais + produtos_sem_mapa no snapshot.

    Tudo envolto em async with conn.transaction() — erro aborta tudo.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            inserted = await conn.fetchval(
                """
                INSERT INTO fechamentos_dia (data_venda)
                VALUES ($1)
                ON CONFLICT (data_venda) DO NOTHING
                RETURNING 1
                """,
                data,
            )
            if inserted is None:
                snap = await conn.fetchrow(
                    """
                    SELECT data_venda, fechado_em, total_vendido, total_recebido,
                           total_entradas, produtos_sem_mapa
                    FROM fechamentos_dia
                    WHERE data_venda = $1
                    """,
                    data,
                )
                return {
                    "ja_fechado": True,
                    "snapshot": dict(snap) if snap else None,
                }

            vendas = await conn.fetch(
                """
                SELECT i.produto_nome,
                       SUM(i.quantidade) AS quantidade_total
                FROM itens_comanda i
                JOIN comandas c ON c.id = i.comanda_id
                WHERE c.data_criacao::date = $1
                GROUP BY i.produto_nome
                """,
                data,
            )

            produtos_baixados: list[dict] = []
            produtos_sem_mapa: list[str] = []
            avisos_negativos: list[dict] = []

            for venda in vendas:
                produto = venda["produto_nome"]
                qtd_vendida_un = Decimal(venda["quantidade_total"])

                mapping = await conn.fetchrow(
                    """
                    SELECT m.item_estoque_id, m.consumo_por_unidade,
                           i.nome AS item_nome, i.unidade
                    FROM mapeamento_produto_estoque m
                    JOIN itens_estoque i ON i.id = m.item_estoque_id
                    WHERE lower(m.produto_nome) = lower($1)
                    """,
                    produto,
                )

                item_id = None
                item_unidade = None
                item_nome = None
                baixa_storage: Decimal | None = None
                estrategia = None

                if mapping is not None:
                    item_id = mapping["item_estoque_id"]
                    item_unidade = mapping["unidade"]
                    item_nome = mapping["item_nome"]
                    baixa_storage = Decimal(mapping["consumo_por_unidade"]) * qtd_vendida_un
                    estrategia = "mapeamento"
                else:
                    candidatos = await conn.fetch(
                        """
                        SELECT id, nome, unidade FROM itens_estoque
                        WHERE unidade IN ('L','ml')
                          AND (lower(nome) LIKE '%' || lower($1) || '%'
                            OR lower($1) LIKE '%' || lower(nome) || '%')
                        """,
                        produto,
                    )
                    if len(candidatos) == 1:
                        c = candidatos[0]
                        item_id = c["id"]
                        item_unidade = c["unidade"]
                        item_nome = c["nome"]
                        default_L = Decimal(_ML_POR_DOSE) / Decimal("1000")
                        if item_unidade == "L":
                            consumo = default_L
                        else:  # ml
                            consumo = Decimal(_ML_POR_DOSE)
                        baixa_storage = consumo * qtd_vendida_un
                        estrategia = "default_400ml"
                    else:
                        produtos_sem_mapa.append(produto)
                        continue

                lote_id = await _recomputar_lote(conn, item_id)
                novo_qtd = await conn.fetchval(
                    """
                    UPDATE itens_estoque
                    SET qtd = qtd - $1, atualizado_em = NOW()
                    WHERE id = $2
                    RETURNING qtd
                    """,
                    baixa_storage, item_id,
                )
                await conn.execute(
                    """
                    UPDATE lotes_estoque
                    SET qtd_vendida = qtd_vendida + $1
                    WHERE id = $2
                    """,
                    baixa_storage, lote_id,
                )
                produtos_baixados.append({
                    "produto": produto,
                    "item_nome": item_nome,
                    "baixa": baixa_storage,
                    "unidade": item_unidade,
                    "estrategia": estrategia,
                })
                if novo_qtd is not None and Decimal(novo_qtd) < 0:
                    avisos_negativos.append({
                        "item_nome": item_nome,
                        "qtd": Decimal(novo_qtd),
                        "unidade": item_unidade,
                    })

            total_vendido = await conn.fetchval(
                """
                SELECT COALESCE(SUM(i.valor_total), 0)
                FROM itens_comanda i
                JOIN comandas c ON c.id = i.comanda_id
                WHERE c.data_criacao::date = $1
                """,
                data,
            )
            total_recebido = await conn.fetchval(
                """
                SELECT COALESCE(SUM(p.valor), 0)
                FROM pagamentos p
                JOIN comandas c ON c.id = p.comanda_id
                WHERE c.data_criacao::date = $1
                """,
                data,
            )
            total_entradas = await conn.fetchval(
                """
                SELECT COALESCE(SUM(valor_total), 0)
                FROM entradas
                WHERE criado_em::date = $1
                """,
                data,
            )

            await conn.execute(
                """
                UPDATE fechamentos_dia
                SET total_vendido     = $1,
                    total_recebido    = $2,
                    total_entradas    = $3,
                    produtos_sem_mapa = $4::jsonb
                WHERE data_venda = $5
                """,
                total_vendido, total_recebido, total_entradas,
                json.dumps(produtos_sem_mapa), data,
            )

    return {
        "ja_fechado": False,
        "produtos_baixados": produtos_baixados,
        "produtos_sem_mapa": produtos_sem_mapa,
        "avisos_negativos": avisos_negativos,
        "total_vendido": total_vendido,
        "total_recebido": total_recebido,
        "total_entradas": total_entradas,
    }


async def gasto_por_categoria(de: date, ate: date) -> list[dict]:
    """Soma entradas.valor_total por categoria do item de estoque no período.

    Entradas sem item_estoque_id (sem FK) caem em 'sem categoria'.
    Período é calendário — o chamador aplica _hoje() para janelas operacionais.
    """
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT COALESCE(i.categoria, 'sem categoria') AS categoria,
                   SUM(e.valor_total)                     AS total,
                   COUNT(*)                               AS qtd_entradas
            FROM entradas e
            LEFT JOIN itens_estoque i ON i.id = e.item_estoque_id
            WHERE e.criado_em::date BETWEEN $1 AND $2
            GROUP BY COALESCE(i.categoria, 'sem categoria')
            ORDER BY total DESC
            """,
            de, ate,
        )
    return [dict(r) for r in rows]


# ------------------------------------------------------------------
# insights e alertas
# ------------------------------------------------------------------


async def receita_intervalo(de: date, ate: date, categoria: str | None = None) -> Decimal:
    async with _pool.acquire() as conn:
        if categoria is None:
            result = await conn.fetchval(
                """
                SELECT COALESCE(SUM(valor), 0)
                FROM pagamentos
                WHERE criado_em::date BETWEEN $1 AND $2
                """,
                de, ate,
            )
        else:
            result = await conn.fetchval(
                """
                SELECT COALESCE(SUM(i.valor_total), 0)
                FROM itens_comanda i
                JOIN comandas c ON c.id = i.comanda_id
                JOIN categoria_produto cp ON lower(cp.produto_nome) = lower(i.produto_nome)
                WHERE lower(cp.categoria) = lower($3)
                  AND c.data_criacao::date BETWEEN $1 AND $2
                """,
                de, ate, categoria,
            )
    return Decimal(str(result)) if result else Decimal(0)


async def comandas_abertas_ha_mais_que(horas: int) -> list[dict]:
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT nome_cliente, data_criacao, saldo_devedor
            FROM v_saldo_comandas
            WHERE status = 'aberta'
              AND data_criacao <= NOW() - make_interval(hours => $1)
            ORDER BY data_criacao ASC
            """,
            horas,
        )
    return [dict(r) for r in rows]


async def upsert_meta(categoria: str, mes_referencia: date, valor: Decimal) -> None:
    async with _pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO metas_mensais (categoria, mes_referencia, valor_mensal)
            VALUES ($1, $2, $3)
            ON CONFLICT (categoria, mes_referencia)
            DO UPDATE SET valor_mensal = EXCLUDED.valor_mensal, atualizada_em = NOW()
            """,
            categoria, mes_referencia, valor,
        )


async def remover_meta(categoria: str, mes_referencia: date) -> bool:
    async with _pool.acquire() as conn:
        result = await conn.execute(
            "DELETE FROM metas_mensais WHERE categoria = $1 AND mes_referencia = $2",
            categoria, mes_referencia,
        )
    return result != "DELETE 0"


async def listar_metas_mes(mes_referencia: date) -> list[dict]:
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT categoria, valor_mensal
            FROM metas_mensais
            WHERE mes_referencia = $1
            ORDER BY valor_mensal DESC
            """,
            mes_referencia,
        )
    return [dict(r) for r in rows]


async def count_produtos_na_categoria(categoria: str) -> int:
    async with _pool.acquire() as conn:
        return await conn.fetchval(
            "SELECT COUNT(*) FROM categoria_produto WHERE categoria = $1",
            categoria,
        )


async def set_categoria_produto(produto_nome: str, categoria: str) -> None:
    if not await produto_ja_existiu_cardapio(produto_nome):
        raise ValueError(f"{produto_nome} não existe em nenhum cardápio")
    async with _pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO categoria_produto (produto_nome, categoria)
            VALUES ($1, $2)
            ON CONFLICT (lower(produto_nome))
            DO UPDATE SET categoria = EXCLUDED.categoria, atualizada_em = NOW()
            """,
            produto_nome, categoria,
        )


async def progresso_metas(mes_referencia: date) -> list[dict]:
    if mes_referencia.month < 12:
        ultimo_dia = mes_referencia.replace(month=mes_referencia.month % 12 + 1, day=1) - timedelta(days=1)
    else:
        ultimo_dia = mes_referencia.replace(year=mes_referencia.year + 1, month=1, day=1) - timedelta(days=1)
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                m.categoria,
                m.valor_mensal,
                COALESCE(SUM(i.valor_total), 0) AS receita_mes_ate_hoje
            FROM metas_mensais m
            LEFT JOIN categoria_produto cp ON cp.categoria = m.categoria
            LEFT JOIN itens_comanda i ON lower(i.produto_nome) = lower(cp.produto_nome)
            LEFT JOIN comandas c ON c.id = i.comanda_id
                AND c.data_criacao::date BETWEEN $2 AND $3
            WHERE m.mes_referencia = $1
            GROUP BY m.categoria, m.valor_mensal
            ORDER BY m.valor_mensal DESC
            """,
            mes_referencia, mes_referencia, ultimo_dia,
        )
    return [
        {
            "categoria": r["categoria"],
            "valor_mensal": Decimal(str(r["valor_mensal"])),
            "receita_mes_ate_hoje": Decimal(str(r["receita_mes_ate_hoje"])),
        }
        for r in rows
    ]


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
