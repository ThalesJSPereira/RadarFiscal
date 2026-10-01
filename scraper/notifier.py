"""Envio de e-mails de alerta quando novas Notas Técnicas / atualizações são detectadas."""

import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from . import config


def _build_html(new_items, updated_items):
    def render_item(it, badge):
        return f"""
        <tr>
          <td style="padding:8px 10px;border-bottom:1px solid #eee;white-space:nowrap;">{it['date']}</td>
          <td style="padding:8px 10px;border-bottom:1px solid #eee;">
            <span style="background:{badge[1]};color:#fff;border-radius:4px;padding:2px 6px;font-size:11px;">{badge[0]}</span>
            <strong>[{it['document']}]</strong> {it['title']}
          </td>
          <td style="padding:8px 10px;border-bottom:1px solid #eee;">{it['summary']}</td>
          <td style="padding:8px 10px;border-bottom:1px solid #eee;"><a href="{it['link']}">Abrir fonte</a></td>
        </tr>
        """

    rows = ""
    for it in new_items:
        rows += render_item(it, ("NOVO", "#1a7f37"))
    for it in updated_items:
        rows += render_item(it, ("NOVA VERSÃO", "#9a6700"))

    html = f"""
    <html>
    <body style="font-family:Segoe UI,Arial,sans-serif;color:#1f2328;">
      <h2>🚨 Radar Fiscal - NF-e / CT-e / MDF-e</h2>
      <p>Foram detectadas <strong>{len(new_items) + len(updated_items)}</strong> publicações
      novas ou atualizadas nas fontes oficiais monitoradas.</p>
      <table style="border-collapse:collapse;width:100%;font-size:13px;">
        <thead>
          <tr style="background:#f6f8fa;text-align:left;">
            <th style="padding:8px 10px;">Data</th>
            <th style="padding:8px 10px;">Documento</th>
            <th style="padding:8px 10px;">O que muda</th>
            <th style="padding:8px 10px;">Fonte</th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
      <p style="margin-top:16px;font-size:12px;color:#666;">
        Este e-mail foi gerado automaticamente pelo monitor de NFe/CTe/MDFe.
        Painel completo disponível no GitHub Pages do projeto.
      </p>
    </body>
    </html>
    """
    return html


def _build_text(new_items, updated_items):
    lines = ["Radar Fiscal - NF-e / CT-e / MDF-e", ""]
    for it in new_items:
        lines.append(f"[NOVO] {it['date']} - [{it['document']}] {it['title']}")
        lines.append(f"  Resumo: {it['summary']}")
        lines.append(f"  Fonte: {it['link']}")
        lines.append("")
    for it in updated_items:
        lines.append(f"[NOVA VERSAO] {it['date']} - [{it['document']}] {it['title']}")
        lines.append(f"  Resumo: {it['summary']}")
        lines.append(f"  Fonte: {it['link']}")
        lines.append("")
    return "\n".join(lines)


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

    total = len(new_items) + len(updated_items)
    subject = f"[Radar Fiscal] {total} nova(s) publicação(ões) NFe/CTe/MDFe"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = config.ALERT_FROM
    msg["To"] = ", ".join(config.ALERT_TO)

    msg.attach(MIMEText(_build_text(new_items, updated_items), "plain", "utf-8"))
    msg.attach(MIMEText(_build_html(new_items, updated_items), "html", "utf-8"))

    context = ssl.create_default_context()
    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT) as server:
        server.starttls(context=context)
        server.login(config.SMTP_USER, config.SMTP_PASS)
        server.sendmail(config.ALERT_FROM, config.ALERT_TO, msg.as_string())

    print(f"[notifier] E-mail enviado para {config.ALERT_TO} ({total} itens).")
