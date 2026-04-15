"""
Router: recebe uma Action do NLU e executa a lógica de negócio correspondente.
Retorna uma string formatada para ser enviada ao dono via WhatsApp.
"""

import logging
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

from app import database as db

logger = logging.getLogger(__name__)

# Pagamento pendente de confirmação (um por vez, dono opera sozinho)
_pagamento_pendente: dict | None = None

# Fechamento de lote pendente de confirmação (mesmo padrão)
_fechamento_lote_pendente: dict | None = None


def has_pending_payment() -> bool:
    return _pagamento_pendente is not None


def has_pending_fechamento_lote() -> bool:
    return _fechamento_lote_pendente is not None


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
    global _pagamento_pendente, _fechamento_lote_pendente

    intent = action.get("intent", "desconhecido")
    params = action.get("params", {})

    # Se havia fechamento de lote pendente mas chegou outra coisa, cancela silenciosamente
    if _fechamento_lote_pendente is not None:
        _fechamento_lote_pendente = None

    # Verifica se há pagamento pendente de confirmação
    if _pagamento_pendente is not None:
        pendente = _pagamento_pendente
        texto_original = params.get("mensagem", "").strip().lower() if intent == "desconhecido" else ""
        _METODOS = {"credito": "Crédito", "crédito": "Crédito", "debito": "Débito", "débito": "Débito",
                     "pix": "Pix", "dinheiro": "Dinheiro"}
        if intent == "desconhecido" and texto_original in _METODOS:
            _pagamento_pendente = None
            pendente["metodo"] = _METODOS[texto_original]
            return await _executar_pagamento(pendente)
        elif intent == "desconhecido" and texto_original in ("não", "nao", "no", "n", "cancela", "cancelar"):
            _pagamento_pendente = None
            return "❌ Pagamento cancelado."
        else:
            # Qualquer outra mensagem cancela o pagamento pendente
            _pagamento_pendente = None
            # Não era método/não — processa o novo comando normalmente

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
        "remover_cardapio": _remover_cardapio,
        "definir_estoque": _definir_estoque,
        "consultar_estoque": _consultar_estoque,
        "atualizar_estoque": _atualizar_estoque,
        "mapear_produto": _mapear_produto,
        "listar_mapeamentos": _listar_mapeamentos,
        "remover_mapeamento": _remover_mapeamento,
        "gasto_categoria": _gasto_categoria,
        "fechar_lote": _fechar_lote,
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


async def _remover_cardapio(params: dict) -> str:
    produto = params.get("produto", "").strip()
    if not produto:
        return "❌ Informe o produto a remover do cardápio."
    removido = await db.remover_produto_cardapio(produto)
    if not removido:
        return f"ℹ️ *{produto}* não encontrado no cardápio de hoje."
    return f"✅ *{produto}* removido do cardápio."


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
        f"Responda: *crédito*, *débito*, *pix*, *dinheiro* ou *não*."
    )


async def _executar_pagamento(dados: dict) -> str:
    """Executa o pagamento após confirmação."""
    metodo = dados.get("metodo")
    novo_saldo = await db.registrar_pagamento_e_fechar(dados["comanda_id"], dados["valor"], metodo)
    metodo_str = f" ({metodo})" if metodo else ""

    if novo_saldo <= 0:
        return f"✅ *{dados['nome_real']}* pagou R$ {dados['valor']:.2f}{metodo_str}. Comanda fechada! 🎉"
    else:
        return (
            f"✅ *{dados['nome_real']}* pagou R$ {dados['valor']:.2f}{metodo_str}.\n"
            f"Saldo restante: R$ {novo_saldo:.2f}"
        )


def _bloco_baixa_estoque(baixa: dict) -> str:
    """Formata o bloco 📦 Baixa de estoque a partir do retorno de consolidar_baixa_estoque."""
    if baixa.get("ja_fechado"):
        snap = baixa.get("snapshot") or {}
        fechado_em = snap.get("fechado_em")
        quando = fechado_em.strftime("%H:%M") if fechado_em else "?"
        sem_mapa = snap.get("produtos_sem_mapa") or []
        partes = [f"ℹ️ Estoque já foi baixado às {quando}."]
        if sem_mapa:
            partes.append(f"Sem mapeamento: {', '.join(sem_mapa)}")
        return "📦 *Baixa de estoque*\n" + "\n".join(partes)

    baixados = baixa.get("produtos_baixados") or []
    sem_mapa = baixa.get("produtos_sem_mapa") or []
    avisos = baixa.get("avisos_negativos") or []

    if not baixados and not sem_mapa:
        return ""  # nada a baixar (sem mapeamentos ou sem vendas)

    partes = [f"✅ {len(baixados)} produto(s) baixado(s)."] if baixados else []
    if sem_mapa:
        partes.append("⚠️ Sem mapeamento: " + ", ".join(sem_mapa))
    for a in avisos:
        partes.append(
            f"⚠️ *{a['item_nome']}* ficou em {_fmt_qtd(a['qtd'], a['unidade'])}"
        )
    return "📦 *Baixa de estoque*\n" + "\n".join(partes)


async def _relatorio_dia(params: dict) -> str:
    dados = await db.relatorio_dia()

    bloco_baixa = ""
    try:
        baixa = await db.consolidar_baixa_estoque(db._hoje())
        bloco_baixa = _bloco_baixa_estoque(baixa)
    except Exception as exc:
        logger.exception("Erro em consolidar_baixa_estoque: %s", exc)
        bloco_baixa = "⚠️ Erro ao baixar estoque. Relatório parcial."

    por_produto = dados["por_produto"]
    if not por_produto:
        base = "ℹ️ Nenhuma venda registrada hoje."
        return base + ("\n\n" + bloco_baixa if bloco_baixa else "")

    linhas = "\n".join(
        f"• {p['produto_nome']}: {p['quantidade_total']}x — R$ {p['receita']:.2f}"
        for p in por_produto
    )
    base = (
        f"📊 *Relatório do dia*\n"
        f"{linhas}\n"
        f"─────────────\n"
        f"Comandas: {dados['total_comandas']}\n"
        f"Vendido:  R$ {dados['total_vendido']:.2f}\n"
        f"Recebido: R$ {dados['total_recebido']:.2f}\n"
        f"*Pendente: R$ {dados['total_pendente']:.2f}*"
    )
    if bloco_baixa:
        base += "\n\n" + bloco_baixa
    return base


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


def _fmt_qtd(qtd, unidade: str) -> str:
    """Formata quantidade preservando precisão razoável (12.3 → '12.3', 12 → '12')."""
    q = Decimal(qtd)
    if q == q.to_integral_value():
        return f"{int(q)} {unidade}"
    return f"{q.normalize():f} {unidade}"


async def _definir_estoque(params: dict) -> str:
    nome = (params.get("nome") or "").strip()
    unidade = (params.get("unidade") or "").strip()
    categoria = (params.get("categoria") or "").strip().lower()
    qtd_inicial = _safe_float(params.get("qtd_inicial"), default=0.0) or 0.0
    custo_unitario = _safe_float(params.get("custo_unitario"), default=0.0) or 0.0

    if not nome:
        return "❌ Informe o nome do item."
    if unidade not in db._UNIDADES_ESTOQUE:
        return "❌ Unidade inválida. Use: L, ml, kg, g, un."
    if not categoria:
        cats = ", ".join(db._CATEGORIAS_ESTOQUE)
        return f"❌ Informe a categoria. Use: {cats}."
    if categoria not in db._CATEGORIAS_ESTOQUE:
        cats = ", ".join(db._CATEGORIAS_ESTOQUE)
        return f"❌ Categoria inválida. Use: {cats}."
    if qtd_inicial < 0 or custo_unitario < 0:
        return "❌ Quantidade e custo não podem ser negativos."

    item = await db.criar_item_estoque(
        nome, unidade, Decimal(str(qtd_inicial)), Decimal(str(custo_unitario)), categoria,
    )
    if item is None:
        return f"⚠️ *{nome}* já está cadastrado no estoque."

    qtd_str = _fmt_qtd(item["qtd"], item["unidade"])
    return (
        f"✅ *{item['nome']}* cadastrado no estoque.\n"
        f"Categoria: {item['categoria']} · Qtd: {qtd_str} · Custo: R$ {item['custo_unitario']:.2f}"
    )


async def _consultar_estoque(params: dict) -> str:
    nome = (params.get("nome") or "").strip()

    if nome:
        item = await db.buscar_item_estoque(nome)
        if item is None:
            return f"⚠️ *{nome}* não está no estoque."
        qtd = Decimal(item["qtd"])
        valor = qtd * Decimal(item["custo_unitario"])
        alerta = " ⚠️" if qtd < 0 else ""
        return (
            f"📦 *{item['nome']}*{alerta}\n"
            f"Categoria: {item['categoria']}\n"
            f"Qtd: {_fmt_qtd(qtd, item['unidade'])}\n"
            f"Custo: R$ {item['custo_unitario']:.2f} · Valor: R$ {valor:.2f}"
        )

    itens = await db.listar_estoque()
    if not itens:
        return "ℹ️ Estoque vazio. Cadastre itens com _definir estoque_."

    por_cat: dict[str, list[dict]] = {}
    for it in itens:
        por_cat.setdefault(it["categoria"], []).append(it)

    linhas: list[str] = []
    for cat, grupo in por_cat.items():
        linhas.append(f"*{cat.capitalize()}*")
        for it in grupo:
            qtd = Decimal(it["qtd"])
            alerta = " ⚠️" if qtd < 0 else ""
            linhas.append(f"• {it['nome']} — {_fmt_qtd(qtd, it['unidade'])}{alerta}")

    capital = await db.capital_congelado()
    return (
        f"📦 *Estoque ({len(itens)} itens):*\n"
        + "\n".join(linhas)
        + f"\n─────────────\n💰 Capital congelado: R$ {capital:.2f}"
    )


async def _atualizar_estoque(params: dict) -> str:
    nome = (params.get("nome") or "").strip()
    nova_qtd_raw = params.get("nova_qtd")
    nova_categoria = (params.get("nova_categoria") or "").strip().lower() or None
    novo_custo_raw = params.get("novo_custo")

    if not nome:
        return "❌ Informe o nome do item."

    nova_qtd = _safe_float(nova_qtd_raw) if nova_qtd_raw is not None else None
    novo_custo = _safe_float(novo_custo_raw) if novo_custo_raw is not None else None

    if nova_qtd is None and nova_categoria is None and novo_custo is None:
        return "❌ Informe o que atualizar: qtd, categoria ou custo."

    if nova_categoria is not None and nova_categoria not in db._CATEGORIAS_ESTOQUE:
        cats = ", ".join(db._CATEGORIAS_ESTOQUE)
        return f"❌ Categoria inválida. Use: {cats}."

    if novo_custo is not None and novo_custo < 0:
        return "❌ Custo não pode ser negativo."

    resultado = await db.atualizar_estoque(
        nome,
        nova_qtd=Decimal(str(nova_qtd)) if nova_qtd is not None else None,
        nova_categoria=nova_categoria,
        novo_custo=Decimal(str(novo_custo)) if novo_custo is not None else None,
    )
    if resultado is None:
        return f"⚠️ *{nome}* não está no estoque. Cadastre primeiro com _definir estoque_."

    anterior = resultado["anterior"]
    novo = resultado["novo"]
    delta = resultado["delta_qtd"]

    mudancas: list[str] = []
    if nova_qtd is not None:
        sinal = "+" if delta >= 0 else ""
        mudancas.append(
            f"qtd {_fmt_qtd(anterior['qtd'], novo['unidade'])} → "
            f"{_fmt_qtd(novo['qtd'], novo['unidade'])} "
            f"(ajuste {sinal}{_fmt_qtd(delta, novo['unidade'])} no lote)"
        )
    if nova_categoria is not None:
        mudancas.append(f"categoria {anterior['categoria']} → {novo['categoria']}")
    if novo_custo is not None:
        mudancas.append(
            f"custo R$ {Decimal(anterior['custo_unitario']):.2f} → R$ {Decimal(novo['custo_unitario']):.2f}"
        )

    alerta = ""
    if Decimal(novo["qtd"]) < 0:
        alerta = "\n⚠️ Qtd ficou negativa — confira o registro."

    return f"✅ *{novo['nome']}*: " + "; ".join(mudancas) + "." + alerta


async def _mapear_produto(params: dict) -> str:
    produto = (params.get("produto") or "").strip()
    item_estoque = (params.get("item_estoque") or "").strip()
    consumo = _safe_float(params.get("consumo"))

    if not produto or not item_estoque:
        return "❌ Informe o produto e o item de estoque."
    if consumo is None or consumo <= 0:
        return "❌ Informe o consumo por unidade (maior que zero)."

    existe_cardapio = await db.produto_ja_existiu_cardapio(produto)
    if not existe_cardapio:
        return (
            f"⚠️ Produto *{produto}* não existe no cardápio. "
            f"Defina primeiro com _hoje tem {produto} a ..._."
        )

    item = await db.buscar_item_estoque(item_estoque)
    if item is None:
        return (
            f"⚠️ Item *{item_estoque}* não está no estoque. "
            f"Cadastre primeiro com _definir estoque_."
        )

    resultado = await db.mapear_produto(produto, item_estoque, Decimal(str(consumo)))
    if resultado is None:
        return f"⚠️ Item *{item_estoque}* não está no estoque."

    return (
        f"✅ *{resultado['produto_nome']}* → "
        f"{_fmt_qtd(resultado['consumo_por_unidade'], resultado['unidade'])} "
        f"de *{resultado['item_nome']}* por unidade vendida."
    )


async def _listar_mapeamentos(params: dict) -> str:
    mapeamentos = await db.listar_mapeamentos()
    if not mapeamentos:
        return "ℹ️ Nenhum mapeamento cadastrado."

    linhas = "\n".join(
        f"• *{m['produto_nome']}* → {_fmt_qtd(m['consumo_por_unidade'], m['unidade'])} de *{m['item_nome']}*"
        for m in mapeamentos
    )
    return f"🔗 *Mapeamentos ({len(mapeamentos)}):*\n{linhas}"


async def _remover_mapeamento(params: dict) -> str:
    produto = (params.get("produto") or "").strip()
    if not produto:
        return "❌ Informe o produto."

    removido = await db.remover_mapeamento(produto)
    if not removido:
        return f"⚠️ Mapeamento de *{produto}* não encontrado."
    return f"✅ Mapeamento de *{produto}* removido."


def _parse_data(valor) -> date | None:
    if not valor:
        return None
    try:
        return date.fromisoformat(str(valor).strip())
    except ValueError:
        return None


async def _gasto_categoria(params: dict) -> str:
    de = _parse_data(params.get("de"))
    ate = _parse_data(params.get("ate"))
    filtro_categoria = (params.get("categoria") or "").strip().lower() or None

    hoje = db._hoje()
    if de is None and ate is None:
        ate = hoje
        de = hoje - timedelta(days=6)
        rotulo = "últimos 7 dias"
    else:
        if de is None:
            de = hoje - timedelta(days=6)
        if ate is None:
            ate = hoje
        if de > ate:
            de, ate = ate, de
        rotulo = f"{de.strftime('%d/%m')} a {ate.strftime('%d/%m')}"

    dados = await db.gasto_por_categoria(de, ate)

    if filtro_categoria:
        dados = [d for d in dados if d["categoria"].lower() == filtro_categoria]

    if not dados:
        return f"ℹ️ Nenhuma entrada registrada em *{rotulo}*."

    dados_ordenados = [d for d in dados if d["categoria"] != "sem categoria"]
    sem_cat = [d for d in dados if d["categoria"] == "sem categoria"]

    total_geral = sum((Decimal(d["total"]) for d in dados), Decimal(0))

    linhas = [
        f"• {d['categoria'].capitalize()}: R$ {Decimal(d['total']):.2f} ({d['qtd_entradas']}x)"
        for d in dados_ordenados
    ]
    for d in sem_cat:
        linhas.append(
            f"• _Sem categoria_: R$ {Decimal(d['total']):.2f} ({d['qtd_entradas']}x)"
        )

    return (
        f"📊 *Gasto por categoria ({rotulo}):*\n"
        + "\n".join(linhas)
        + f"\n─────────────\nTotal: R$ {total_geral:.2f}"
    )


def _formatar_fechamento_lote(r: dict) -> str:
    """Formata a resposta de um lote efetivamente fechado."""
    unidade = r["unidade"]
    linhas = [
        f"✅ Lote de *{r['item_nome']}* fechado.",
        f"Comprado: {_fmt_qtd(r['qtd_comprada'], unidade)}",
        f"Vendido: {_fmt_qtd(r['qtd_vendida'], unidade)}",
    ]
    if Decimal(r["qtd_ajustes"]) != 0:
        linhas.append(f"Ajustes: {_fmt_qtd(r['qtd_ajustes'], unidade)}")
    linhas.append(f"Restante: {_fmt_qtd(r['qtd_restante'], unidade)}")

    if r["sem_vendas"]:
        linhas.append(f"Perda: {_fmt_qtd(r['perda'], unidade)} _(sem vendas — % não calculável)_")
    else:
        pct = Decimal(r["perda_pct"]) if r["perda_pct"] is not None else None
        pct_str = f" ({pct:.1f}%)" if pct is not None else ""
        linhas.append(f"Perda: {_fmt_qtd(r['perda'], unidade)}{pct_str}")

    return "\n".join(linhas)


async def _fechar_lote(params: dict) -> str:
    global _fechamento_lote_pendente

    item = (params.get("item") or "").strip()
    forcar = bool(params.get("forcar"))

    if not item:
        return "❌ Informe o item cujo lote quer fechar."

    resultado = await db.fechar_lote(item, forcar=forcar)
    if resultado is None:
        return f"⚠️ *{item}* não está no estoque."

    if resultado.get("nenhum_lote_aberto"):
        return f"ℹ️ *{resultado['item_nome']}* não tem lote aberto."

    if resultado.get("confirmar"):
        _fechamento_lote_pendente = {"item_nome": resultado["item_nome"]}
        unidade = resultado["unidade"]
        qtd_atual = resultado["qtd_atual"]
        return (
            f"⚠️ Ainda há {_fmt_qtd(qtd_atual, unidade)} cadastrados em "
            f"*{resultado['item_nome']}*. Zerar mesmo assim? (sim/não)"
        )

    return _formatar_fechamento_lote(resultado)


async def resolver_fechamento_lote_pendente(text: str) -> str:
    """Chamado por main.py quando há pendência e a mensagem é sim/não."""
    global _fechamento_lote_pendente
    pendente = _fechamento_lote_pendente
    _fechamento_lote_pendente = None

    if pendente is None:
        return ""

    resposta = (text or "").strip().lower()
    if resposta in ("não", "nao", "no", "n", "cancela", "cancelar"):
        return f"❌ Fechamento de *{pendente['item_nome']}* cancelado."

    resultado = await db.fechar_lote(pendente["item_nome"], forcar=True)
    if resultado is None:
        return f"⚠️ *{pendente['item_nome']}* não está mais no estoque."
    if resultado.get("nenhum_lote_aberto"):
        return f"ℹ️ *{resultado['item_nome']}* não tem lote aberto."
    return _formatar_fechamento_lote(resultado)


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
