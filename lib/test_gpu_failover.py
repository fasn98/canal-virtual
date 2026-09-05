"""Testes do failover de pod GPU (lib/gpu_client). Sem rede: as chamadas
GraphQL/REST do RunPod são mockadas.

Uso (raiz do repo):  python3 -m lib.test_gpu_failover
"""
import os
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import lib.gpu_client as G

GPUTYPES = {
    "gpuTypes": [
        {"id": "A", "displayName": "GPU A 16GB", "memoryInGb": 16,
         "lowestPrice": {"stockStatus": "Low", "uninterruptablePrice": 0.10}},
        {"id": "B", "displayName": "GPU B 24GB", "memoryInGb": 24,
         "lowestPrice": {"stockStatus": "Low", "uninterruptablePrice": 0.34}},
        {"id": "C", "displayName": "GPU C 20GB", "memoryInGb": 20,
         "lowestPrice": {"stockStatus": None, "uninterruptablePrice": 0.20}},  # sem estoque
        {"id": "D", "displayName": "GPU D 48GB", "memoryInGb": 48,
         "lowestPrice": {"stockStatus": "Medium", "uninterruptablePrice": 0.80}},
    ]
}


def main():
    ok = True

    # 1) _find_available_gpu: mais barata COM estoque e VRAM >= min
    G._runpod_graphql = lambda q, *, timeout: GPUTYPES
    gid, price, name = G._find_available_gpu("EUR-IS-1", 20, timeout=5)
    r1 = gid == "B" and abs(price - 0.34) < 1e-6           # A tem estoque mas 16<20; C sem estoque
    print(f"[{'OK ' if r1 else 'FAIL'}] _find_available_gpu -> {gid} ${price} ({name}) [esperado B $0.34]")
    ok &= r1

    # 1b) nada serve -> GpuError
    G._runpod_graphql = lambda q, *, timeout: {"gpuTypes": [
        {"id": "X", "memoryInGb": 8, "lowestPrice": {"stockStatus": "Low", "uninterruptablePrice": 0.1}}]}
    try:
        G._find_available_gpu("EUR-IS-1", 20, timeout=5)
        r2 = False
    except G.GpuError:
        r2 = True
    print(f"[{'OK ' if r2 else 'FAIL'}] _find_available_gpu sem candidata -> GpuError")
    ok &= r2

    # 2) _runpod_pod_id: fallback pro env quando não há Redis
    os.environ.pop("REDIS_HOST", None)
    os.environ["RUNPOD_POD_ID"] = "envpod"
    r3 = G._runpod_pod_id() == "envpod" and G._runpod_pod_id("explicit") == "explicit"
    print(f"[{'OK ' if r3 else 'FAIL'}] _runpod_pod_id: env fallback + explícito")
    ok &= r3

    # 3) _failover_deploy: replica config + deploya + persiste
    calls = {}

    def fake_rest(method, path, *, json_body=None, timeout):
        if path == "/networkvolumes":
            return [{"id": "vol1", "dataCenterId": "EUR-IS-1"}]
        if path == "/pods/oldpod123":
            return {"networkVolumeId": "vol1", "imageName": "img:1", "containerDiskInGb": 40,
                    "ports": ["8888/http", "8000/http", "22/tcp"], "volumeMountPath": "/workspace"}
        raise AssertionError(f"REST inesperado: {method} {path}")

    G._runpod_rest = fake_rest

    def fake_gql(q, *, timeout):
        if "gpuTypes" in q:
            return GPUTYPES
        if "podFindAndDeployOnDemand" in q:
            calls["mutation"] = q
            return {"podFindAndDeployOnDemand": {"id": "newpod777"}}
        raise AssertionError(f"query inesperada: {q[:80]}")

    G._runpod_graphql = fake_gql
    saved = {}
    G._set_active_pod_id = lambda pid: saved.setdefault("pid", pid)
    new = G._failover_deploy("oldpod123", timeout=5)
    m = calls.get("mutation", "")
    r4 = (new == "newpod777" and saved.get("pid") == "newpod777"
          and '"vol1"' in m and '"img:1"' in m and "containerDiskInGb: 40" in m
          and "cloudType: SECURE" in m and "dockerArgs:" in m
          and '"8888/http,8000/http,22/tcp"' in m and '"B"' in m)  # gpuTypeId B (mais barata c/ estoque)
    print(f"[{'OK ' if r4 else 'FAIL'}] _failover_deploy -> {new}, persistido={saved.get('pid')}, "
          f"mutation replica config={'sim' if ('img:1' in m and 'vol1' in m) else 'NÃO'}")
    ok &= r4

    # 4) start_gpu: podResume 'not enough free GPUs' -> failover -> URL do pod novo
    os.environ["GPU_FAILOVER_ENABLED"] = "true"
    G._failover_deploy = lambda old, *, timeout: "newpod777"

    def gql_resume_fails(q, *, timeout):
        if "podResume" in q:
            raise G.GpuError("API do RunPod retornou erro: [{'message': 'There are not "
                             "enough free GPUs on the host machine to start this pod.'}]")
        if "pod(input" in q:
            return {"pod": {"id": "newpod777", "desiredStatus": "RUNNING",
                            "runtime": {"uptimeInSeconds": 7}}}
        raise AssertionError(q[:80])

    G._runpod_graphql = gql_resume_fails
    url = G.start_gpu(pod_id="oldpod123")
    r5 = url == "https://newpod777-8000.proxy.runpod.net"
    print(f"[{'OK ' if r5 else 'FAIL'}] start_gpu failover -> {url}")
    ok &= r5

    # 4b) erro DIFERENTE (não é falta de GPU) -> propaga, sem failover
    def gql_other_error(q, *, timeout):
        if "podResume" in q:
            raise G.GpuError("API do RunPod retornou erro: [{'message': 'pod not found'}]")
        raise AssertionError(q[:80])

    G._runpod_graphql = gql_other_error
    tripped = {"failover": False}
    G._failover_deploy = lambda *a, **k: tripped.__setitem__("failover", True) or "x"
    try:
        G.start_gpu(pod_id="oldpod123")
        r6 = False
    except G.GpuError as e:
        r6 = "pod not found" in str(e) and not tripped["failover"]
    print(f"[{'OK ' if r6 else 'FAIL'}] start_gpu: erro não-GPU propaga sem failover")
    ok &= r6

    # 4c) failover DESLIGADO -> erro de GPU propaga
    os.environ["GPU_FAILOVER_ENABLED"] = "false"
    G._runpod_graphql = gql_resume_fails
    try:
        G.start_gpu(pod_id="oldpod123")
        r7 = False
    except G.GpuError as e:
        r7 = "not enough free GPUs" in str(e)
    os.environ["GPU_FAILOVER_ENABLED"] = "true"
    print(f"[{'OK ' if r7 else 'FAIL'}] GPU_FAILOVER_ENABLED=false -> erro de GPU propaga")
    ok &= r7

    print("\n" + ("TODOS OS CASOS OK" if ok else "HÁ FALHAS"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
