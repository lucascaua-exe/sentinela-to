"""
Sentinela TO - Simulador de Telemetria de Hardware (IoT Emulator)

Emula um medidor multimedida instalado no Quadro Geral de Distribuição (QGD)
de uma planta industrial, enviando pacotes de telemetria trifásica a cada 500 ms.

O cenário ativo pode ser definido pela linha de comando (--mode) ou alterado
remotamente pelo operador no dashboard: o backend devolve o cenário comandado
na resposta de cada pacote de telemetria.

Uso:
    python simulator/iot_emulator.py
    python simulator/iot_emulator.py --mode SAG_PALMAS --interval 0.5
    python simulator/iot_emulator.py --sites palmas,gurupi
"""

import argparse
import math
import os
import random
import sys
import time
from pathlib import Path

import requests

URL_BACKEND = "http://localhost:8000/api/telemetry"
DEVICE_TOKEN_HEADER = "X-Device-Token"
# Mesmo arquivo gerado pelo backend na primeira execução (backend/core/auth.py).
DEVICE_TOKEN_FILE = Path(__file__).resolve().parents[1] / "data" / "device.token"

MODES = (
    "NORMAL",
    "SAG_PALMAS",
    "SWELL",
    "PHASE_LOSS",
    "PHASE_IMBALANCE",
    "HARMONIC",
    "BLACKOUT",
    "PRE_FAULT",
)

def _u(a: float, b: float) -> float:
    return round(random.uniform(a, b), 2)


def _n(center: float, spread: float) -> float:
    """Valor em torno do ponto de operação do cenário, limitado a ±2 desvios."""
    return round(center + max(-2.0, min(2.0, random.gauss(0.0, 1.0))) * spread / 2.0, 2)


def generate_electrical_data(mode: str = "NORMAL", tick: int = 0) -> dict:
    """Gera um pacote de telemetria para o cenário informado."""
    if mode == "NORMAL":
        # Cenário 1 - rede estável: A 220.4 V, B 219.8 V, C 221.1 V, 60.0 Hz, FP 0.96
        v_a, v_b, v_c = _n(220.4, 1.2), _n(219.8, 1.2), _n(221.1, 1.2)
        current = _n(32.0, 2.5)
        freq = _n(60.0, 0.03)
        fp = _n(0.96, 0.005)
        thd = _n(3.0, 0.4)

    elif mode == "PRE_FAULT":
        # Degradação lenta e sutil: tensão caindo e THD subindo (precursor de falha)
        drift = min(tick, 40) / 40.0
        v_a = _u(212.0 - 14 * drift, 220.0 - 14 * drift)
        v_b = _u(211.0 - 12 * drift, 219.0 - 12 * drift)
        v_c = _u(214.0 - 4 * drift, 222.0 - 4 * drift)
        current = _u(38.0 + 6 * drift, 44.0 + 8 * drift)
        freq = _u(59.8 - 0.2 * drift, 60.0)
        fp = _u(0.90 - 0.05 * drift, 0.93 - 0.05 * drift)
        thd = _u(5.0 + 3 * drift, 6.5 + 3 * drift)

    elif mode == "SAG_PALMAS":
        # Cenário 2 - afundamento em Palmas: A e B caem para ~168.2 V, frequência ~58.1 Hz
        v_a, v_b = _n(168.2, 1.5), _n(168.2, 1.5)
        v_c = _n(219.0, 1.5)
        current = _n(58.0, 3.0)  # Sobrecorrente por subtensão
        freq = _n(58.1, 0.1)
        fp = _n(0.79, 0.015)
        thd = _n(7.5, 0.6)

    elif mode == "SWELL":
        # Cenário 3 - surto: pico de ~248.5 V na Fase A
        v_a, v_b, v_c = _n(248.5, 1.2), _n(236.0, 1.2), _n(234.5, 1.2)
        current = _n(26.0, 2.0)
        freq = _n(60.3, 0.05)
        fp = _n(0.90, 0.01)
        thd = _n(5.0, 0.5)

    elif mode == "PHASE_LOSS":
        # Cenário 4 - fusível queimado na subestação: Fase C cai para 0 V
        v_a, v_b = _n(220.6, 1.2), _n(219.4, 1.2)
        v_c = abs(_n(0.0, 0.3))
        current = _n(55.0, 2.5)  # Sobrecorrente nas fases remanescentes
        freq = _n(59.95, 0.03)
        fp = _n(0.82, 0.01)
        thd = _n(6.0, 0.5)

    elif mode == "PHASE_IMBALANCE":
        # Falta de fase parcial / desequilíbrio severo entre fases
        v_a = _u(218.0, 224.0)
        v_b = _u(192.0, 200.0)
        v_c = _u(228.0, 234.0)
        current = _u(42.0, 52.0)
        freq = _u(59.8, 60.1)
        fp = _u(0.84, 0.89)
        thd = _u(4.5, 6.5)

    elif mode == "HARMONIC":
        # Distorção harmônica elevada (inversores, retificadores, fornos)
        ripple = 3.0 * math.sin(tick / 2.0)
        v_a, v_b, v_c = _u(212.0, 220.0) + ripple, _u(211.0, 219.0) - ripple, _u(212.0, 221.0)
        current = _u(36.0, 46.0)
        freq = _u(59.7, 60.2)
        fp = _u(0.80, 0.86)
        thd = _u(11.0, 16.0)

    elif mode == "BLACKOUT":
        # Interrupção do fornecimento (apagão não programado)
        v_a, v_b, v_c = _u(0.0, 8.0), _u(0.0, 8.0), _u(0.0, 8.0)
        current = _u(0.0, 1.5)
        freq = _u(0.0, 5.0)
        fp = _u(0.0, 0.2)
        thd = _u(0.0, 1.0)

    else:
        raise ValueError(f"Modo desconhecido: {mode}")

    return {
        "timestamp": time.time(),
        "voltage_a": round(v_a, 2),
        "voltage_b": round(v_b, 2),
        "voltage_c": round(v_c, 2),
        "current_total": current,
        "frequency": freq,
        "power_factor": fp,
        "thd_voltage": thd,
    }


def device_token() -> str | None:
    token = os.environ.get("SENTINELA_DEVICE_TOKEN")
    if token:
        return token
    if DEVICE_TOKEN_FILE.exists():
        return DEVICE_TOKEN_FILE.read_text(encoding="utf-8").strip()
    return None


def connect(url: str) -> tuple[requests.Session, list[str]]:
    """Aguarda o backend e obtém o cadastro de unidades (fonte única: SITES no backend)."""
    while True:
        token = device_token()
        if token is None:
            print("[Sentinela TO] Aguardando o backend gerar o token do dispositivo (data/device.token)...")
        else:
            session = requests.Session()
            session.headers[DEVICE_TOKEN_HEADER] = token
            try:
                resp = session.get(f"{url.rstrip('/')}/sites", timeout=2)
                resp.raise_for_status()
                return session, [site["id"] for site in resp.json()]
            except requests.RequestException as exc:
                print(f"[Sentinela TO] Backend indisponível ({exc.__class__.__name__}). Nova tentativa...")
        time.sleep(2)


def run(session: requests.Session, url: str, site_ids: list[str], mode: str, interval: float) -> None:
    # Cada unidade tem cenário e contador próprios; o operador comanda cada uma pelo dashboard.
    units = {site_id: {"mode": mode, "tick": 0, "status": "?"} for site_id in site_ids}
    print(f"[Sentinela TO] Simulador IoT iniciado -> {url}")
    print(f"[Sentinela TO] Unidades: {', '.join(site_ids)} | cenário inicial: {mode} | intervalo: {interval * 1000:.0f} ms")

    while True:
        started = time.perf_counter()
        try:
            for site_id, unit in units.items():
                payload = generate_electrical_data(unit["mode"], unit["tick"]) | {"site_id": site_id}
                resp = session.post(url, json=payload, timeout=2)
                resp.raise_for_status()
                body = resp.json()
                commanded = body.get("command_mode")
                if commanded and commanded != unit["mode"] and commanded in MODES:
                    print(f"\n[Sentinela TO] {site_id}: cenário alterado pelo operador {unit['mode']} -> {commanded}")
                    unit["mode"], unit["tick"] = commanded, 0
                unit["status"] = body.get("severity", "?")
                unit["tick"] += 1
            sys.stdout.write("\r" + " | ".join(f"{s}: {u['mode']} -> {u['status']}" for s, u in units.items()) + "   ")
            sys.stdout.flush()
        except requests.RequestException as exc:
            print(f"\n[Sentinela TO] Backend indisponível ({exc.__class__.__name__}). Nova tentativa...")
            time.sleep(2)

        time.sleep(max(0.0, interval - (time.perf_counter() - started)))


def main() -> None:
    parser = argparse.ArgumentParser(description="Sentinela TO - Emulador de medidor trifásico IoT")
    parser.add_argument("--url", default=URL_BACKEND)
    parser.add_argument("--mode", default="NORMAL", choices=MODES)
    parser.add_argument("--interval", type=float, default=0.5, help="Intervalo entre pacotes (s)")
    parser.add_argument("--sites", default="all", help="Unidades simuladas, separadas por vírgula, ou 'all'")
    args = parser.parse_args()
    try:
        session, known = connect(args.url)
        site_ids = known if args.sites == "all" else [s.strip() for s in args.sites.split(",")]
        unknown = [s for s in site_ids if s not in known]
        if unknown:
            parser.error(f"Unidade(s) desconhecida(s): {', '.join(unknown)}. Opções: {', '.join(known)}")
        run(session, args.url, site_ids, args.mode, args.interval)
    except KeyboardInterrupt:
        print("\n[Sentinela TO] Simulador encerrado.")


if __name__ == "__main__":
    main()
