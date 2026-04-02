import json
from datetime import date, datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler
from api._db import query


_ML_POR_DOSE = 400


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        entradas = query(
            """
            SELECT produto_nome,
                   SUM(quantidade)                       AS qtd_comprada,
                   SUM(COALESCE(litros * quantidade, 0)) AS litros_comprados,
                   SUM(valor_total)                      AS custo_total
            FROM entradas
            GROUP BY produto_nome
            ORDER BY produto_nome
            """
        )
        saidas = query(
            """
            SELECT produto_nome,
                   SUM(quantidade)  AS qtd_vendida,
                   SUM(valor_total) AS receita_total
            FROM itens_comanda
            GROUP BY produto_nome
            """
        )
        configs = query("SELECT nome, perda_pct FROM configuracao_produto")

        configs_map = {r["nome"].lower(): float(r["perda_pct"]) for r in configs}
        saidas_map = {r["produto_nome"].lower(): r for r in saidas}

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

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(resultado, default=_serialize).encode())


def _serialize(obj):
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Not serializable: {type(obj)}")
