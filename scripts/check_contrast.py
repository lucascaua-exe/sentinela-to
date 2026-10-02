"""Verifica o contraste WCAG dos pares de cores do tema claro do dashboard (frontend/styles.css)."""


def _lin(c: float) -> float:
    c /= 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def luminance(hex_color: str) -> float:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _lin(r) + 0.7152 * _lin(g) + 0.0722 * _lin(b)


def ratio(fg: str, bg: str) -> float:
    a, b = sorted((luminance(fg), luminance(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)


SURFACE = "#ffffff"
CANVAS = "#f8fafc"
TEXT = "#0f172a"
TEXT_SECONDARY = "#64748b"

# (descrição, frente, fundo, mínimo exigido)
PAIRS = [
    ("texto principal / card", TEXT, SURFACE, 4.5),
    ("texto principal / canvas", TEXT, CANVAS, 4.5),
    ("texto secundário / card", TEXT_SECONDARY, SURFACE, 4.5),
    ("texto secundário / canvas", TEXT_SECONDARY, CANVAS, 4.5),
    ("rótulos dos eixos do gráfico / card", "#64748b", SURFACE, 4.5),
    ("cenário ativo (branco sobre grafite)", "#ffffff", TEXT, 4.5),
    ("status 'atenção' 12px / card", "#b45309", SURFACE, 4.5),
    ("status 'crítico' 12px / card", "#b91c1c", SURFACE, 4.5),
    ("número de atenção 48px (texto grande) / card", "#d97706", SURFACE, 3.0),
    ("número crítico 48px (texto grande) / card", "#dc2626", SURFACE, 3.0),
    ("linha fase A / card", "#ea580c", SURFACE, 3.0),
    ("linha fase B / card", "#2563eb", SURFACE, 3.0),
    ("linha fase C / card", "#475569", SURFACE, 3.0),
    ("ponto de status operando / card", "#16a34a", SURFACE, 3.0),
    ("ponto de status em alerta / card", "#dc2626", SURFACE, 3.0),
    ("botão 'Comutar agora' (branco sobre vermelho)", "#ffffff", "#dc2626", 4.5),
    ("botão 'Sair' / card", "#334155", SURFACE, 4.5),
    ("inicial do usuário / avatar", "#c2410c", "#fff7ed", 4.5),
]


def main() -> None:
    failures = 0
    for name, fg, bg, minimum in PAIRS:
        r = ratio(fg, bg)
        ok = r >= minimum
        failures += not ok
        print(f"{'OK ' if ok else 'FALHA'} {r:5.2f}:1 (mín {minimum}) {name}  {fg} sobre {bg}")
    print(f"\n{len(PAIRS) - failures}/{len(PAIRS)} pares aprovados")


if __name__ == "__main__":
    main()
