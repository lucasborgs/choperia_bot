import json
from datetime import date, datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from api._db import query_one


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        qs = parse_qs(urlparse(self.path).query)
        today = date.today()
        de = qs.get("de", [today.replace(day=1).isoformat()])[0]
        ate = qs.get("ate", [today.isoformat()])[0]

        custo = query_one(
            "SELECT COALESCE(SUM(valor_total), 0) AS v FROM entradas WHERE criado_em::date BETWEEN %s AND %s",
            (de, ate),
        )["v"]

        vendido = query_one(
            """
            SELECT COALESCE(SUM(i.valor_total), 0) AS v
            FROM itens_comanda i
            JOIN comandas c ON c.id = i.comanda_id
            WHERE c.data_criacao::date BETWEEN %s AND %s
            """,
            (de, ate),
        )["v"]

        recebido = query_one(
            "SELECT COALESCE(SUM(valor), 0) AS v FROM pagamentos WHERE criado_em::date BETWEEN %s AND %s",
            (de, ate),
        )["v"]

        data = {
            "custo_total": float(custo),
            "vendido_total": float(vendido),
            "recebido_total": float(recebido),
            "lucro_bruto": float(vendido) - float(custo),
            "a_receber": float(vendido) - float(recebido),
        }

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())
