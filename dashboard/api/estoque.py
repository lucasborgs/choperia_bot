import json
from datetime import date, datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from api._db import query


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        qs = parse_qs(urlparse(self.path).query)
        nome = qs.get("nome", [None])[0]

        sql = """
            SELECT id, nome, unidade, qtd, custo_unitario, categoria,
                   (qtd * custo_unitario) AS valor
            FROM itens_estoque
        """
        params = None
        if nome:
            sql += " WHERE lower(nome) = lower(%s)"
            params = (nome,)
        sql += " ORDER BY categoria, lower(nome)"

        rows = query(sql, params)

        resultado = [
            {
                "id": str(r["id"]),
                "nome": r["nome"],
                "unidade": r["unidade"],
                "qtd": float(r["qtd"]),
                "custo_unitario": float(r["custo_unitario"]),
                "categoria": r["categoria"],
                "valor": float(r["valor"]),
            }
            for r in rows
        ]

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
