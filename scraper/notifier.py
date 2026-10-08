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

Destinatários
-------------
O Secret ALERT_TO aceita VÁRIOS e-mails. Podem ser separados por vírgula, ponto e
vírgula, espaço ou quebra de linha (uma linha por pessoa), e também no formato
"Nome <email@empresa.com>". Endereços repetidos são enviados uma vez só e endereços
inválidos são ignorados com aviso no log. Se o servidor recusar ALGUNS destinatários,
o e-mail segue para os demais e o log informa quais foram recusados.
Todos os destinatários aparecem no campo "Para" do e-mail.

Conexão SMTP
------------
  - tempo máximo de 30 s para conectar/responder (antes dependia do timeout do
    sistema, ~2 minutos);
  - porta 465 usa SSL direto; as demais usam STARTTLS;
  - qualquer falha vira EmailDeliveryError, com mensagem em português e, nos erros
    de conexão, um diagnóstico de quais portas do servidor respondem.
Nenhuma mensagem de erro ou diagnóstico imprime senha, usuário completo ou os
valores dos Secrets (o GitHub também mascara esses valores nos logs).
"""

import ipaddress
import re
import smtplib
import socket
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import getaddresses
from html import escape

from . import config
from .nt_summary import summary_to_text, rollout_info, clean_document_url

TEST_BANNER = (
    "E-mail de TESTE: foi gerado com a Nota Técnica mais recente publicada nos portais oficiais. "
    "Nenhuma publicação nova foi detectada e nenhum dado do painel foi alterado."
)

SMTP_TIMEOUT = 30          # segundos para conectar e para cada resposta do servidor
_DIAG_TIMEOUT = 5          # segundos por porta no diagnóstico
_DIAG_PORTS = ((587, "STARTTLS (587)"), (465, "SSL (465)"), (25, "sem criptografia (25)"), (2525, "alternativa (2525)"))

# cores do bloco de destaque (fundo, texto, borda)
_HOMOLOG_COLORS = ("#fff8c5", "#7d4e00", "#d4a72c")
_PROD_COLORS = ("#ffebe9", "#a40e26", "#ff8182")
_UNKNOWN_COLORS = ("#f6f8fa", "#57606a", "#d0d7de")


class EmailDeliveryError(Exception):
    """Falha ao enviar o e-mail. A mensagem já vem pronta para ser mostrada no log."""


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


def _item_link(it):
    """Link do documento (ou da página do portal) SEM os marcadores temporários do portal
    (AspxAutoDetectCookieSupport etc.), que causam ERR_TOO_MANY_REDIRECTS no navegador.
    Também corrige itens antigos já gravados com o link "sujo"."""
    raw = it.get("doc_url") or it.get("link")
    return clean_document_url(raw) if raw else "#"


def _render_item_html(it, badge_text, badge_color):
    link = _item_link(it)
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
            lines.append(f"  Fonte: {_item_link(it)}")
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


# ---------------------------------------------------------------------------
# Destinatários
# ---------------------------------------------------------------------------
_RE_EMAIL = re.compile(r"^[A-Za-z0-9._%+\-']+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}$")


def parse_recipients(raw=None):
    """Lê os destinatários (ALERT_TO) e devolve (válidos, inválidos).

    Aceita vírgula, ponto e vírgula, espaço ou quebra de linha como separador, e o formato
    "Nome <email@empresa.com>". Remove repetidos (sem diferenciar maiúsculas/minúsculas),
    mantendo a ordem em que aparecem. `raw` pode ser texto ou lista; sem argumento usa
    config.ALERT_TO."""
    if raw is None:
        raw = config.ALERT_TO
    if isinstance(raw, str):
        raw = [raw]
    text = ",".join(str(r) for r in (raw or []))
    text = re.sub(r"[;\r\n\t]+", ",", text)
    # "a@x.com b@x.com" (separados só por espaço) -> vírgula; "Nome <a@x.com>" fica como está
    text = re.sub(r"(?<=[A-Za-z0-9>])\s+(?=[A-Za-z0-9._%+\-']+@)", ",", text)
    # o Python 3.12 rejeita a lista INTEIRA se houver vírgula vazia (",,", ou vírgula nas pontas)
    text = re.sub(r",(?:\s*,)+", ",", text).strip(" ,")

    valid, invalid, seen = [], [], set()
    for _, addr in getaddresses([text]):
        addr = (addr or "").strip().strip(",")
        if not addr:
            continue
        if not _RE_EMAIL.match(addr):
            invalid.append(addr)
            continue
        key = addr.lower()
        if key not in seen:
            seen.add(key)
            valid.append(addr)
    # sobras que o parser não reconheceu como endereço (ex.: texto solto sem "@")
    leftovers = [t.strip() for t in text.split(",") if t.strip() and "@" not in t]
    invalid += [t for t in leftovers if t not in invalid]
    return valid, invalid


def _mask_email(addr):
    if "@" not in addr:
        return "***"
    local, domain = addr.rsplit("@", 1)
    return f"{local[:2]}***@{domain}"


# ---------------------------------------------------------------------------
# Configuração e diagnóstico de conexão SMTP
# ---------------------------------------------------------------------------
def _smtp_settings():
    """(host, porta, usuário, senha) já sem espaços/quebras de linha nas pontas
    (comum ao colar o valor de um Secret)."""
    host = (config.SMTP_HOST or "").strip()
    user = (config.SMTP_USER or "").strip()
    password = (config.SMTP_PASS or "").strip()
    return host, int(config.SMTP_PORT), user, password


def _port_name(port):
    if port == 587:
        return "STARTTLS, padrão recomendado"
    if port == 465:
        return "SSL direto"
    if port == 25:
        return "sem criptografia - o GitHub bloqueia esta porta"
    return "porta não padrão"


def _host_kind(host):
    low = host.lower()
    if low.endswith(("gmail.com", "google.com")):
        return "Gmail/Google"
    if low.endswith(("office365.com", "outlook.com")):
        return "Microsoft 365/Outlook"
    return "outro servidor"


def _mask_user(user):
    if "@" not in user:
        return "preenchido" if user else "VAZIO"
    return _mask_email(user)


def _config_hints(host, port):
    """Problemas de configuração que dá para detectar só olhando os valores."""
    hints = []
    if not host:
        return ["SMTP_HOST está vazio."]
    if re.search(r"[:/\s]", host):
        hints.append("SMTP_HOST deve ter só o nome do servidor (ex.: smtp.gmail.com), sem https://, sem porta e sem espaços.")
    else:
        internal = False
        try:
            internal = ipaddress.ip_address(host).is_private
        except ValueError:
            internal = "." not in host or bool(re.search(r"\.(local|lan|corp|internal|intra|home)$", host, re.I))
        if internal:
            hints.append("SMTP_HOST parece ser um servidor da rede interna da empresa: o GitHub (nuvem) não consegue acessá-lo. "
                         "Use um servidor público, como smtp.gmail.com.")
    if port == 25:
        hints.append("A porta 25 é bloqueada nos servidores do GitHub. Use SMTP_PORT = 587.")
    return hints


def describe_recipients():
    """Linhas de log com os destinatários que vão receber o e-mail (endereços parcialmente ocultos)."""
    valid, invalid = parse_recipients()
    lines = [f"  - ALERT_TO: {len(valid)} destinatário(s) válido(s)" + (": " + ", ".join(_mask_email(a) for a in valid) if valid else "")]
    if invalid:
        lines.append(f"  ! ALERT_TO tem {len(invalid)} valor(es) que não parecem e-mail e serão ignorados. "
                     "Separe os e-mails por vírgula, ponto e vírgula ou uma linha para cada.")
    return lines


def describe_settings():
    """Resumo da configuração de e-mail para o log. Não mostra senha nem valores dos Secrets."""
    host, port, user, password = _smtp_settings()
    lines = ["Configuração de e-mail em uso (valores sensíveis não são exibidos):",
             f"  - SMTP_HOST: {'preenchido (' + _host_kind(host) + ')' if host else 'VAZIO'}",
             f"  - SMTP_PORT: {_port_name(port)}",
             f"  - SMTP_USER: {_mask_user(user)}",
             f"  - SMTP_PASS: {'preenchido' if password else 'VAZIO'}",
             f"  - ALERT_FROM: {'preenchido' if (config.ALERT_FROM or '').strip() else 'VAZIO'}"]
    lines += describe_recipients()
    lines += [f"  ! {h}" for h in _config_hints(host, port)]
    return lines


def _tcp_check(host, port, timeout=_DIAG_TIMEOUT):
    """Tenta abrir uma conexão TCP e descreve o resultado em português."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return "acessível"
    except socket.gaierror:
        return "nome do servidor não encontrado (DNS)"
    except ConnectionRefusedError:
        return "recusada pelo servidor"
    except (socket.timeout, TimeoutError):
        return "sem resposta (tempo esgotado)"
    except OSError as exc:
        return f"falha ({exc.strerror or type(exc).__name__})"


def diagnose_connection(host, port):
    """Linhas de log com o resultado da conexão com o servidor nas portas de e-mail."""
    lines = ["Diagnóstico da conexão com o servidor de e-mail:"]
    try:
        socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError):
        lines.append("  - Nome do servidor (DNS): NÃO encontrado. Confira o valor de SMTP_HOST "
                     "(sem espaços, sem https://, sem porta) ou use um servidor público, como smtp.gmail.com.")
        return lines
    lines.append("  - Nome do servidor (DNS): encontrado")

    results = {p: _tcp_check(host, p) for p in dict.fromkeys([port] + [p for p, _ in _DIAG_PORTS])}
    lines.append(f"  - Porta configurada em SMTP_PORT ({_port_name(port)}): {results[port]}")
    lines += [f"  - {label}: {results[p]}" for p, label in _DIAG_PORTS if p != port]

    if results[port] != "acessível":
        if results.get(587) == "acessível":
            lines.append("  => Ajuste SMTP_PORT para 587 (STARTTLS): essa porta responde neste servidor.")
        elif results.get(465) == "acessível":
            lines.append("  => A porta 465 responde neste servidor: use SMTP_PORT = 465 (SSL).")
        else:
            lines.append("  => Nenhuma porta de envio (587/465) respondeu: SMTP_HOST provavelmente está errado "
                         "ou é um servidor interno que o GitHub não alcança. Para Gmail use smtp.gmail.com.")
    return lines


def _delivery_error(*blocks):
    return EmailDeliveryError("\n".join(b for b in blocks if b))


def send_alert(new_items, updated_items, test_mode=False):
    """Envia e-mail de alerta para TODOS os destinatários de ALERT_TO.

    Retorna True se o e-mail foi aceito pelo servidor para pelo menos um destinatário, e False se
    não havia nada a enviar ou se as credenciais SMTP/destinatários não estão configurados
    (apenas avisa no log, para não quebrar o pipeline em ambientes de teste/desenvolvimento).

    Levanta EmailDeliveryError (com mensagem em português) se o envio falhar. Se o servidor
    recusar apenas ALGUNS destinatários, o envio continua para os demais e o log informa
    quais foram recusados."""
    if not new_items and not updated_items:
        print("[notifier] Nenhuma novidade, e-mail não será enviado.")
        return False

    host, port, user, password = _smtp_settings()
    recipients, invalid = parse_recipients()
    if invalid:
        print(f"[notifier] AVISO: {len(invalid)} valor(es) de ALERT_TO não parecem e-mail e foram ignorados "
              "(separe os e-mails por vírgula, ponto e vírgula ou uma linha para cada).")
    if not (host and user and password and recipients):
        print(
            "[notifier] Variáveis de SMTP/ALERT_TO não configuradas ou sem e-mail válido - pulando "
            "envio de e-mail (configure os Secrets no GitHub para habilitar)."
        )
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = _build_subject(new_items, updated_items, test_mode)
    msg["From"] = config.ALERT_FROM
    msg["To"] = ", ".join(recipients)
    msg.attach(MIMEText(_build_text(new_items, updated_items, test_mode), "plain", "utf-8"))
    msg.attach(MIMEText(_build_html(new_items, updated_items, test_mode), "html", "utf-8"))

    context = ssl.create_default_context()
    refused = {}
    try:
        if port == 465:  # SSL direto
            server = smtplib.SMTP_SSL(host, port, timeout=SMTP_TIMEOUT, context=context)
        else:            # STARTTLS
            server = smtplib.SMTP(host, port, timeout=SMTP_TIMEOUT)
        with server:
            if port != 465:
                server.starttls(context=context)
            server.login(user, password)
            # devolve {} se todos foram aceitos, ou {endereço: (código, motivo)} dos recusados
            refused = server.sendmail(config.ALERT_FROM, recipients, msg.as_string()) or {}
    except smtplib.SMTPAuthenticationError as exc:
        raise _delivery_error(
            f"Autenticação recusada pelo servidor (código {exc.smtp_code}). A conexão funciona, "
            "mas usuário/senha não foram aceitos.",
            "  - Gmail: use uma SENHA DE APP de 16 letras (não a senha normal) e SMTP_USER com o e-mail completo.",
            "  - Microsoft 365: o SMTP AUTH pode estar desabilitado para a conta/organização.",
        ) from exc
    except smtplib.SMTPRecipientsRefused as exc:
        raise _delivery_error(
            f"O servidor recusou TODOS os {len(recipients)} destinatário(s). Confira os e-mails em ALERT_TO.",
            *[f"  - {_mask_email(a)}: código {code}" for a, (code, _) in exc.recipients.items()],
        ) from exc
    except smtplib.SMTPSenderRefused as exc:
        raise _delivery_error(
            "O servidor recusou o remetente. Confira ALERT_FROM (precisa ser o mesmo e-mail de SMTP_USER).",
            f"Detalhe do servidor: {type(exc).__name__}",
        ) from exc
    except smtplib.SMTPServerDisconnected as exc:
        raise _delivery_error(
            "O servidor encerrou a conexão antes de concluir o envio. Em geral é combinação errada de servidor/porta.",
            "  - Gmail: SMTP_HOST = smtp.gmail.com e SMTP_PORT = 587 (ou 465).",
        ) from exc
    except smtplib.SMTPException as exc:
        raise _delivery_error(f"O servidor de e-mail retornou um erro: {type(exc).__name__}: {exc}") from exc
    except OSError as exc:  # timeout, conexão recusada, DNS, falha de TLS
        reason = "tempo esgotado" if isinstance(exc, (socket.timeout, TimeoutError)) else (exc.strerror or type(exc).__name__)
        raise _delivery_error(
            f"Não foi possível conectar ao servidor de e-mail ({reason}). A senha nem chegou a ser testada.",
            *describe_settings(),
            *diagnose_connection(host, port),
        ) from exc

    accepted = [a for a in recipients if a not in refused]
    print(f"[notifier] E-mail enviado para {len(accepted)} de {len(recipients)} destinatário(s): "
          + ", ".join(_mask_email(a) for a in accepted)
          + f" ({len(new_items) + len(updated_items)} itens).")
    if refused:
        print(f"[notifier] AVISO: o servidor recusou {len(refused)} destinatário(s): "
              + ", ".join(f"{_mask_email(a)} (código {code})" for a, (code, _) in refused.items())
              + ". Confira esses endereços em ALERT_TO.")
    return True
