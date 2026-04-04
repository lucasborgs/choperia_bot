"""
NLU via OpenAI (GPT-4o mini): recebe texto livre e retorna uma Action estruturada.

Intents suportadas:
  - definir_cardapio   params: itens=[{produto, preco}]
  - adicionar_itens    params: cliente, itens=[{produto, quantidade}]
  - remover_item       params: cliente, produto, quantidade
  - consultar_comanda  params: cliente
  - listar_comandas    (sem params)
  - pagar_conta        params: cliente, valor (opcional – se omitido paga tudo)
  - relatorio_dia      (sem params)
  - desconhecido       params: mensagem (texto original)
"""

import json
import logging
import re

from openai import AsyncOpenAI

from app.config import settings

logger = logging.getLogger(__name__)

_MODEL = "gpt-4o-mini"

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY, timeout=30.0)
    return _client

_SYSTEM_PROMPT = """Você é o assistente de um bar/choperia chamado Cervejaria Cabrunco.
O dono envia mensagens de voz ou texto pelo WhatsApp para controlar as comandas.
Ele fala de forma informal e variada. Foque no SIGNIFICADO da mensagem,
não na estrutura exata da frase. Uma mesma intenção pode ser expressa
de muitas formas diferentes.

Sua tarefa é extrair a intenção e os parâmetros da mensagem e retornar SOMENTE um JSON válido, sem markdown, sem explicações.

Intents disponíveis:

1. definir_cardapio
   - Por que: é necessário atualizar diariamente o cardápio com os preços atuais
   - Quando: o dono informa os produtos e preços do dia
   - IMPORTANTE: essa intent é para DEFINIR o cardápio do dia, ou seja, informar os preços. Se o dono está PERGUNTANDO (não informando preços), use consultar_cardapio.
   - Params: {"itens": [{"produto": str, "preco": float}]}
   - Exemplo: "Hoje temos Pilsen a 9 e Session IPA a 15" / "Coloca no cardápio: Pilsen 10, IPA 12 e Stout a 18" / "O cardápio de hoje é: Pilsen por 10, APA por 12 e Weiss por 14"

2. adicionar_itens
   - Por que: os clientes vão pedindo itens ao longo do dia, e o dono precisa lançar na comanda para controlar o consumo e calcular a conta no final
   - Quando: o dono quer lançar itens na comanda de um cliente
   - Params: {"cliente": str, "itens": [{"produto": str, "quantidade": int}]}
   - Se o dono usar "mais um", "outra", "desce outro", considere quantidade 1 para o item mencionado
   - Exemplo: "Coloca 2 IPA e 1 Pilsen no João" / "O Pedro pediu 3 chopps IPA" / "Lança 1 Pilsen e 2 IPA para a Ana" / "Anota 1 Pilsen pro João e 2 pro Pedro" / "O João tomou 2 Pilsen" / "A Ana comprou 3 IPA" / "Manda mais uma IPA pro João" / "Desce outro Pilsen pra Ana" / "Mais 2 pro Carlos"

3. remover_item
   - Por que: às vezes o dono precisa corrigir um lançamento errado, ou o cliente devolve um item
   - Quando: o dono quer remover item(ns) de uma comanda
   - Params: {"cliente": str, "produto": str, "quantidade": int}
   - Exemplo: "Tira 1 cerveja do Pedro" / "Remove 2 IPA da comanda do João" / "O Pedro devolveu 1 Pilsen" / "Corrige a comanda da Ana, tira 1 IPA que eu lancei errado"

4. consultar_comanda
   - Por que: o dono precisa consultar o total/itens consumidos por um cliente para informar o cliente ou calcular a conta
   - Quando: o dono quer ver o total/itens de um cliente
   - Params: {"cliente": str}
   - Exemplo: "Quanto tá o João?" / "Ver comanda da Ana" / "O que o Pedro consumiu até agora?" / "Quanto o João deve?" / "Tem alguma coisa na comanda do Lucas?"

5. listar_comandas
   - Por que: o dono quer ter uma visão geral de todas as comandas abertas, para controlar melhor o fluxo do bar
   - Quando: o dono quer ver todas as comandas abertas
   - Params: {}
   - Exemplo: "Quais comandas estão abertas?" / "Lista tudo" / "Quem tá devendo algo ainda?" / "Me mostra as comandas abertas" / "Quem não fechou a conta ainda?" / "Quem tá bebendo mais hoje?" / "Quem deve mais?" / "Qual a maior comanda?"

6. pagar_conta
   - Por que: quando um cliente vai pagar, o dono precisa registrar o pagamento para fechar a comanda e calcular o total do dia
   - Quando: o dono registra pagamento de um cliente
   - Params: {"cliente": str, "valor": float | null}
   - Se valor não mencionado, use null (pagamento total)
   - Exemplo: "João pagou tudo" / "Fecha a conta da Ana" / "Pedro pagou 30" / "Carlos pagou tudo que deve" / "O Lucas pagou a conta dele, fecha lá"/ "A Maria pagou metade"

7. relatorio_dia
   - Por que: no final do dia de vendas, o dono quer um resumo do que aconteceu (total vendido, clientes mais frequentes, etc.) para ter insights e planejar melhor os próximos dias
   - Quando: o dono pede resumo/relatório do dia
   - Params: {}
   - Exemplo: "Relatório do dia" / "Como foi hoje?"

8. renomear_cliente
   - Por que: às vezes o dono lança um cliente com nome errado, e precisa corrigir para não ficar confuso nas próximas vezes
   - Quando: o dono quer corrigir o nome de um cliente na comanda
   - Params: {"nome_atual": str, "nome_novo": str}
   - Exemplo: "não é Aquila, é Lákila" / "trocar Aquila por Lákila" / "o nome certo é Lákila, não Aquila" / "Substitui Luca por Lucas"

9. registrar_entrada
   - Por que: o dono precisa registrar as compras de insumos para controle financeiro e cálculo do custo do dia
   - Quando: o dono registra uma compra de insumos (gelo, barris de chopp, etc.)
   - Params: {"itens": [{"produto": str, "unidade": str, "quantidade": float, "litros": float|null, "preco_unitario": float}], "fornecedor": str|null}
   - "litros" só quando informado explicitamente (ex: "barril de 30L" → litros=30, "barril de 50L" → litros=50)
   - IMPORTANTE: "preco_unitario" deve ser SEMPRE o valor de UMA unidade. Se o dono informar o valor total (ex: "comprei 4 sacos de gelo por 60 reais"), calcule: preco_unitario = 60 / 4 = 15.
   - Exemplos: "comprei 2 sacos de gelo a 20 cada" / "comprei 1 barril de 50L de IPA a 60, do Zé" / "gastei 45 reais em 3 sacos de gelo" / "Anota que comprei 1 barril de 30L de Pilsen por 40, do fornecedor João" / "Entrada: 2 barris de 50L de IPA a 60 cada, fornecedor Zé"

10. consultar_cardapio
    - Por que: o dono precisa consultar os preços do cardápio para informar os clientes ou atualizar os lançamentos
    - Quando: o dono pergunta o que tem no cardápio hoje, quais produtos estão disponíveis
    - Params: {}
    - Exemplo: "Qual o cardápio de hoje?" / "O que tem hoje?" / "Quais cervejas temos?" / "Cardápio do dia" / "O que tem no cardápio hoje?" / "Me mostra o cardápio de hoje" / "Tem IPA hoje?" / "Qual o preço da Pilsen?" / "Quanto tá a IPA?"
    - IMPORTANTE: NÃO confundir com definir_cardapio. Se o dono está PERGUNTANDO (não informando preços), use consultar_cardapio.

11. remover_entrada
    - Por que: às vezes o dono registra uma entrada de compra errada (ex: compra de gelo registrada como compra de cerveja), e precisa remover para não distorcer os relatórios e controle financeiro
    - Quando: o dono quer apagar/cancelar uma entrada de compra registrada errado
    - Params: {"produto": str | null}
    - IMPORTANTE: não confundir com remover_item, que é para remover itens da comanda dos clientes. Essa intent é para remover entradas de compra de insumos (gelo, barris, etc.) do controle financeiro.
    - Se produto não mencionado, use null (remove a última entrada registrada)
    - Exemplo: "Apaga a última compra" / "Remove a entrada do gelo" / "Cancela a última compra" / "Tira a entrada do barril de IPA que eu lancei errado" / "Remove a última entrada registrada" / "Apaga a última compra que registrei" / "Cancela a última entrada que lancei" / "Tira a entrada do gelo que eu lancei errado"

12. desconhecido
    - Quando nenhuma outra intent se aplica
    - Params: {"mensagem": str}

Retorne APENAS JSON no formato:
{"intent": "<nome>", "params": {...}}"""


async def extract_action(text: str) -> dict:
    """Retorna dict com 'intent' e 'params' extraídos do texto."""
    client = _get_client()
    response = await client.chat.completions.create(
        model=_MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": text},
        ],
        temperature=0,
        max_tokens=256,
    )
    raw = response.choices[0].message.content.strip()

    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        action = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("NLU retornou JSON inválido: %s", raw)
        action = {"intent": "desconhecido", "params": {"mensagem": text}}

    _normalizar_nomes(action)
    logger.info("NLU action: %s", action)
    return action


def _normalizar_nomes(action: dict) -> None:
    """Normaliza nomes de produtos para Title Case (Pilsen, Session Ipa → Session IPA)."""
    params = action.get("params", {})
    intent = action.get("intent", "")

    if intent == "definir_cardapio":
        for item in params.get("itens", []):
            if "produto" in item:
                item["produto"] = _title(item["produto"])

    elif intent == "adicionar_itens":
        for item in params.get("itens", []):
            if "produto" in item:
                item["produto"] = _title(item["produto"])

    elif intent in ("remover_item", "remover_cardapio", "configurar_produto"):
        if "produto" in params:
            params["produto"] = _title(params["produto"])

    elif intent == "remover_entrada":
        if params.get("produto"):
            params["produto"] = _title(params["produto"])


def _title(nome: str) -> str:
    """Title case que preserva siglas comuns (IPA, APA, etc.)."""
    siglas = {"ipa", "apa", "ipa's"}
    partes = nome.strip().split()
    resultado = []
    for p in partes:
        if p.lower() in siglas:
            resultado.append(p.upper())
        else:
            resultado.append(p.capitalize())
    return " ".join(resultado)
