#!/usr/bin/env python3
"""
Autorização única (OAuth 2.0) para o bot de engajamento do canal.

Rode UMA vez, na SUA máquina (a que tem navegador — pode ser seu notebook, NÃO
precisa ser o servidor). O script abre a tela de consentimento do Google; você
autoriza com a conta dona do canal que faz a live 24h e ele imprime o
REFRESH TOKEN. Copie CLIENT_ID / CLIENT_SECRET / REFRESH_TOKEN para o .env do
servidor (variáveis YOUTUBE_OAUTH_*). O refresh token NÃO é preso à máquina —
funciona em qualquer lugar depois.

Uso:
    python3 get_refresh_token.py

Pré-requisitos:
    - Python 3.8+ (só biblioteca padrão, sem pip install).
    - Um "OAuth client ID" do tipo "Desktop app" criado no Google Cloud Console,
      no MESMO projeto onde a "YouTube Data API v3" está habilitada.
    - A tela de consentimento (Google Auth Platform) com o escopo
      .../auth/youtube.force-ssl e publicação em "In production" (senão o
      refresh token é revogado em 7 dias).
"""

import base64
import hashlib
import http.server
import json
import os
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
# force-ssl cobre liveChatMessages.insert, liveBroadcasts.list e videos.list.
SCOPE = "https://www.googleapis.com/auth/youtube.force-ssl"


def main():
    client_id = input("Client ID: ").strip()
    client_secret = input("Client secret: ").strip()
    if not client_id or not client_secret:
        sys.exit("Client ID e Client secret sao obrigatorios.")

    # PKCE (S256) — exigido pelo fluxo de app desktop atual.
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode()
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    state = secrets.token_urlsafe(16)

    # Servidor loopback efemero. O fluxo "OOB" (copiar/colar codigo) foi
    # descontinuado pelo Google; hoje o redirect precisa ser 127.0.0.1:<porta>.
    holder = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            holder.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                "<h2>Pode fechar esta aba e voltar ao terminal.</h2>".encode("utf-8")
            )

        def log_message(self, *a):
            pass

    # Porta fixa por padrao (facilita o caso de tunel SSH: basta
    # `ssh -L 8765:localhost:8765 ...`). Use OAUTH_PORT=0 para porta efemera.
    port = int(os.environ.get("OAUTH_PORT", "8765"))
    httpd = http.server.HTTPServer(("127.0.0.1", port), Handler)
    redirect_uri = f"http://127.0.0.1:{httpd.server_address[1]}"

    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",  # pede refresh_token
        "prompt": "consent",       # forca vir o refresh_token mesmo em re-autorizacao
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    url = AUTH_URI + "?" + urllib.parse.urlencode(params)
    print("\nAbra este link no navegador (tentando abrir sozinho):\n")
    print(url + "\n")
    try:
        webbrowser.open(url)
    except Exception:
        pass
    print("Aguardando o retorno do Google nesta porta loopback...")
    httpd.handle_request()  # bloqueia ate o Google redirecionar de volta

    if holder.get("state") != state:
        sys.exit("state nao confere — abortando por seguranca.")
    if "error" in holder:
        sys.exit(f"Google retornou erro: {holder['error']}")
    code = holder.get("code")
    if not code:
        sys.exit("Nao recebi o authorization code.")

    data = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "code_verifier": verifier,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        }
    ).encode()
    try:
        with urllib.request.urlopen(
            urllib.request.Request(TOKEN_URI, data=data), timeout=30
        ) as resp:
            tok = json.load(resp)
    except urllib.error.HTTPError as e:
        sys.exit(f"Falha na troca do code: HTTP {e.code}\n{e.read().decode()}")

    rt = tok.get("refresh_token")
    if not rt:
        sys.exit(
            "Sem refresh_token na resposta. Revogue o acesso do app em "
            "https://myaccount.google.com/permissions e rode de novo "
            "(precisa de prompt=consent + access_type=offline)."
        )

    print("\n" + "=" * 64)
    print("COLE ISTO NO .env DO SERVIDOR:")
    print("=" * 64)
    print(f"YOUTUBE_OAUTH_CLIENT_ID={client_id}")
    print(f"YOUTUBE_OAUTH_CLIENT_SECRET={client_secret}")
    print(f"YOUTUBE_OAUTH_REFRESH_TOKEN={rt}")
    print("=" * 64)


if __name__ == "__main__":
    main()
