# Plano: failover automático de pod GPU (RunPod) — sem GPU livre no host atual

**Status: DOCUMENTADO, NÃO IMPLEMENTADO.** Escrito em 2026-09-05 (sessão anterior
encerrando por limite). Próxima sessão: revisar as duas confirmações pendentes
no fim deste arquivo, depois implementar + testar com aprovação explícita antes
de rodar `podFindAndDeployOnDemand` de verdade (cria pod novo, começa a cobrar
na hora).

## Problema

`start_gpu()` (`lib/gpu_client.py`) já liga/desliga o pod direto na API do
RunPod (`podResume`/`podStop`/`pod`, ver commits de 2026-09-05) e funciona
corretamente — inclusive tratando erro sem travar nada. Mas hoje, em produção,
`podResume` falhou repetidas vezes com:

```
API do RunPod retornou erro: [{'message': 'There are not enough free GPUs on
the host machine to start this pod.', 'path': ['podResume'], ...}]
```

Pods do RunPod ficam presos a uma **máquina física específica** (`machineId`)
por causa do disco local do container. Se aquela máquina não tem GPU livre
agora, `podResume` falha — **mesmo que existam GPUs livres em outras máquinas**
no mesmo datacenter. Cada falha dessas descarta a notícia (áudio ElevenLabs já
pago é jogado fora, já aconteceu 2x hoje).

## Solução: montar o volume em outro host quando o atual não tiver GPU

O storage de verdade (pesos do modelo, ~100GB) está num **Network Volume**,
que **não é preso a nenhum host** — pode ser montado por um pod novo, em
qualquer máquina do MESMO datacenter que tenha GPU disponível.

**Fluxo**: `podResume` falha com "not enough free GPUs" → buscar GPU com
estoque no datacenter do volume → `podFindAndDeployOnDemand` num host novo,
montando o MESMO volume → pod novo sobe com o /generate funcionando (via
auto-start, ver seção própria) → passa a ser o pod ativo → segue o fluxo
normal (`_wait_inference_ready`, `generate_video`, depois `stop_gpu`).

## Fatos confirmados hoje (testado ao vivo, só leitura + 1 chamada de
mutation com input propositalmente inválido — nada foi criado/reservado)

- **Auth**: `Authorization: Bearer <RUNPOD_API_KEY>`, endpoint
  `https://api.runpod.io/graphql`. Introspecção desligada em produção — todo
  campo abaixo foi confirmado testando de verdade (erro de tipo/validação
  prova que o campo existe, sem executar a mutation).

- **Pod atual** (`RUNPOD_POD_ID=ykvbdr0tnvzjqk`):
  ```
  machineId:        wm6x1eometq0   (a máquina sem GPU livre)
  networkVolumeId:  ovkufmr6fe     (100GB — ESSE é o volume a preservar)
  imageName:        runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404
  containerDiskInGb: 30
  ports:            "8888/http,8000/http,22/tcp"
  volumeMountPath:  /workspace
  env:              ["JUPYTER_PASSWORD=..."]   (não crítico pro pipeline)
  GPU atual:        RTX 4000 Ada (gpuTypeId = "NVIDIA RTX 4000 Ada Generation", 20GB)
  ```
  Existe também um pod velho (`3q5x6m90tkm2f3`, `gpuCount:0`, EXITED) preso ao
  MESMO volume — sobra de antes, deixar como está (não atrapalha).

- **Network Volume** (`myself.networkVolumes`):
  ```
  id: ovkufmr6fe, size: 100 (GB), dataCenterId: "EUR-IS-1"
  ```
  **Todo pod novo TEM que ser deployado em `EUR-IS-1`**, senão não monta esse
  volume.

- **Consulta de estoque por GPU** (testado, funciona):
  ```graphql
  { gpuTypes {
      id displayName
      lowestPrice(input: {gpuCount: 1, dataCenterId: "EUR-IS-1"}) {
        stockStatus uninterruptablePrice
      }
  } }
  ```
  `stockStatus: null` = sem estoque nesse datacenter. No teste de hoje (não
  confie neste snapshot amanhã, estoque muda o tempo todo — a consulta é o
  que importa, não estes valores):
  ```
  RTX 4000 Ada (atual)        -> null (SEM estoque — por isso falhou)
  NVIDIA GeForce RTX 4090     -> "Low", $0.34/h   (24GB, upgrade sobre a atual)
  NVIDIA GeForce RTX 5090     -> "Low", $0.69/h   (32GB)
  RTX PRO 4500 Blackwell      -> "Low", $0.34/h   (32GB)
  RTX PRO 6000 Blackwell Srv  -> "Low", $1.69/h   (96GB)
  ```

- **Mutation de deploy** (confirmada, campos validados um a um sem executar):
  ```graphql
  mutation {
    podFindAndDeployOnDemand(input: {
      cloudType: SECURE            # enum confirmado válido
      gpuTypeId: "..."             # de gpuTypes, com estoque no datacenter certo
      gpuCount: 1
      name: "..."
      imageName: "runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404"
      containerDiskInGb: 30
      volumeMountPath: "/workspace"
      ports: "8888/http,8000/http,22/tcp"
      networkVolumeId: "ovkufmr6fe"
      dockerArgs: "..."           # ver seção auto-start abaixo
      env: [{key: "...", value: "..."}]
    }) { id }
  }
  ```
  Testado com `gpuTypeId` inválido de propósito -> erro limpo
  `"Unknown GPU type: ..."`, `data.podFindAndDeployOnDemand: null` — nada
  reservado. Todos os outros campos aceitos sem erro de validação.

## Auto-start do servidor de inferência (decisão do usuário, 2026-09-05)

**Via `dockerArgs`, no mesmo padrão do `pre_start.sh` que o usuário validou
diretamente no RunPod hoje (fora deste repositório e desta sessão — não há
nenhum arquivo `pre_start.sh` neste repo; procurei e não existe).**

Pendência pra próxima sessão: **confirmar com o usuário o caminho/comando
exato** desse `pre_start.sh` (presumivelmente algo em `/workspace/`, já que é
isso que sobrevive no Network Volume entre pods). O `dockerArgs` da mutation
deve chamar esse script no boot do container novo — algo como:
```
dockerArgs: "bash -c 'bash /workspace/pre_start.sh && <comando que mantém o processo vivo>'"
```
(formato exato depende de como o `pre_start.sh` real funciona — se ele já
faz `nohup .. &` e retorna, ou se é bloqueante). **Não adivinhar isto** —
perguntar ou ler o script antes de montar o `dockerArgs` de verdade.

## Design do código (a implementar)

Novo módulo `lib/runpod_failover.py` (ou funções em `gpu_client.py`):

1. `_current_pod_config(pod_id) -> dict` — lê `imageName`, `containerDiskInGf`,
   `ports`, `volumeMountPath`, `networkVolumeId` do pod ATIVO via query `pod`,
   pra não hardcodear (replica o que tiver, mesmo se mudar no futuro).
2. `_volume_datacenter(network_volume_id) -> str` — via `myself.networkVolumes`.
3. `_find_available_gpu(datacenter_id, min_vram_gb) -> str | None` — varre
   `gpuTypes` com `lowestPrice(input:{gpuCount:1, dataCenterId})`, filtra
   `stockStatus is not None` e `memoryInGb >= min_vram_gb`, devolve o mais
   barato (`uninterruptablePrice`). `min_vram_gb` = env `GPU_MIN_VRAM_GB`
   (default 20, o da GPU atual — não aceitar downgrade sem querer).
4. `_deploy_new_pod(gpu_type_id, config) -> str` — chama
   `podFindAndDeployOnDemand`, devolve o novo `podId`.
5. **Onde plugar**: dentro de `start_gpu()`, no `except` do `podResume` —
   SÓ quando a mensagem de erro contém "not enough free GPUs" (não fazer
   failover em qualquer erro — só nesse específico). Nos outros erros,
   comportamento atual (raise) continua.
6. **Trocar de pod ativo sem editar `.env` toda hora**: gravar o novo
   `podId` numa chave Redis (ex. `gpu:active_pod_id`) em vez de depender só
   de `RUNPOD_POD_ID` do `.env`. `_runpod_pod_id()` passa a checar Redis
   primeiro, cai pro env se a chave não existir. Isso também resolve, de
   quebra, o incômodo de toda vez que o pod muda ter que atualizar
   `GPU_SERVER_URL`/`RUNPOD_POD_ID` no `.env` manualmente (já aconteceu 2x
   nesta sessão) — a URL já é derivada de `pod_id` dinamicamente
   (`_runpod_proxy_url`), então só falta o `pod_id` também vir do Redis.
7. Pod antigo (sem GPU): **não terminar automaticamente** — deixar EXITED
   (não cobra enquanto parado) como está hoje com `3q5x6m90tkm2f3`. Revisão
   manual periódica é mais seguro que um script decidindo destruir pods.
8. Logar bem alto sempre que um failover acontecer (qual GPU, qual preço,
   qual pod novo) — é uma mudança de custo/hardware que a pessoa precisa
   perceber, não só o log de rotina.

## Confirmações pendentes ANTES de implementar/rodar de verdade

1. **Caminho/comando exato do `pre_start.sh`** (ou equivalente) que sobe o
   servidor de inferência sozinho no boot — perguntar ao usuário ou inspecionar
   o volume (`/workspace`) via SSH.
2. **Primeiro teste real vai criar um pod novo de verdade** (custo por hora
   começa na hora do deploy) — pedir aprovação explícita antes dessa chamada
   específica, mesmo com o resto do mecanismo já implementado e testado a seco.
