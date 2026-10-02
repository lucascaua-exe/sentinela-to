"""
Relatórios de ocorrências: PDF empresarial (ReportLab) e CSV para planilhas.
"""

from __future__ import annotations

import csv
import io
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from PIL import Image
from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.shapes import Drawing, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

LOGO_PATH = Path(__file__).resolve().parents[2] / "devkit" / "logo_sem_fundo.png"
# Recorte do escudo no PNG oficial (o texto da logo é claro demais para fundo branco).
SHIELD_BOX = (165, 112, 352, 304)
MAX_CRITICAL_DETAILS = 12

ORANGE = colors.HexColor("#EA580C")
SLATE = colors.HexColor("#475569")
INK = colors.HexColor("#0F172A")
MUTED = colors.HexColor("#64748B")
BORDER = colors.HexColor("#E2E8F0")
TRACK = colors.HexColor("#F1F5F9")
RED = colors.HexColor("#DC2626")
AMBER = colors.HexColor("#D97706")
GREEN = colors.HexColor("#16A34A")

SEVERITY_TEXT = {"CRITICAL": "Crítica", "WARNING": "Atenção"}
SEVERITY_COLOR = {"CRITICAL": RED, "WARNING": AMBER}
CODE_LABELS = {
    "SAG": "Afundamento de tensão",
    "SWELL": "Sobretensão",
    "PHASE_LOSS": "Falta de fase",
    "IMBALANCE": "Desequilíbrio de tensão",
    "INTERRUPTION": "Interrupção",
    "OVERCURRENT": "Sobrecorrente",
    "FREQUENCY": "Desvio de frequência",
    "HARMONIC": "Distorção harmônica",
    "POWER_FACTOR": "Fator de potência",
    "AI_PATTERN": "Padrão atípico (IA)",
}


@dataclass
class ReportMeta:
    period: str
    sites: str
    filters: str
    generated_by: str
    company: str = ""
    responsible: str = ""
    site_names: dict[str, str] = field(default_factory=dict)
    loss_per_transfer_brl: float = 0.0
    generated_at: float = field(default_factory=time.time)


def code_label(code: str) -> str:
    return CODE_LABELS.get(code, code)


def fmt_duration(seconds: float) -> str:
    seconds = int(round(seconds or 0))
    if seconds < 60:
        return f"{seconds} s"
    minutes, s = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} min {s:02d} s"
    hours, m = divmod(minutes, 60)
    return f"{hours} h {m:02d} min"


def fmt_ts(ts: float | None, with_date: bool = True) -> str:
    if ts is None:
        return "—"
    return time.strftime("%d/%m/%Y %H:%M:%S" if with_date else "%H:%M:%S", time.localtime(ts))


def fmt_num(value: float | None, digits: int = 1, unit: str = "") -> str:
    if value is None:
        return "—"
    text = f"{value:,.{digits}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{text} {unit}".strip()


def fmt_brl(value: float) -> str:
    return "R$ " + fmt_num(value, 2)


@lru_cache(maxsize=1)
def shield_image() -> ImageReader | None:
    if not LOGO_PATH.exists():
        return None
    shield = Image.open(LOGO_PATH).convert("RGBA").crop(SHIELD_BOX)
    background = Image.new("RGB", shield.size, (255, 255, 255))
    background.paste(shield, mask=shield.getchannel("A"))
    buffer = io.BytesIO()
    background.save(buffer, format="PNG")
    buffer.seek(0)
    return ImageReader(buffer)


STYLES = {
    "title": ParagraphStyle("title", fontName="Helvetica-Bold", fontSize=19, leading=24, textColor=INK, spaceAfter=2),
    "subtitle": ParagraphStyle("subtitle", fontName="Helvetica", fontSize=10.5, leading=14, textColor=MUTED),
    "h2": ParagraphStyle("h2", fontName="Helvetica-Bold", fontSize=12.5, leading=16, textColor=INK, spaceBefore=14, spaceAfter=8),
    "body": ParagraphStyle("body", fontName="Helvetica", fontSize=9, leading=13, textColor=INK),
    "muted": ParagraphStyle("muted", fontName="Helvetica", fontSize=8, leading=11, textColor=MUTED),
    "cell": ParagraphStyle("cell", fontName="Helvetica", fontSize=8, leading=10.5, textColor=INK),
    "cell_bold": ParagraphStyle("cell_bold", fontName="Helvetica-Bold", fontSize=8, leading=10.5, textColor=INK),
    "kpi_value": ParagraphStyle("kpi_value", fontName="Helvetica-Bold", fontSize=17, leading=21, textColor=INK),
    "kpi_label": ParagraphStyle("kpi_label", fontName="Helvetica", fontSize=7.5, leading=10, textColor=MUTED),
    "label": ParagraphStyle("label", fontName="Helvetica-Bold", fontSize=7.5, leading=10, textColor=MUTED),
    "right": ParagraphStyle("right", fontName="Helvetica", fontSize=8, leading=10.5, textColor=INK, alignment=TA_RIGHT),
}


def esc(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class NumberedCanvas(pdfcanvas.Canvas):
    """Cabeçalho e rodapé desenhados no fim, quando o total de páginas é conhecido."""

    def __init__(self, *args, meta: ReportMeta, **kwargs):
        super().__init__(*args, **kwargs)
        self._pages = []
        self._meta = meta

    def showPage(self):
        self._pages.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._pages)
        for page in self._pages:
            self.__dict__.update(page)
            self._decorate(total)
            super().showPage()
        super().save()

    def _decorate(self, total: int) -> None:
        width, height = A4
        left, right = 18 * mm, width - 18 * mm
        top = height - 14 * mm
        logo = shield_image()
        if logo:
            self.drawImage(logo, left, top - 9 * mm, width=9 * mm, height=9.2 * mm, mask="auto")
        self.setFont("Helvetica", 12.5)
        self.setFillColor(SLATE)
        self.drawString(left + 11.5 * mm, top - 6.2 * mm, "Sentinela")
        self.setFont("Helvetica-Bold", 12.5)
        self.setFillColor(ORANGE)
        self.drawString(left + 11.5 * mm + self.stringWidth("Sentinela ", "Helvetica", 12.5), top - 6.2 * mm, "TO")
        self.setFont("Helvetica", 8)
        self.setFillColor(MUTED)
        self.drawRightString(right, top - 3.6 * mm, "Relatório de ocorrências")
        self.drawRightString(right, top - 7.6 * mm, self._meta.company or "Qualidade de energia · Tocantins")
        self.setStrokeColor(ORANGE)
        self.setLineWidth(1.2)
        self.line(left, top - 12 * mm, left + 22 * mm, top - 12 * mm)
        self.setStrokeColor(BORDER)
        self.setLineWidth(0.6)
        self.line(left + 22 * mm, top - 12 * mm, right, top - 12 * mm)

        self.setFont("Helvetica", 7.5)
        self.setFillColor(MUTED)
        self.line(left, 15 * mm, right, 15 * mm)
        self.drawString(
            left, 10.5 * mm, f"Sentinela TO · Emitido em {fmt_ts(self._meta.generated_at)} por {self._meta.generated_by}"
        )
        self.drawRightString(right, 10.5 * mm, f"Página {self._pageNumber} de {total}")


def _info_table(meta: ReportMeta) -> Table:
    rows = [
        ("Período analisado", meta.period),
        ("Unidades", meta.sites),
        ("Filtros aplicados", meta.filters),
    ]
    if meta.company:
        rows.insert(0, ("Empresa", meta.company))
    if meta.responsible:
        rows.append(("Responsável técnico", meta.responsible))
    data = [[Paragraph(label.upper(), STYLES["label"]), Paragraph(esc(value), STYLES["body"])] for label, value in rows]
    table = Table(data, colWidths=[42 * mm, None])
    table.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LINEBELOW", (0, 0), (-1, -2), 0.4, BORDER),
                ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                ("BOX", (0, 0), (-1, -1), 0.6, BORDER),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return table


def _kpis(stats: dict, meta: ReportMeta) -> Table:
    items = [
        (str(stats["total"]), "Ocorrências registradas", INK),
        (str(stats["critical"]), "Críticas", RED),
        (str(stats["transfers"]), "Comutações para autoprodução", ORANGE),
        (fmt_duration(stats["total_duration_s"]), "Tempo total em anomalia", INK),
    ]
    cells = [
        [Paragraph(f'<font color="{c.hexval().replace("0x", "#")}">{esc(v)}</font>', STYLES["kpi_value"]), Paragraph(esc(l), STYLES["kpi_label"])]
        for v, l, c in items
    ]
    table = Table([[Table([[a], [b]], style=[("LEFTPADDING", (0, 0), (-1, -1), 0)]) for a, b in cells]], colWidths=[None] * 4)
    table.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.6, BORDER),
                ("INNERGRID", (0, 0), (-1, -1), 0.6, BORDER),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    return table


def _daily_chart(stats: dict, width: float) -> Drawing | None:
    days = list(stats["by_day"].items())[-31:]
    if not days:
        return None
    height = 150
    drawing = Drawing(width, height)
    chart = VerticalBarChart()
    chart.x, chart.y = 28, 26
    chart.width, chart.height = width - 40, height - 46
    chart.data = [[d[1].get("CRITICAL", 0) for d in days], [d[1].get("WARNING", 0) for d in days]]
    chart.categoryAxis.style = "stacked"
    chart.categoryAxis.categoryNames = [time.strftime("%d/%m", time.strptime(d[0], "%Y-%m-%d")) for d in days]
    chart.categoryAxis.labels.fontName = "Helvetica"
    chart.categoryAxis.labels.fontSize = 6.5 if len(days) > 14 else 7.5
    chart.categoryAxis.labels.fillColor = MUTED
    chart.categoryAxis.strokeColor = BORDER
    chart.valueAxis.valueMin = 0
    top = max(sum(v.values()) for _, v in days)
    chart.valueAxis.valueMax = max(4, top + 1)
    chart.valueAxis.valueStep = max(1, round(chart.valueAxis.valueMax / 4))
    chart.valueAxis.labels.fontName = "Helvetica"
    chart.valueAxis.labels.fontSize = 7
    chart.valueAxis.labels.fillColor = MUTED
    chart.valueAxis.strokeColor = BORDER
    chart.valueAxis.gridStrokeColor = TRACK
    chart.valueAxis.visibleGrid = True
    chart.bars[0].fillColor = RED
    chart.bars[1].fillColor = AMBER
    chart.bars.strokeColor = None
    chart.barSpacing = 1
    chart.groupSpacing = 6 if len(days) < 10 else 3
    drawing.add(chart)
    for i, (label, color) in enumerate((("Críticas", RED), ("Atenção", AMBER))):
        x = width - 120 + i * 60
        drawing.add(String(x + 9, height - 10, label, fontName="Helvetica", fontSize=7.5, fillColor=MUTED))
        drawing.add(Rect(x, height - 11, 6, 6, fillColor=color, strokeColor=None))
    return drawing


def _breakdown(title: str, rows: list[tuple[str, int]], total: int) -> Table:
    data = [[Paragraph(title.upper(), STYLES["label"]), Paragraph("QTD.", STYLES["label"]), Paragraph("%", STYLES["label"])]]
    for name, count in rows:
        pct = 100 * count / total if total else 0
        data.append([Paragraph(esc(name), STYLES["cell"]), Paragraph(str(count), STYLES["right"]), Paragraph(fmt_num(pct, 0) + "%", STYLES["right"])])
    if len(data) == 1:
        data.append([Paragraph("Sem registros", STYLES["muted"]), "", ""])
    table = Table(data, colWidths=[None, 14 * mm, 14 * mm])
    table.setStyle(
        TableStyle(
            [
                ("LINEBELOW", (0, 0), (-1, 0), 0.8, INK),
                ("LINEBELOW", (0, 1), (-1, -2), 0.4, BORDER),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    return table


def _episodes_table(episodes: list[dict], meta: ReportMeta) -> Table:
    header = ["Nº", "Início", "Unidade", "Distúrbio", "Nível", "Duração", "Tensão mín./máx.", "Comutou", "Conf. IA"]
    data = [[Paragraph(h.upper(), STYLES["label"]) for h in header]]
    styles = []
    for row, ep in enumerate(episodes, start=1):
        sev = ep["peak_severity"]
        sev_color = SEVERITY_COLOR.get(sev, INK).hexval().replace("0x", "#")
        confidence = "—" if ep["diagnosis_confidence"] is None else fmt_num(ep["diagnosis_confidence"], 1) + "%"
        if ep["low_confidence"]:
            confidence += " *"
        data.append(
            [
                Paragraph(f"#{ep['id']}", STYLES["cell"]),
                Paragraph(fmt_ts(ep["started_at"]).replace(" ", "<br/>"), STYLES["cell"]),
                Paragraph(esc(meta.site_names.get(ep["site_id"], ep["site_id"])), STYLES["cell"]),
                Paragraph(esc(code_label(ep["primary_code"])), STYLES["cell_bold"]),
                Paragraph(f'<font color="{sev_color}"><b>{SEVERITY_TEXT.get(sev, sev)}</b></font>', STYLES["cell"]),
                Paragraph(fmt_duration(ep["duration_s"]) + (" (em curso)" if ep["ongoing"] else ""), STYLES["cell"]),
                Paragraph(f"{fmt_num(ep['min_voltage'], 0)} / {fmt_num(ep['max_voltage'], 0)} V", STYLES["cell"]),
                Paragraph("Sim" if ep["transferred"] else "Não", STYLES["cell"]),
                Paragraph(confidence, STYLES["cell"]),
            ]
        )
        if row % 2 == 0:
            styles.append(("BACKGROUND", (0, row), (-1, row), colors.HexColor("#F8FAFC")))
    widths = [10 * mm, 22 * mm, 24 * mm, 31 * mm, 14 * mm, 19 * mm, 21 * mm, 16.5 * mm, None]
    table = Table(data, colWidths=widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("LINEBELOW", (0, 0), (-1, 0), 0.8, INK),
                ("LINEBELOW", (0, 1), (-1, -1), 0.3, BORDER),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                *styles,
            ]
        )
    )
    return table


def _critical_detail(ep: dict, meta: ReportMeta) -> KeepTogether:
    site = meta.site_names.get(ep["site_id"], ep["site_id"])
    head = Paragraph(
        f'<font color="#DC2626"><b>#{ep["id"]}</b></font>  <b>{esc(code_label(ep["primary_code"]))}</b>'
        f'  <font color="#64748B">· {esc(site)} · {fmt_ts(ep["started_at"])} · {fmt_duration(ep["duration_s"])}'
        f'{" · comutou para autoprodução" if ep["transferred"] else ""}</font>',
        STYLES["body"],
    )
    parts = [head, Spacer(1, 3)]
    if ep.get("diagnosis_cause"):
        label = "Hipótese da IA (confiança abaixo de 90%)" if ep["low_confidence"] else "Causa provável"
        parts.append(Paragraph(f"<b>{label}:</b> {esc(ep['diagnosis_cause'])}", STYLES["body"]))
    if ep.get("measurement"):
        parts.append(Paragraph(f"<b>Medição:</b> {esc(ep['measurement'])}", STYLES["muted"]))
    actions = ep.get("actions") or []
    if actions:
        lines = "".join(
            f"<br/>{'Imediata' if a['type'] == 'IMEDIATA' else 'Preventiva'}: {esc(a['action'])}" for a in actions[:4]
        )
        parts.append(Paragraph(f"<b>Ações recomendadas:</b>{lines}", STYLES["body"]))
    if ep.get("norm_references"):
        parts.append(Paragraph("Referências: " + esc("; ".join(ep["norm_references"][:4])), STYLES["muted"]))
    box = Table([[parts]], colWidths=[None])
    box.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.6, BORDER),
                ("LINEBEFORE", (0, 0), (0, -1), 2.2, RED),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return KeepTogether([box, Spacer(1, 7)])


def build_pdf(episodes: list[dict], stats: dict, meta: ReportMeta) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=32 * mm,
        bottomMargin=22 * mm,
        title="Relatório de ocorrências - Sentinela TO",
        author="Sentinela TO",
        subject=meta.period,
    )
    width = A4[0] - 36 * mm
    story = [
        Paragraph("Relatório de Ocorrências de Qualidade de Energia", STYLES["title"]),
        Paragraph(esc(f"{meta.period} · {meta.sites}"), STYLES["subtitle"]),
        Spacer(1, 12),
        _info_table(meta),
        Paragraph("Resumo executivo", STYLES["h2"]),
        _kpis(stats, meta),
        Spacer(1, 8),
    ]
    summary = (
        f"No período foram registradas <b>{stats['total']}</b> ocorrências, sendo <b>{stats['critical']}</b> críticas e "
        f"<b>{stats['warning']}</b> de atenção. A proteção automática comutou a planta para autoprodução "
        f"<b>{stats['transfers']}</b> vez(es)"
    )
    if stats["transfers"] and meta.loss_per_transfer_brl:
        summary += f", com perda evitada estimada em <b>{fmt_brl(stats['transfers'] * meta.loss_per_transfer_brl)}</b>"
    story.append(Paragraph(summary + ".", STYLES["body"]))

    chart = _daily_chart(stats, width)
    if chart:
        story += [Paragraph("Ocorrências por dia", STYLES["h2"]), chart]

    by_site = [(meta.site_names.get(k, k), v) for k, v in sorted(stats["by_site"].items(), key=lambda kv: -kv[1])]
    by_code = [(code_label(k), v) for k, v in stats["by_code"].items()]
    split = Table(
        [[_breakdown("Por unidade", by_site, stats["total"]), _breakdown("Por tipo de distúrbio", by_code, stats["total"])]],
        colWidths=[width / 2 - 4 * mm, width / 2 - 4 * mm],
        spaceBefore=6,
    )
    split.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (0, 0), 8 * mm)]))
    story += [Paragraph("Distribuição", STYLES["h2"]), split]

    story.append(Paragraph("Detalhamento das ocorrências", STYLES["h2"]))
    if episodes:
        story.append(_episodes_table(episodes, meta))
        if any(ep["low_confidence"] for ep in episodes):
            story.append(Spacer(1, 4))
            story.append(Paragraph("* Diagnóstico com confiança abaixo de 90%: tratado como hipótese e enviado à curadoria da engenharia.", STYLES["muted"]))
    else:
        story.append(Paragraph("Nenhuma ocorrência encontrada com os filtros selecionados.", STYLES["muted"]))

    critical = [ep for ep in episodes if ep["peak_severity"] == "CRITICAL"][:MAX_CRITICAL_DETAILS]
    if critical:
        story.append(Paragraph("Análise das ocorrências críticas", STYLES["h2"]))
        story += [_critical_detail(ep, meta) for ep in critical]
        hidden = sum(1 for ep in episodes if ep["peak_severity"] == "CRITICAL") - len(critical)
        if hidden > 0:
            story.append(Paragraph(f"Outras {hidden} ocorrências críticas constam no detalhamento acima.", STYLES["muted"]))

    story += [
        Spacer(1, 10),
        Paragraph(
            "Faixas de referência: PRODIST Módulo 8 (ANEEL) para Vn = 220 V — tensão adequada de 202 a 231 V, "
            "desequilíbrio até 3% e THD até 10%. Causas e ações são geradas pelo copiloto de diagnóstico a partir "
            "da base técnica (IEEE 1159, NBR 5410, PRODIST e manuais de fabricantes) e devem ser confirmadas em campo.",
            STYLES["muted"],
        ),
    ]
    doc.build(story, canvasmaker=lambda *a, **kw: NumberedCanvas(*a, meta=meta, **kw))
    return buffer.getvalue()


CSV_COLUMNS = [
    ("Nº", lambda ep, m: ep["id"]),
    ("Unidade", lambda ep, m: m.site_names.get(ep["site_id"], ep["site_id"])),
    ("Início", lambda ep, m: fmt_ts(ep["started_at"])),
    ("Fim", lambda ep, m: fmt_ts(ep["ended_at"]) if ep["ended_at"] else "em curso"),
    ("Duração (s)", lambda ep, m: fmt_num(ep["duration_s"], 1)),
    ("Severidade", lambda ep, m: SEVERITY_TEXT.get(ep["peak_severity"], ep["peak_severity"])),
    ("Distúrbio principal", lambda ep, m: code_label(ep["primary_code"])),
    ("Distúrbios", lambda ep, m: ", ".join(code_label(c) for c in ep["codes"])),
    ("Tensão mínima (V)", lambda ep, m: fmt_num(ep["min_voltage"], 1)),
    ("Tensão máxima (V)", lambda ep, m: fmt_num(ep["max_voltage"], 1)),
    ("Corrente máxima (A)", lambda ep, m: fmt_num(ep["max_current"], 1)),
    ("FP mínimo", lambda ep, m: fmt_num(ep["min_power_factor"], 2)),
    ("THD máximo (%)", lambda ep, m: fmt_num(ep["max_thd"], 1)),
    ("Comutou para autoprodução", lambda ep, m: "Sim" if ep["transferred"] else "Não"),
    ("Causa provável (IA)", lambda ep, m: ep.get("diagnosis_cause") or ""),
    ("Confiança IA (%)", lambda ep, m: fmt_num(ep["diagnosis_confidence"], 1)),
    ("Hipótese (abaixo de 90%)", lambda ep, m: "Sim" if ep["low_confidence"] else "Não"),
    ("Referências", lambda ep, m: "; ".join(ep.get("norm_references") or [])),
]


def build_csv(episodes: list[dict], meta: ReportMeta) -> bytes:
    """CSV com ';' e BOM UTF-8: abre direto no Excel em português com acentos corretos."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\r\n")
    writer.writerow([name for name, _ in CSV_COLUMNS])
    for ep in episodes:
        writer.writerow([get(ep, meta) for _, get in CSV_COLUMNS])
    return ("\ufeff" + buffer.getvalue()).encode("utf-8")
