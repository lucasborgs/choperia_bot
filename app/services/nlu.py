"""
NLU via Groq (LLaMA): recebe texto livre e retorna uma Action estruturada.

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

from groq import AsyncGroq

from app.config import settings

logger = logging.getLogger(__name__)

_MODEL = "llama-3.3-70b-versatile"

_client: AsyncGroq | None = None


def _get_client() -> AsyncGroq:
    global _client
    if _client is None:
        _client = AsyncGroq(api_key=settings.GROQ_API_KEY, timeout=30.0)
    return _client

_SYSTEM_PROMPT = """Você é o assistente de um bar/choperia chamado Choperia Bot.
O dono envia mensagens de voz ou texto pelo WhatsApp para controlar as comandas.

Sua tarefa é extrair a intenção e os parâmetros da mensagem e retornar SOMENTE um JSON válido, sem markdown, sem explicações.

Intents disponíveis:

1. definir_cardapio
   - Quando: o dono informa os produtos e preços do dia
   - Params: {"itens": [{"produto": str, "preco": float}]}
   - Exemplo: "Hoje temos frango a 25 e linguiça a 18"

2. adicionar_itens
   - Quando: o dono quer lançar itens na comanda de um cliente
   - Params: {"cliente": str, "itens": [{"produto": str, "quantidade": int}]}
   - Exemplo: "Coloca 2 cervejas e 1 frango no João"

3. remover_item
   - Quando: o dono quer remover item(ns) de uma comanda
   - Params: {"cliente": str, "produto": str, "quantidade": int}
   - Exemplo: "Tira 1 cerveja do Pedro"

4. consultar_comanda
   - Quando: o dono quer ver o total/itens de um cliente
   - Params: {"cliente": str}
   - Exemplo: "Quanto tá o João?" / "Ver comanda da Ana"

5. listar_comandas
   - Quando: o dono quer ver todas as comandas abertas
   - Params: {}
   - Exemplo: "Quais comandas estão abertas?" / "Lista tudo"

6. pagar_conta
   - Quando: o dono registra pagamento de um cliente
   - Params: {"cliente": str, "valor": float | null}
   - Se valor não mencionado, use null (pagamento total)
   - Exemplo: "João pagou" / "Fecha a conta da Ana" / "Pedro pagou 30"

7. relatorio_dia
   - Quando: o dono pede resumo/relatório do dia
   - Params: {}
   - Exemplo: "Relatório do dia" / "Como foi hoje?"

8. renomear_cliente
   - Quando: o dono quer corrigir o nome de um cliente na comanda
   - Params: {"nome_atual": str, "nome_novo": str}
   - Exemplo: "não é Aquila, é Lákila" / "trocar Aquila por Lákila" / "o nome certo é Lákila, não Aquila"

9. registrar_entrada
   - Quando: o dono registra uma compra de insumos (gelo, barris, etc.)
   - Params: {"itens": [{"produto": str, "unidade": str, "quantidade": float, "litros": float|null, "preco_unitario": float}], "fornecedor": str|null}
   - "litros" só quando informado explicitamente (ex: "barril de 30L" → litros=30, "barril de 50L" → litros=50)
   - Exemplos: "comprei 2 sacos de gelo a 20" / "comprei 1 barril de 50L de IPA a 60, do Zé"

10. configurar_produto
    - Quando: o dono define o percentual de perda de um chopp (para cálculo de doses)
    - Params: {"produto": str, "perda_pct": float}
    - Dose padrão é sempre 400ml (copo de chopp)
    - Exemplo: "configura IPA com 8% de perda" / "perda do Pilsen é 12%"

11. desconhecido
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

    logger.info("NLU action: %s", action)
    return action
