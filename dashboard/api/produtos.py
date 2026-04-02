import json
from http.server import BaseHTTPRequestHandler
from api._db import query


class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        produtos = query(
            """
            SELECT DISTINCT nome FROM (
                SELECT produto_nome AS nome FROM entradas
                UNION
                SELECT produto_nome AS nome FROM itens_comanda
            ) t
            ORDER BY nome
            """
        )
        unidades = query(
            "SELECT DISTINCT unidade FROM entradas WHERE unidade IS NOT NULL ORDER BY unidade"
        )

        data = {
            "produtos": [r["nome"] for r in produtos],
            "unidades": [r["unidade"] for r in unidades],
        }

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())
