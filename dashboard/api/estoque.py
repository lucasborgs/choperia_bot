import json
from datetime import date, datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from api._db import query


_ML_POR_DOSE = 400


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        qs = parse_qs(urlparse(self.path).query)
        produto = qs.get("produto", [None])[0]

        ent_sql = """
            SELECT produto_nome,
                   SUM(quantidade)                       AS qtd_comprada,
                   SUM(COALESCE(litros * quantidade, 0)) AS litros_comprados,
                   SUM(valor_total)                      AS custo_total
            FROM entradas
        """
        ent_params = []
        if produto:
            ent_sql += " WHERE produto_nome = %s"
            ent_params.append(produto)
        ent_sql += " GROUP BY produto_nome ORDER BY produto_nome"

        sai_sql = """
            SELECT produto_nome,
                   SUM(quantidade)  AS qtd_vendida,
                   SUM(valor_total) AS receita_total
            FROM itens_comanda
        """
        sai_params = []
        if produto:
            sai_sql += " WHERE produto_nome = %s"
            sai_params.append(produto)
        sai_sql += " GROUP BY produto_nome"

        entradas = query(ent_sql, ent_params or None)
        saidas = query(sai_sql, sai_params or None)
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

            unid_compradas = int(litros * 1000 / _ML_POR_DOSE * fator) if litros > 0 else None
            unid_vendidas = int(float(s.get("qtd_vendida", 0))) if litros > 0 else None

            custo = float(e["custo_total"])
            receita = float(s.get("receita_total", 0))
            margem_pct = round((receita - custo) / receita * 100, 1) if receita > 0 else None

            resultado.append({
                "produto": nome,
                "qtd_comprada": float(e["qtd_comprada"]),
                "litros_comprados": litros,
                "custo_total": custo,
                "qtd_vendida": float(s.get("qtd_vendida", 0)),
                "receita_total": receita,
                "unid_compradas": unid_compradas,
                "unid_vendidas": unid_vendidas,
                "perda_pct": perda if litros > 0 else None,
                "margem_pct": margem_pct,
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
