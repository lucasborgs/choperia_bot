"""
Router: recebe uma Action do NLU e executa a lógica de negócio correspondente.
Retorna uma string formatada para ser enviada ao dono via WhatsApp.
"""

import logging
from decimal import Decimal
from uuid import UUID

from app import database as db

logger = logging.getLogger(__name__)

# Pagamento pendente de confirmação (um por vez, dono opera sozinho)
_pagamento_pendente: dict | None = None


def has_pending_payment() -> bool:
    return _pagamento_pendente is not None


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
    global _pagamento_pendente

    intent = action.get("intent", "desconhecido")
    params = action.get("params", {})

    # Verifica se há pagamento pendente de confirmação
    if _pagamento_pendente is not None:
        pendente = _pagamento_pendente
        texto_original = params.get("mensagem", "").strip().lower() if intent == "desconhecido" else ""
        # Aceita "sim", "s", "yes", "confirma" como confirmação
        if intent == "desconhecido" and texto_original in ("sim", "s", "yes", "confirma", "confirmar"):
            _pagamento_pendente = None
            return await _executar_pagamento(pendente)
        else:
            # Qualquer outra mensagem cancela o pagamento pendente
            _pagamento_pendente = None
            if intent == "desconhecido" and texto_original in ("não", "nao", "no", "n", "cancela", "cancelar"):
                return "❌ Pagamento cancelado."
            # Não era sim/não — processa o novo comando normalmente

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
        "remover_entrada": _remover_entrada,
        "desconhecido": _desconhecido,
    }

    handler = handlers.get(intent, _desconhecido)
    try:
        return await handler(params)
    except Exception as exc:
        _pagamento_pendente = None  # limpa estado pendente em caso de erro
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

    # Resolve preços no cardápio do dia (com fallback por similaridade)
    itens_com_preco = []
    nao_encontrados = []
    for item in itens_req:
        produto = item.get("produto", "").strip()
        quantidade = _safe_int(item.get("quantidade", 1), default=1)
        resultado = await db.buscar_preco_produto(produto)
        if resultado is None:
            nao_encontrados.append(produto)
        else:
            itens_com_preco.append(
                {"produto": resultado["nome_real"], "quantidade": quantidade, "valor_unitario": resultado["preco"]}
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
    # Filtra comandas sem itens e sem saldo (vazias/zeradas)
    comandas = [c for c in comandas if c["total_consumido"] > 0 or c["saldo_devedor"] > 0]
    if not comandas:
        return "ℹ️ Nenhuma comanda aberta no momento."

    # Ordena por total consumido (quem bebeu mais primeiro)
    comandas.sort(key=lambda c: c["total_consumido"], reverse=True)

    linhas = "\n".join(
        f"• *{c['nome_cliente']}* — R$ {c['total_consumido']:.2f} consumido · R$ {c['saldo_devedor']:.2f} a pagar"
        for c in comandas
    )
    return f"📋 *Comandas abertas ({len(comandas)}):*\n{linhas}"


async def _pagar_conta(params: dict) -> str:
    global _pagamento_pendente

    cliente = params.get("cliente", "").strip()
    valor_raw = params.get("valor")
    produto_raw = params.get("produto")
    quantidade_raw = params.get("quantidade")

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

    if valor_raw is not None:
        parsed = _safe_float(valor_raw)
        if parsed is None:
            return f"❌ Valor inválido: *{valor_raw}*"
        valor = Decimal(str(parsed))
    elif produto_raw and quantidade_raw:
        preco_info = await db.buscar_preco_produto(produto_raw)
        if preco_info is None:
            return f"❌ Produto *{produto_raw}* não encontrado no cardápio de hoje."
        qtd = int(quantidade_raw)
        valor = Decimal(str(float(preco_info["preco"]) * qtd))
    else:
        valor = saldo_devedor

    if valor <= 0:
        await db.fechar_comanda(comanda_id)
        return f"✅ Comanda de *{nome_real}* fechada (saldo zerado)."

    # Guarda pagamento pendente e pede confirmação
    _pagamento_pendente = {
        "comanda_id": comanda_id,
        "nome_real": nome_real,
        "valor": valor,
        "saldo_devedor": saldo_devedor,
    }

    fecha = " e *fechar comanda*" if valor >= saldo_devedor else ""
    return (
        f"💰 Registrar pagamento de *R$ {valor:.2f}* para *{nome_real}*{fecha}?\n"
        f"Responda *sim* ou *não*."
    )


async def _executar_pagamento(dados: dict) -> str:
    """Executa o pagamento após confirmação."""
    novo_saldo = await db.registrar_pagamento_e_fechar(dados["comanda_id"], dados["valor"])

    if novo_saldo <= 0:
        return f"✅ *{dados['nome_real']}* pagou R$ {dados['valor']:.2f}. Comanda fechada! 🎉"
    else:
        return (
            f"✅ *{dados['nome_real']}* pagou R$ {dados['valor']:.2f}.\n"
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
