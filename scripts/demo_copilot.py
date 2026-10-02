"""Mostra o diagnóstico do Copilot RAG para cada cenário do emulador (sem backend rodando)."""

import json

from backend.ai.anomaly_detector import AnomalyDetector
from backend.ai.copilot import DiagnosisRequest, DiagnosticCopilot, ordered_codes
from simulator.iot_emulator import generate_electrical_data

SCENARIOS = ("SAG_PALMAS", "SWELL", "PHASE_LOSS", "PHASE_IMBALANCE", "HARMONIC", "BLACKOUT", "PRE_FAULT")


def main() -> None:
    detector = AnomalyDetector()
    copilot = DiagnosticCopilot()
    print(f"Base: {len(copilot.kb.chunks)} trechos de {len(copilot.kb.sources)} fontes | motor: {copilot.engine}\n")
    for mode in SCENARIOS:
        detector.reset_window()
        for tick in range(45 if mode == "PRE_FAULT" else 12):
            sample = generate_electrical_data(mode, tick)
            analysis = detector.analyze(sample)
        diags = [d.as_dict() for d in analysis.diagnostics]
        req = DiagnosisRequest(
            site_id="palmas",
            city="Palmas",
            location="Distrito Industrial de Palmas - Setor 02",
            sample=sample,
            severity=analysis.severity,
            codes=ordered_codes(diags, analysis.severity),
            risk_score=analysis.risk_score,
        )
        result = copilot.diagnose_local(req)
        print(f"=== {mode} ({analysis.severity}) ===")
        print(json.dumps({k: result[k] for k in ("cause_analysis", "severity_level", "confidence_score",
                                                  "norm_references", "recommended_solutions", "speech_summary")},
                         ensure_ascii=False, indent=2))
        print()


if __name__ == "__main__":
    main()
