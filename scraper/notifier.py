"""Envio de e-mails de alerta quando novas Notas Técnicas / atualizações são detectadas.

Estrutura do e-mail:
  1. DATAS DE IMPLANTAÇÃO em destaque, logo no início: para cada NT, a data do
     ambiente de HOMOLOGAÇÃO (coluna "Implantação Teste" do cronograma da NT) e a
     do ambiente de PRODUÇÃO (coluna "Implantação Produção"), extraídas do documento;
  2. (somente no modo teste) aviso de que é um e-mail de teste;
  3. para cada publicação: resumo do portal, "Resumo das alterações" (ver
     scraper/nt_summary.py) e link para o documento.

As mesmas datas vão no texto de pré-visualização (aparece ao lado do assunto na
caixa de entrada) e no início da versão em texto puro. Quando o documento diz
"Até 05/10/2026", o "até" aparece junto da data.

Com test_mode=True (usado por scraper/send_test_email.py) o e-mail sai marcado
como [TESTE], com um aviso no corpo e sem o selo "NOVO".
"""

import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape

from . import config
from .nt_summary import summary_to_text, rollout_info

TEST_BANNER = (
    "E-mail de TESTE: foi gerado com a Nota Técnica mais recente publicada nos portais oficiais. "
    "Nenhuma publicação nova foi detectada e nenhum dado do painel foi alterado."
)

# cores do bloco de destaque (fundo, texto, borda)
_HOMOLOG_COLORS = ("#fff8c5", "#7d4e00", "#d4a72c")
_PROD_COLORS = ("#ffebe9", "#a40e26", "#ff8182")
_UNKNOWN_COLORS = ("#f6f8fa", "#57606a", "#d0d7de")


def _fmt_date(iso):
    """yyyy-mm-dd -> dd/mm/aaaa"""
    try:
        y, m, d = iso.split("-")
        return f"{d}/{m}/{y}"
    except (ValueError, AttributeError):
        return iso or "-"


def _all_items(new_items, updated_items):
    return list(new_items) + list(updated_items)


def _unknown_reason(cs):
    """Texto mostrado no lugar de uma data que não foi encontrada."""
    if cs and cs.get("status") == "ok":
        return "não informada no documento"
    return "indisponível - confira o PDF"


def _with_note(note, date):
    return f"{note} {date}" if note else date


# ---------------------------------------------------------------------------
# Bloco de destaque: datas de homologação e produção
# ---------------------------------------------------------------------------
def _date_cell_html(label, date, note, colors, unknown_reason):
    bg, fg, border = colors if date else _UNKNOWN_COLORS
    if date:
        prefix = f'<span style="font-size:12px;font-weight:600;">{escape(note)} </span>' if note else ""
        value = (f'<div style="font-size:22px;line-height:26px;font-weight:700;color:{fg};white-space:nowrap;">'
                 f"{prefix}{escape(date)}</div>")
    else:
        value = f'<div style="font-size:12px;line-height:18px;padding:4px 0;color:{fg};">{escape(unknown_reason)}</div>'
    return (
        f'<td width="30%" valign="top" style="background:{bg};border:1px solid {border};border-radius:6px;padding:8px 12px;">'
        f'<div style="font-size:11px;font-weight:700;letter-spacing:.06em;color:{fg};">{label}</div>{value}</td>'
    )


def _rollout_panel_html(new_items, updated_items):
    items = _all_items(new_items, updated_items)
    if not items:
        return ""
    rows = []
    for it in items:
        cs = it.get("change_summary")
        info = rollout_info(cs)
        reason = _unknown_reason(cs)
        source_line = (
            f'<div style="font-size:11px;color:#57606a;margin-top:2px;">cronograma da v{escape(str(info["schedule_version"]))} do documento</div>'
            if info["schedule_version"] else ""
        )
        rows.append(
            "<tr>"
            f'<td valign="middle" style="padding:6px 8px 6px 0;font-size:14px;">'
            f'<strong>[{escape(it["document"])}]</strong> {escape(it["title"])}{source_line}</td>'
            + _date_cell_html("HOMOLOGAÇÃO", info["homologation"], info["homologation_note"], _HOMOLOG_COLORS, reason)
            + '<td width="8"></td>'
            + _date_cell_html("PRODUÇÃO", info["production"], info["production_note"], _PROD_COLORS, reason)
            + "</tr>"
        )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="border:2px solid #0969da;border-radius:8px;border-collapse:separate;margin:0 0 16px;">'
        '<tr><td style="background:#0969da;color:#ffffff;padding:8px 14px;font-weight:700;font-size:14px;letter-spacing:.04em;">'
        "📅 DATAS DE IMPLANTAÇÃO DA NOTA TÉCNICA</td></tr>"
        '<tr><td style="padding:10px 14px;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="border-collapse:separate;border-spacing:0 6px;">'
        + "".join(rows)
        + "</table>"
        '<div style="font-size:11px;color:#57606a;margin-top:2px;">Homologação = "Implantação Teste" e Produção = "Implantação Produção" '
        "do cronograma da NT. Datas extraídas automaticamente do documento: confira no PDF antes de planejar a implantação.</div>"
        "</td></tr></table>"
    )


def _rollout_text(new_items, updated_items):
    items = _all_items(new_items, updated_items)
    if not items:
        return []
    lines = ["=== DATAS DE IMPLANTAÇÃO DA NOTA TÉCNICA ==="]
    for it in items:
        cs = it.get("change_summary")
        info = rollout_info(cs)
        reason = _unknown_reason(cs)
        version = f" (cronograma da v{info['schedule_version']})" if info["schedule_version"] else ""
        lines.append(f"[{it['document']}] {it['title']}{version}")
        h = _with_note(info["homologation_note"], info["homologation"]) if info["homologation"] else reason
        p = _with_note(info["production_note"], info["production"]) if info["production"] else reason
        lines.append(f"  HOMOLOGAÇÃO: {h}")
        lines.append(f"  PRODUÇÃO:    {p}")
    lines.append('(Homologação = "Implantação Teste"; Produção = "Implantação Produção". Datas extraídas automaticamente do documento da NT. Confira no PDF.)')
    lines.append("")
    return lines


def _preheader_text(new_items, updated_items):
    """Texto curto que a caixa de entrada mostra ao lado do assunto."""
    items = _all_items(new_items, updated_items)
    if not items:
        return ""
    first = items[0]
    info = rollout_info(first.get("change_summary"))
    h = _with_note(info["homologation_note"], info["homologation"]) if info["homologation"] else "não informada"
    p = _with_note(info["production_note"], info["production"]) if info["production"] else "não informada"
    text = " · ".join([f"Homologação: {h}", f"Produção: {p}", f"{first['document']} {first['title']}"])
    if len(items) > 1:
        text += f" (+{len(items) - 1} NT)"
    return text


# ---------------------------------------------------------------------------
# Resumo das alterações e cartões de cada publicação
# ---------------------------------------------------------------------------
def _other_deadlines(cs):
    """Prazos que NÃO são homologação/produção (esses já estão no destaque do topo)."""
    out = []
    for line in cs.get("deadlines") or []:
        low = line.lower()
        if low.startswith(("homologação:", "homologacao:", "produção:", "producao:")):
            continue
        out.append(line)
    return out


def _summary_html(cs):
    if not cs:
        return ""
    if cs.get("status") != "ok":
        return (
            '<p style="margin:10px 0 0;font-size:12px;color:#57606a;">'
            f"Resumo detalhado indisponível: {escape(cs.get('reason', ''))}. "
            "Consulte o documento original.</p>"
        )
    parts = ['<div style="margin-top:12px;padding:10px 12px;background:#f6f8fa;border-left:4px solid #0969da;border-radius:4px;">']
    parts.append('<div style="font-weight:700;margin-bottom:6px;">📝 Resumo das alterações propostas</div>')
    if cs.get("purpose"):
        parts.append(f'<p style="margin:0 0 8px;"><strong>Objetivo:</strong> {escape(cs["purpose"])}</p>')
    for sec in cs.get("sections", []):
        parts.append(f'<div style="margin:6px 0 2px;font-weight:600;">{escape(sec["title"])}</div><ul style="margin:0 0 6px 18px;padding:0;">')
        parts.extend(f'<li style="margin-bottom:3px;">{escape(it)}</li>' for it in sec["items"])
        parts.append("</ul>")
    if cs.get("elements"):
        parts.append(f'<p style="margin:6px 0;"><strong>Campos/grupos citados:</strong> {escape(", ".join(cs["elements"]))}</p>')
    if cs.get("rules"):
        parts.append(f'<p style="margin:6px 0;"><strong>Regras/rejeições citadas:</strong> {escape(", ".join(cs["rules"]))}</p>')
    others = _other_deadlines(cs)
    if others:
        parts.append(f'<p style="margin:6px 0;"><strong>Outras datas citadas:</strong> {escape(" | ".join(others))}</p>')
    parts.append(
        '<p style="margin:8px 0 0;font-size:11px;color:#57606a;">Resumo gerado automaticamente a partir do texto do '
        "documento. Confira o PDF original antes de implementar.</p></div>"
    )
    return "".join(parts)


def _render_item_html(it, badge_text, badge_color):
    link = it.get("doc_url") or it.get("link") or "#"
    link_label = "Abrir Nota Técnica" if it.get("doc_url") else "Abrir fonte"
    previous = ""
    if it.get("previous_version"):
        previous = (
            f'<p style="margin:6px 0 0;font-size:12px;color:#57606a;">Versão anterior: v{escape(str(it["previous_version"]))}'
            f' — {escape(it.get("previous_summary") or "")}</p>'
        )
    return f"""
    <div style="border:1px solid #d0d7de;border-radius:8px;padding:14px;margin:12px 0;">
      <div>
        <span style="background:{badge_color};color:#fff;border-radius:4px;padding:2px 6px;font-size:11px;font-weight:700;">{badge_text}</span>
        <strong>[{escape(it['document'])}]</strong> {escape(it['title'])}
        <span style="color:#57606a;white-space:nowrap;">· Publicada em {_fmt_date(it.get('date'))}</span>
      </div>
      <p style="margin:8px 0 0;">{escape(it.get('summary') or '')}</p>
      {previous}
      {_summary_html(it.get('change_summary'))}
      <p style="margin:10px 0 0;"><a href="{escape(link)}">{link_label} ↗</a></p>
    </div>
    """


# ---------------------------------------------------------------------------
# Montagem do e-mail
# ---------------------------------------------------------------------------
def _build_html(new_items, updated_items, test_mode=False):
    new_badge = ("ÚLTIMA NT · TESTE", "#57606a") if test_mode else ("NOVO", "#1a7f37")
    cards = "".join(_render_item_html(it, *new_badge) for it in new_items)
    cards += "".join(_render_item_html(it, "NOVA VERSÃO", "#9a6700") for it in updated_items)
    total = len(new_items) + len(updated_items)

    banner = (
        f'<div style="background:#fff8c5;border:1px solid #d4a72c;border-radius:6px;padding:10px 12px;margin:0 0 12px;">🧪 {escape(TEST_BANNER)}</div>'
        if test_mode else ""
    )
    intro = (
        "<p>Prévia do alerta com a Nota Técnica mais recente publicada.</p>"
        if test_mode else
        f"<p>Foram detectadas <strong>{total}</strong> publicação(ões) nova(s) ou atualizada(s) nas fontes oficiais monitoradas.</p>"
    )
    preheader = _preheader_text(new_items, updated_items)
    hidden_preheader = (
        f'<div style="display:none;font-size:1px;line-height:1px;max-height:0;max-width:0;opacity:0;overflow:hidden;mso-hide:all;">'
        f"{escape(preheader)}</div>"
        if preheader else ""
    )
    return f"""
    <html>
    <body style="font-family:Segoe UI,Arial,sans-serif;color:#1f2328;font-size:14px;">
      {hidden_preheader}
      <h2 style="margin:0 0 12px;">🚨 Radar Fiscal - NF-e / CT-e / MDF-e</h2>
      {_rollout_panel_html(new_items, updated_items)}
      {banner}
      {intro}
      {cards}
      <p style="margin-top:16px;font-size:12px;color:#666;">
        E-mail gerado automaticamente pelo Radar Fiscal. O painel completo está no GitHub Pages do projeto.
      </p>
    </body>
    </html>
    """


def _build_text(new_items, updated_items, test_mode=False):
    lines = []
    lines += _rollout_text(new_items, updated_items)
    if test_mode:
        lines += [f"*** {TEST_BANNER} ***", ""]
    lines += ["Radar Fiscal - NF-e / CT-e / MDF-e", ""]
    new_label = "ÚLTIMA NT - TESTE" if test_mode else "NOVO"
    for label, items in ((new_label, new_items), ("NOVA VERSAO", updated_items)):
        for it in items:
            lines.append(f"[{label}] {_fmt_date(it.get('date'))} - [{it['document']}] {it['title']}")
            lines.append(f"  Portal: {it.get('summary') or ''}")
            text_summary = summary_to_text(it.get("change_summary"))
            if text_summary:
                lines.append("  --- Resumo das alterações ---")
                lines.extend("  " + ln for ln in text_summary.splitlines())
            lines.append(f"  Fonte: {it.get('doc_url') or it.get('link')}")
            lines.append("")
    return "\n".join(lines)


def _build_subject(new_items, updated_items, test_mode=False):
    total = len(new_items) + len(updated_items)
    if total == 1:
        it = (new_items or updated_items)[0]
        subject = f"[Radar Fiscal] {it['document']}: {it['title']}"
    else:
        subject = f"[Radar Fiscal] {total} nova(s) publicação(ões) NFe/CTe/MDFe"
    return f"[TESTE] {subject}" if test_mode else subject


def send_alert(new_items, updated_items, test_mode=False):
    """Envia e-mail de alerta. Não faz nada (apenas avisa no log) se as
    credenciais SMTP não estiverem configuradas, para não quebrar o pipeline
    em ambientes de teste/desenvolvimento. Retorna True se o e-mail foi enviado."""
    if not new_items and not updated_items:
        print("[notifier] Nenhuma novidade, e-mail não será enviado.")
        return False

    if not (config.SMTP_HOST and config.SMTP_USER and config.SMTP_PASS and config.ALERT_TO):
        print(
            "[notifier] Variáveis de SMTP/ALERT_TO não configuradas - pulando "
            "envio de e-mail (configure os Secrets no GitHub para habilitar)."
        )
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = _build_subject(new_items, updated_items, test_mode)
    msg["From"] = config.ALERT_FROM
    msg["To"] = ", ".join(config.ALERT_TO)
    msg.attach(MIMEText(_build_text(new_items, updated_items, test_mode), "plain", "utf-8"))
    msg.attach(MIMEText(_build_html(new_items, updated_items, test_mode), "html", "utf-8"))

    context = ssl.create_default_context()
    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT) as server:
        server.starttls(context=context)
        server.login(config.SMTP_USER, config.SMTP_PASS)
        server.sendmail(config.ALERT_FROM, config.ALERT_TO, msg.as_string())

    print(f"[notifier] E-mail enviado para {config.ALERT_TO} ({len(new_items) + len(updated_items)} itens).")
    return True
