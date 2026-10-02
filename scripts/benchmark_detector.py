"""Avalia o detector em todos os cenários do emulador (taxa de alarmes e risco)."""

import numpy as np

from backend.ai.anomaly_detector import AnomalyDetector
from simulator.iot_emulator import MODES, generate_electrical_data

SEVERITIES = ("NORMAL", "INFO", "WARNING", "CRITICAL")


def main() -> None:
    detector = AnomalyDetector()
    print(f"{'Cenário':<16} {'risco p50':>9} {'p95':>6} {'máx':>6} {'lat. ms':>8}  " + " ".join(f"{s:>8}" for s in SEVERITIES))
    for mode in MODES:
        detector.reset_window()
        n = 1000 if mode == "NORMAL" else 45
        results = [detector.analyze(generate_electrical_data(mode, tick=i)) for i in range(n)]
        risks = np.array([r.risk_score for r in results])
        latency = np.mean([r.latency_ms for r in results])
        counts = [sum(r.severity == s for r in results) for s in SEVERITIES]
        print(
            f"{mode:<16} {np.median(risks):9.1f} {np.percentile(risks, 95):6.1f} {risks.max():6.1f} {latency:8.2f}  "
            + " ".join(f"{c:>8}" for c in counts)
        )
        if mode == "NORMAL":
            for r in [r for r in results if r.severity in ("WARNING", "CRITICAL")][:3]:
                print("   falso alarme:", r.severity, r.risk_score, [d.code for d in r.diagnostics])


if __name__ == "__main__":
    main()
