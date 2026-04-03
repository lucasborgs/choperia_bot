"""
Router: recebe uma Action do NLU e executa a lógica de negócio correspondente.
Retorna uma string formatada para ser enviada ao dono via WhatsApp.
"""

import logging
from decimal import Decimal

from app import database as db

logger = logging.getLogger(__name__)


def _safe_int(value, default: int = 1) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _safe_float(value, default: float | None = None) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


async def dispatch(action: dict) -> str:
    intent = action.get("intent", "desconhecido")
    params = action.get("params", {})

    handlers = {
        "definir_cardapio": _definir_cardapio,
        "consultar_cardapio": _consultar_cardapio,
        "adicionar_itens": _adicionar_itens,
        "remover_item": _remover_item,
        "consultar_comanda": _consultar_comanda,
        "listar_comandas": _listar_comandas,
        "pagar_conta": _pagar_conta,
        "relatorio_dia": _relatorio_dia,
        "renomear_cliente": _renomear_cliente,
        "registrar_entrada": _registrar_entrada,
        "remover_cardapio": _remover_cardapio,
        "remover_entrada": _remover_entrada,
        "configurar_produto": _configurar_produto,
        "desconhecido": _desconhecido,
    }

    handler = handlers.get(intent, _desconhecido)
    try:
        return await handler(params)
    except Exception as exc:
        logger.exception("Erro no handler '%s': %s", intent, exc)
        raise


# ------------------------------------------------------------------
# Handlers
# ------------------------------------------------------------------

async def _definir_cardapio(params: dict) -> str:
    itens = params.get("itens", [])
    if not itens:
        return "❌ Nenhum item informado para o cardápio."

    rows = await db.limpar_e_inserir_cardapio(itens)
    linhas = "\n".join(f"• {r['nome']} — R$ {r['preco']:.2f}" for r in rows)

    # Mostra cardápio completo do dia
    todos = await db.buscar_cardapio_hoje()
    if len(todos) > len(rows):
        linhas_total = "\n".join(f"• {r['nome']} — R$ {r['preco']:.2f}" for r in todos)
        return (
            f"✅ *Cardápio atualizado!*\n"
            f"Adicionado/alterado: {len(rows)} item(ns)\n\n"
            f"📋 *Cardápio completo ({len(todos)} itens):*\n{linhas_total}"
        )
    return f"✅ *Cardápio do dia atualizado* ({len(rows)} itens):\n{linhas}"


async def _consultar_cardapio(params: dict) -> str:
    cardapio = await db.buscar_cardapio_hoje()
    if not cardapio:
        return "ℹ️ Nenhum cardápio definido para hoje."
    linhas = "\n".join(f"• {r['nome']} — R$ {r['preco']:.2f}" for r in cardapio)
    return f"📋 *Cardápio de hoje ({len(cardapio)} itens):*\n{linhas}"


async def _adicionar_itens(params: dict) -> str:
    cliente = params.get("cliente", "").strip()
    itens_req = params.get("itens", [])

    if not cliente:
        return "❌ Nome do cliente não informado."
    if not itens_req:
        return "❌ Nenhum item informado."

    # Resolve preços no cardápio do dia
    itens_com_preco = []
    nao_encontrados = []
    for item in itens_req:
        produto = item.get("produto", "").strip()
        quantidade = _safe_int(item.get("quantidade", 1), default=1)
        preco = await db.buscar_preco_produto(produto)
        if preco is None:
            nao_encontrados.append(produto)
        else:
            itens_com_preco.append(
                {"produto": produto, "quantidade": quantidade, "valor_unitario": preco}
            )

    if nao_encontrados:
        lista = ", ".join(nao_encontrados)
        return f"❌ Produto(s) não encontrado(s) no cardápio de hoje: {lista}"

    comanda_id = await db.buscar_ou_criar_comanda(cliente)
    await db.inserir_itens(comanda_id, itens_com_preco)

    linhas = "\n".join(
        f"• {i['quantidade']}x {i['produto']} — R$ {i['valor_unitario']:.2f} cada"
        for i in itens_com_preco
    )
    return f"✅ *Itens adicionados* na comanda de *{cliente}*:\n{linhas}"


async def _remover_item(params: dict) -> str:
    cliente = params.get("cliente", "").strip()
    produto = params.get("produto", "").strip()
    quantidade = _safe_int(params.get("quantidade", 1), default=1)

    if not cliente or not produto:
        return "❌ Informe o cliente e o produto a remover."

    comandas = await db.buscar_comandas_abertas_por_nome(cliente)
    if not comandas:
        return f"❌ Nenhuma comanda aberta para *{cliente}*."
    if len(comandas) > 1:
        nomes = ", ".join(c["nome_cliente"] for c in comandas)
        return f"⚠️ Mais de uma comanda encontrada: {nomes}\nSeja mais específico."

    comanda_id = comandas[0]["id"]
    encontrado = await db.remover_item(comanda_id, produto, quantidade)
    if not encontrado:
        return f"❌ Item *{produto}* não encontrado na comanda de *{cliente}*."

    return f"✅ Removido {quantidade}x *{produto}* da comanda de *{cliente}*."


async def _consultar_comanda(params: dict) -> str:
    cliente = params.get("cliente", "").strip()
    if not cliente:
        return "❌ Informe o nome do cliente."

    comandas = await db.buscar_comandas_abertas_por_nome(cliente)
    if not comandas:
        return f"ℹ️ Nenhuma comanda aberta para *{cliente}*."
    if len(comandas) > 1:
        nomes = ", ".join(c["nome_cliente"] for c in comandas)
        return f"⚠️ Mais de uma comanda encontrada: {nomes}\nSeja mais específico."

    comanda_id = comandas[0]["id"]
    nome_real = comandas[0]["nome_cliente"]
    itens = await db.buscar_itens_comanda(comanda_id)
    saldo = await db.buscar_saldo(comanda_id)

    if not itens:
        return f"ℹ️ Comanda de *{nome_real}* está vazia."

    linhas = "\n".join(
        f"• {r['quantidade']}x {r['produto_nome']} — R$ {r['valor_total']:.2f}"
        for r in itens
    )
    total = saldo["total_consumido"] if saldo else Decimal(0)
    pago = saldo["total_pago"] if saldo else Decimal(0)
    devedor = saldo["saldo_devedor"] if saldo else Decimal(0)

    return (
        f"🧾 *Comanda de {nome_real}*\n"
        f"{linhas}\n"
        f"─────────────\n"
        f"Total: R$ {total:.2f}\n"
        f"Pago:  R$ {pago:.2f}\n"
        f"*Saldo: R$ {devedor:.2f}*"
    )


async def _listar_comandas(params: dict) -> str:
    comandas = await db.listar_comandas_abertas()
    if not comandas:
        return "ℹ️ Nenhuma comanda aberta no momento."

    linhas = "\n".join(
        f"• *{c['nome_cliente']}* — R$ {c['saldo_devedor']:.2f} a pagar"
        for c in comandas
    )
    return f"📋 *Comandas abertas ({len(comandas)}):*\n{linhas}"


async def _pagar_conta(params: dict) -> str:
    cliente = params.get("cliente", "").strip()
    valor_raw = params.get("valor")

    if not cliente:
        return "❌ Informe o nome do cliente."

    comandas = await db.buscar_comandas_abertas_por_nome(cliente)
    if not comandas:
        return f"ℹ️ Nenhuma comanda aberta para *{cliente}*."
    if len(comandas) > 1:
        nomes = ", ".join(c["nome_cliente"] for c in comandas)
        return f"⚠️ Mais de uma comanda encontrada: {nomes}\nSeja mais específico."

    comanda_id = comandas[0]["id"]
    nome_real = comandas[0]["nome_cliente"]
    saldo = await db.buscar_saldo(comanda_id)

    if saldo is None:
        return f"❌ Não foi possível calcular o saldo de *{nome_real}*."

    saldo_devedor: Decimal = saldo["saldo_devedor"]

    if valor_raw is None:
        valor = saldo_devedor
    else:
        parsed = _safe_float(valor_raw)
        if parsed is None:
            return f"❌ Valor inválido: *{valor_raw}*"
        valor = Decimal(str(parsed))

    if valor <= 0:
        return f"ℹ️ Comanda de *{nome_real}* já está quitada."

    novo_saldo = await db.registrar_pagamento_e_fechar(comanda_id, valor)

    if novo_saldo <= 0:
        return f"✅ *{nome_real}* pagou R$ {valor:.2f}. Comanda fechada! 🎉"
    else:
        return (
            f"✅ *{nome_real}* pagou R$ {valor:.2f}.\n"
            f"Saldo restante: R$ {novo_saldo:.2f}"
        )


async def _relatorio_dia(params: dict) -> str:
    dados = await db.relatorio_dia()

    por_produto = dados["por_produto"]
    if not por_produto:
        return "ℹ️ Nenhuma venda registrada hoje."

    linhas = "\n".join(
        f"• {p['produto_nome']}: {p['quantidade_total']}x — R$ {p['receita']:.2f}"
        for p in por_produto
    )
    return (
        f"📊 *Relatório do dia*\n"
        f"{linhas}\n"
        f"─────────────\n"
        f"Comandas: {dados['total_comandas']}\n"
        f"Vendido:  R$ {dados['total_vendido']:.2f}\n"
        f"Recebido: R$ {dados['total_recebido']:.2f}\n"
        f"*Pendente: R$ {dados['total_pendente']:.2f}*"
    )


async def _renomear_cliente(params: dict) -> str:
    nome_atual = params.get("nome_atual", "").strip()
    nome_novo = params.get("nome_novo", "").strip()

    if not nome_atual or not nome_novo:
        return "❌ Informe o nome atual e o nome correto."

    comandas = await db.buscar_comandas_abertas_por_nome(nome_atual)
    if not comandas:
        return f"❌ Nenhuma comanda aberta com o nome *{nome_atual}*."
    if len(comandas) > 1:
        nomes = ", ".join(c["nome_cliente"] for c in comandas)
        return f"⚠️ Mais de uma comanda encontrada: {nomes}\nSeja mais específico."

    comanda_id = comandas[0]["id"]
    await db.renomear_cliente(comanda_id, nome_novo)
    return f"✅ Nome corrigido: *{nome_atual}* → *{nome_novo}*"


_ML_POR_DOSE = 400  # copo padrão de chopp


async def _registrar_entrada(params: dict) -> str:
    itens = params.get("itens", [])
    fornecedor = (params.get("fornecedor") or "").strip() or None

    if not itens:
        return "❌ Nenhum item informado."

    rows = await db.inserir_entradas(itens, fornecedor)

    linhas = []
    total_geral = Decimal(0)
    for r in rows:
        descricao = f"{r['quantidade']:g}x {r['unidade']} de *{r['produto_nome']}*"
        if r.get("litros"):
            descricao += f" ({r['litros']:g}L)"
        descricao += f" — R$ {r['valor_unitario']:.2f} cada"
        linhas.append(f"• {descricao}")
        total_geral += Decimal(str(r["valor_total"]))

    texto = f"📦 *Entrada registrada!*\n" + "\n".join(linhas)
    if fornecedor:
        texto += f"\nFornecedor: {fornecedor}"
    texto += f"\n─────────────\nTotal: R$ {total_geral:.2f}"
    return texto


async def _configurar_produto(params: dict) -> str:
    produto = params.get("produto", "").strip()
    perda_pct = _safe_float(params.get("perda_pct", 10.0), default=10.0)

    if not produto:
        return "❌ Informe o nome do produto."
    if not (0 <= perda_pct <= 100):
        return "❌ Percentual de perda deve ser entre 0 e 100."

    await db.upsert_configuracao_produto(produto, perda_pct)

    fator = 1 - perda_pct / 100
    doses_50 = int(50_000 / _ML_POR_DOSE * fator)
    doses_30 = int(30_000 / _ML_POR_DOSE * fator)

    return (
        f"⚙️ *{produto} configurado!*\n"
        f"• Barril de 50L → ~{doses_50} doses úteis\n"
        f"• Barril de 30L → ~{doses_30} doses úteis\n"
        f"• Perda estimada: {perda_pct:.0f}% · Dose: {_ML_POR_DOSE}ml"
    )


async def _remover_cardapio(params: dict) -> str:
    produto = params.get("produto", "").strip()
    if not produto:
        return "❌ Informe o produto a remover do cardápio."

    removido = await db.remover_produto_cardapio(produto)
    if not removido:
        return f"❌ *{produto}* não encontrado no cardápio de hoje."
    return f"✅ *{produto}* removido do cardápio."


async def _remover_entrada(params: dict) -> str:
    produto = (params.get("produto") or "").strip() or None

    removido = await db.remover_ultima_entrada(produto)
    if not removido:
        if produto:
            return f"❌ Nenhuma entrada encontrada para *{produto}*."
        return "❌ Nenhuma entrada registrada."

    desc = f"{removido['quantidade']:g}x {removido['unidade']} de *{removido['produto_nome']}*"
    return f"✅ Entrada removida: {desc} — R$ {removido['valor_total']:.2f}"


async def _desconhecido(params: dict) -> str:
    return (
        "🤔 Não entendi o comando. Exemplos:\n"
        "• _Hoje temos Pilsen a 10 e IPA a 12_\n"
        "• _Coloca 2 cervejas no João_\n"
        "• _Tira 1 cerveja do Pedro_\n"
        "• _Quanto tá o João?_\n"
        "• _Lista as comandas abertas_\n"
        "• _João pagou_ / _Pedro pagou 30_\n"
        "• _Trocar Aquila por Lákila_\n"
        "• _Comprei 2 barris de IPA a 60, do Zé_\n"
        "• _Relatório do dia_"
    )
