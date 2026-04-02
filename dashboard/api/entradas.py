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

        rows = query(
            """
            SELECT produto_nome, unidade, quantidade, litros,
                   valor_unitario, valor_total, fornecedor, criado_em
            FROM entradas
            WHERE criado_em::date BETWEEN %s AND %s
            ORDER BY criado_em DESC
            """,
            (de, ate),
        )

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
