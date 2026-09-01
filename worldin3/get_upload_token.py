#!/usr/bin/env python3
"""Autorização OAuth 2.0 com escopo de UPLOAD para o "O Mundo em 3 Minutos".

Reaproveita o MESMO OAuth client (YOUTUBE_OAUTH_CLIENT_ID / _CLIENT_SECRET) já
usado pelo bot de chat, mas pede um escopo mais forte:

    https://www.googleapis.com/auth/youtube.upload      -> videos.insert
    https://www.googleapis.com/auth/youtube.force-ssl   -> playlistItems.insert
                                                           (adicionar à playlist)

O refresh token resultante é salvo numa variável NOVA e separada
(YOUTUBE_OAUTH_REFRESH_TOKEN_UPLOAD) — NÃO sobrescreve o token do bot de chat.

Fluxo em 2 passos (o servidor não tem navegador; você usa o SEU):

  1) Gera a URL de autorização e guarda o estado PKCE:
         python3 -m worldin3.get_upload_token url

     Abra a URL no navegador logado na conta DONA do canal, autorize. O
     navegador vai tentar abrir  http://127.0.0.1:8765/?code=...&state=...
     e falhar ("não foi possível acessar o site") — isso é ESPERADO. Copie a
     URL inteira da barra de endereço (ou só o valor de code=).

  2) Troca o código pelo refresh token e grava no .env:
         python3 -m worldin3.get_upload_token exchange "<url-ou-code>" --write-env

Estado PKCE fica em volumes/output/.upload_token_state.json (efêmero).
"""

import base64
import hashlib
import json
import os
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request

AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]
REDIRECT_URI = os.environ.get("UPLOAD_OAUTH_REDIRECT", "http://127.0.0.1:8765")
ENV_VAR = "YOUTUBE_OAUTH_REFRESH_TOKEN_UPLOAD"

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
STATE_FILE = os.path.join(_REPO, "volumes", "output", ".upload_token_state.json")
ENV_FILE = os.path.join(_REPO, ".env")


def _load_env_file(path=ENV_FILE):
    vals = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                vals[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return vals


def _creds():
    env = _load_env_file()
    cid = (os.environ.get("YOUTUBE_OAUTH_CLIENT_ID")
           or env.get("YOUTUBE_OAUTH_CLIENT_ID", "")).strip()
    csec = (os.environ.get("YOUTUBE_OAUTH_CLIENT_SECRET")
            or env.get("YOUTUBE_OAUTH_CLIENT_SECRET", "")).strip()
    if not cid or not csec:
        sys.exit("YOUTUBE_OAUTH_CLIENT_ID / _CLIENT_SECRET não encontrados no .env nem no ambiente.")
    return cid, csec


def cmd_url():
    client_id, _ = _creds()
    verifier = base64.urlsafe_b64encode(secrets.token_bytes(64)).rstrip(b"=").decode()
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=").decode()
    )
    state = secrets.token_urlsafe(16)
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump({"verifier": verifier, "state": state,
                   "redirect_uri": REDIRECT_URI, "client_id": client_id}, f)
    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    print(AUTH_URI + "?" + urllib.parse.urlencode(params))


def _extract_code(arg, expected_state):
    arg = arg.strip().strip('"').strip("'")
    if arg.startswith("http://") or arg.startswith("https://"):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(arg).query)
        if "error" in q:
            sys.exit(f"Google retornou erro na URL: {q['error'][0]}")
        got_state = (q.get("state") or [""])[0]
        if expected_state and got_state and got_state != expected_state:
            sys.exit("state não confere — refaça o passo 'url' e use a URL nova.")
        code = (q.get("code") or [""])[0]
    else:
        code = arg
    if not code:
        sys.exit("Não achei o 'code'. Cole a URL inteira da barra de endereço ou o valor de code=.")
    return code


def cmd_exchange(arg, write_env=False):
    client_id, client_secret = _creds()
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            st = json.load(f)
    except FileNotFoundError:
        sys.exit("Estado PKCE não encontrado. Rode 'python3 -m worldin3.get_upload_token url' primeiro.")

    code = _extract_code(arg, st.get("state"))
    data = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "code_verifier": st["verifier"],
        "grant_type": "authorization_code",
        "redirect_uri": st.get("redirect_uri", REDIRECT_URI),
    }).encode()
    try:
        with urllib.request.urlopen(
            urllib.request.Request(TOKEN_URI, data=data), timeout=30
        ) as resp:
            tok = json.load(resp)
    except urllib.error.HTTPError as e:
        sys.exit(f"Falha na troca do code: HTTP {e.code}\n{e.read().decode()}")

    rt = tok.get("refresh_token")
    scope = tok.get("scope", "")
    if not rt:
        sys.exit("Sem refresh_token na resposta (revogue o acesso em "
                 "https://myaccount.google.com/permissions e refaça com prompt=consent).")
    if "youtube.upload" not in scope:
        print(f"AVISO: escopo devolvido não inclui youtube.upload: {scope!r}", file=sys.stderr)

    print("\n" + "=" * 64)
    print(f"{ENV_VAR}={rt}")
    print("=" * 64)
    print(f"escopos concedidos: {scope}", file=sys.stderr)

    if write_env:
        _write_env_var(ENV_VAR, rt)
        print(f"[ok] {ENV_VAR} adicionado a {ENV_FILE}", file=sys.stderr)
    try:
        os.remove(STATE_FILE)
    except OSError:
        pass


def _write_env_var(key, value):
    lines = []
    try:
        with open(ENV_FILE, encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        pass
    hit = False
    for i, ln in enumerate(lines):
        if ln.split("=", 1)[0].strip() == key:
            lines[i] = f"{key}={value}\n"
            hit = True
            break
    if not hit:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines.append("\n# Parte 2 worldin3 — refresh token com escopo de upload "
                     "(separado do token do bot de chat)\n")
        lines.append(f"{key}={value}\n")
    with open(ENV_FILE, "w", encoding="utf-8") as f:
        f.writelines(lines)


def main(argv):
    if not argv or argv[0] not in ("url", "exchange"):
        sys.exit(__doc__)
    if argv[0] == "url":
        cmd_url()
    else:
        if len(argv) < 2:
            sys.exit('uso: python3 -m worldin3.get_upload_token exchange "<url-ou-code>" [--write-env]')
        cmd_exchange(argv[1], write_env="--write-env" in argv[2:])


if __name__ == "__main__":
    main(sys.argv[1:])
