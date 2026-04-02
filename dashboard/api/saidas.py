import json
from datetime import date, datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from api._db import query, query_one


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        qs = parse_qs(urlparse(self.path).query)
        today = date.today()
        de = qs.get("de", [today.replace(day=1).isoformat()])[0]
        ate = qs.get("ate", [today.isoformat()])[0]
        produto = qs.get("produto", [None])[0]

        where = "WHERE i.criado_em::date BETWEEN %s AND %s"
        params = [de, ate]

        if produto:
            where += " AND i.produto_nome = %s"
            params.append(produto)

        rows = query(
            f"""
            SELECT i.produto_nome, i.quantidade, i.valor_unitario,
                   i.valor_total, c.nome_cliente, i.criado_em
            FROM itens_comanda i
            JOIN comandas c ON c.id = i.comanda_id
            {where}
            ORDER BY i.criado_em DESC
            """,
            params,
        )

        resumo_row = query_one(
            f"""
            SELECT COUNT(DISTINCT c.id) AS total_comandas,
                   COALESCE(SUM(i.valor_total), 0) AS total_vendido
            FROM itens_comanda i
            JOIN comandas c ON c.id = i.comanda_id
            {where}
            """,
            params,
        )
        total_comandas = int(resumo_row["total_comandas"])
        total_vendido = float(resumo_row["total_vendido"])
        ticket_medio = total_vendido / total_comandas if total_comandas > 0 else 0

        ranking = query(
            f"""
            SELECT i.produto_nome,
                   SUM(i.quantidade) AS qtd,
                   SUM(i.valor_total) AS total
            FROM itens_comanda i
            JOIN comandas c ON c.id = i.comanda_id
            {where}
            GROUP BY i.produto_nome
            ORDER BY SUM(i.valor_total) DESC
            LIMIT 5
            """,
            params,
        )

        data = {
            "rows": rows,
            "resumo": {
                "total_comandas": total_comandas,
                "ticket_medio": ticket_medio,
            },
            "ranking": ranking,
        }

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, default=_serialize).encode())


def _serialize(obj):
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Not serializable: {type(obj)}")
