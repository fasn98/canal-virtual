# ⚠️ Este diretório não tem mais um docker-compose.yml ativo

O `docker-compose.yml` oficial do projeto agora é **apenas o da raiz**:

```
canal-virtual/docker-compose.yml
```

## O que aconteceu com o `infra/docker-compose.yml`?

Foi renomeado para `docker-compose.legacy-minio.yml.bak`. Ele descrevia uma
variante do pipeline baseada em MinIO + imagens pré-buildadas (`fasn/*:latest`),
mas essa variante nunca foi conectada ao código real dos serviços (nenhum
`main.py` do projeto usa `MINIO_ENDPOINT`, por exemplo) e incluía dois
serviços mortos:

- `orchestrator` — só imprimia "Orchestrator dummy rodando..." em loop
- `ultrafast` — lia de um stream Redis (`news.categorized`) que nenhum
  serviço escreve, e escrevia em outro (`news.ultrafast`) que nenhum
  serviço lê

Se um dia vocês quiserem retomar a arquitetura com MinIO (por exemplo, pra
guardar áudio/vídeo em object storage em vez de volume local), o arquivo
`.bak` está aqui como referência — mas ele vai precisar de trabalho de
verdade pra funcionar, não é plug-and-play.

## Como rodar o projeto hoje

Sempre a partir da raiz do repositório:

```bash
docker compose up -d --build
```
