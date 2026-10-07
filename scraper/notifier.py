"""Envio de e-mails de alerta quando novas Notas Técnicas / atualizações são detectadas.

O e-mail traz, para cada publicação nova, o resumo oficial do portal e (quando
disponível) o "Resumo das alterações" extraído do documento da NT
(ver scraper/nt_summary.py).
"""

import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from html import escape

from . import config
from .nt_summary import summary_to_text


def _fmt_date(iso):
    """yyyy-mm-dd -> dd/mm/aaaa"""
    try:
        y, m, d = iso.split("-")
        return f"{d}/{m}/{y}"
    except (ValueError, AttributeError):
        return iso or "-"


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
    if cs.get("deadlines"):
        parts.append(f'<p style="margin:6px 0;"><strong>Prazos:</strong> {escape(" | ".join(cs["deadlines"]))}</p>')
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


def _build_html(new_items, updated_items):
    body = "".join(_render_item_html(it, "NOVO", "#1a7f37") for it in new_items)
    body += "".join(_render_item_html(it, "NOVA VERSÃO", "#9a6700") for it in updated_items)
    total = len(new_items) + len(updated_items)
    return f"""
    <html>
    <body style="font-family:Segoe UI,Arial,sans-serif;color:#1f2328;font-size:14px;">
      <h2>🚨 Radar Fiscal - NF-e / CT-e / MDF-e</h2>
      <p>Foram detectadas <strong>{total}</strong> publicação(ões) nova(s) ou atualizada(s) nas fontes oficiais monitoradas.</p>
      {body}
      <p style="margin-top:16px;font-size:12px;color:#666;">
        E-mail gerado automaticamente pelo Radar Fiscal. O painel completo está no GitHub Pages do projeto.
      </p>
    </body>
    </html>
    """


def _build_text(new_items, updated_items):
    lines = ["Radar Fiscal - NF-e / CT-e / MDF-e", ""]
    for label, items in (("NOVO", new_items), ("NOVA VERSAO", updated_items)):
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


def _build_subject(new_items, updated_items):
    total = len(new_items) + len(updated_items)
    if total == 1:
        it = (new_items or updated_items)[0]
        return f"[Radar Fiscal] {it['document']}: {it['title']}"
    return f"[Radar Fiscal] {total} nova(s) publicação(ões) NFe/CTe/MDFe"


def send_alert(new_items, updated_items):
    """Envia e-mail de alerta. Não faz nada (apenas avisa no log) se as
    credenciais SMTP não estiverem configuradas, para não quebrar o pipeline
    em ambientes de teste/desenvolvimento."""
    if not new_items and not updated_items:
        print("[notifier] Nenhuma novidade, e-mail não será enviado.")
        return

    if not (config.SMTP_HOST and config.SMTP_USER and config.SMTP_PASS and config.ALERT_TO):
        print(
            "[notifier] Variáveis de SMTP/ALERT_TO não configuradas - pulando "
            "envio de e-mail (configure os Secrets no GitHub para habilitar)."
        )
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = _build_subject(new_items, updated_items)
    msg["From"] = config.ALERT_FROM
    msg["To"] = ", ".join(config.ALERT_TO)
    msg.attach(MIMEText(_build_text(new_items, updated_items), "plain", "utf-8"))
    msg.attach(MIMEText(_build_html(new_items, updated_items), "html", "utf-8"))

    context = ssl.create_default_context()
    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT) as server:
        server.starttls(context=context)
        server.login(config.SMTP_USER, config.SMTP_PASS)
        server.sendmail(config.ALERT_FROM, config.ALERT_TO, msg.as_string())

    print(f"[notifier] E-mail enviado para {config.ALERT_TO} ({len(new_items) + len(updated_items)} itens).")
