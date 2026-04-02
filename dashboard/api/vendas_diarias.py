import json
from datetime import date, datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from api._db import query


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        qs = parse_qs(urlparse(self.path).query)
        today = date.today()
        de = qs.get("de", [today.replace(day=1).isoformat()])[0]
        ate = qs.get("ate", [today.isoformat()])[0]
        produto = qs.get("produto", [None])[0]

        sql = """
            SELECT i.criado_em::date AS dia, SUM(i.valor_total) AS total
            FROM itens_comanda i
            JOIN comandas c ON c.id = i.comanda_id
            WHERE i.criado_em::date BETWEEN %s AND %s
        """
        params = [de, ate]

        if produto:
            sql += " AND i.produto_nome = %s"
            params.append(produto)

        sql += " GROUP BY dia ORDER BY dia"

        rows = query(sql, params)

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(rows, default=_serialize).encode())


def _serialize(obj):
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Not serializable: {type(obj)}")
