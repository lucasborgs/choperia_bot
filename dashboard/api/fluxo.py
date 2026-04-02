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
        produto = qs.get("produto", [None])[0]

        # Custo (entradas)
        custo_sql = "SELECT COALESCE(SUM(valor_total), 0) AS v FROM entradas WHERE criado_em::date BETWEEN %s AND %s"
        custo_params = [de, ate]
        if produto:
            custo_sql += " AND produto_nome = %s"
            custo_params.append(produto)
        custo = query_one(custo_sql, custo_params)["v"]

        # Vendido (itens_comanda)
        vendido_sql = """
            SELECT COALESCE(SUM(i.valor_total), 0) AS v
            FROM itens_comanda i
            JOIN comandas c ON c.id = i.comanda_id
            WHERE c.data_criacao::date BETWEEN %s AND %s
        """
        vendido_params = [de, ate]
        if produto:
            vendido_sql += " AND i.produto_nome = %s"
            vendido_params.append(produto)
        vendido = query_one(vendido_sql, vendido_params)["v"]

        # Recebido (pagamentos) — sem filtro de produto
        recebido = query_one(
            "SELECT COALESCE(SUM(valor), 0) AS v FROM pagamentos WHERE criado_em::date BETWEEN %s AND %s",
            (de, ate),
        )["v"]

        data = {
            "custo_total": float(custo),
            "vendido_total": float(vendido),
            "recebido_total": float(recebido),
            "resultado_periodo": float(vendido) - float(custo),
            "a_receber": float(vendido) - float(recebido),
            "filtro_produto": produto is not None,
        }

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())
