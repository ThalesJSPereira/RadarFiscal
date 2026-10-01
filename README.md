# 📡 Radar Fiscal — Monitor de NF-e / CT-e / MDF-e

Monitoramento automático de **Notas Técnicas, Informes e Manuais** publicados
nas fontes oficiais do governo para NF-e/NFC-e, CT-e/BP-e e MDF-e. Quando uma
publicação nova (ou uma **nova versão** de uma Nota Técnica já conhecida) é
detectada, o sistema:

1. Atualiza um painel web (hospedado gratuitamente no **GitHub Pages**);
2. Envia um **e-mail de alerta** com o resumo do que está sendo alterado na
   estrutura do documento fiscal.

Todo o projeto roda dentro do próprio GitHub: o **GitHub Actions** faz a
coleta periódica (não precisa de servidor, VPS ou nuvem paga) e o
**GitHub Pages** hospeda o painel.

---

## Fontes oficiais monitoradas

| Documento | Fonte | URL |
|---|---|---|
| NF-e / NFC-e | Portal Nacional da NF-e — Notas Técnicas | `nfe.fazenda.gov.br/portal/listaConteudo.aspx?tipoConteudo=04BIflQt1aY=` |
| NF-e / NFC-e | Portal Nacional da NF-e — Informes (home) | `nfe.fazenda.gov.br/portal/principal.aspx` |
| CT-e / BP-e | Portal do CT-e — Notas Técnicas | `cte.fazenda.gov.br/portal/listaConteudo.aspx?tipoConteudo=Y0nErnoZpsg=` |
| MDF-e | Portal do MDF-e (SVRS) — Documentos | `dfe-portal.svrs.rs.gov.br/Mdfe/Documentos` |

Novas fontes (ex.: CONFAZ, Diário Oficial da União, ENCAT) podem ser
adicionadas facilmente em `scraper/config.py` (veja a seção
[Como adicionar uma nova fonte](#como-adicionar-uma-nova-fonte)).

## Como funciona (arquitetura)

```
┌────────────────────┐     agendado (cron)      ┌──────────────────────────┐
│  GitHub Actions     │ ───────────────────────▶ │ scraper/main.py          │
│  (.github/workflows)│                          │  1. baixa cada portal    │
└────────────────────┘                          │  2. faz o parsing        │
                                                  │  3. compara com o estado │
                                                  │     anterior (diff)      │
                                                  │  4. grava data/state.json│
                                                  │  5. grava docs/data.json │
                                                  │  6. envia e-mail (SMTP)  │
                                                  └──────────────────────────┘
                                                              │
                                                              ▼
                                                  ┌──────────────────────────┐
                                                  │  GitHub Pages (docs/)    │
                                                  │  painel web estático     │
                                                  └──────────────────────────┘
```

- **`data/state.json`**: fonte de verdade com o histórico completo de tudo
  que já foi visto (nunca é sobrescrito "do zero" — apenas acrescido).
- **`docs/data.json`**: versão "achatada" do estado, pronta para o painel
  consumir via `fetch()`.
- A cada execução, o próprio workflow **commita e faz push** dos arquivos
  `data/state.json` e `docs/data.json` de volta no repositório — por isso o
  job precisa de `permissions: contents: write`.

### Como o "resumo do que mudou" é gerado

Cada portal oficial já publica, ao lado de cada Nota Técnica, uma frase
curta descrevendo o que ela altera (ex.: *"Divulga alteração nas regras de
validação do GTIN"*). O parser extrai exatamente esse texto oficial — ou
seja, o resumo vem da própria fonte do governo, sem "inventar" conteúdo.

Além disso, quando o sistema percebe que uma Nota Técnica já conhecida
ganhou uma **versão nova** (ex.: NT 2025.002 v1.50 → v1.51), ele guarda o
resumo da versão anterior ao lado do resumo da nova, para você comparar
rapidamente o que mudou entre as duas (campo `previous_summary` em
`data/state.json`, exibido no painel como "Versão anterior: ...").

> 💡 **Evolução natural**: se quiser um resumo ainda mais detalhado (por
> exemplo, comparando os campos XML de um PDF de NT com o da versão
> anterior usando IA), dá para acrescentar um módulo
> `scraper/ai_summary.py` que baixa o PDF linkado, extrai o texto e chama um
> modelo de linguagem para gerar um "diff" de campos. Deixei o pipeline
> pronto para receber esse módulo sem precisar reescrever o resto.

---

## Colocando no ar (passo a passo)

### 1. Criar o repositório
Crie um repositório novo no GitHub (pode ser privado) e suba todo o
conteúdo desta pasta para a branch `main`.

```bash
git init
git add .
git commit -m "chore: setup inicial do radar fiscal"
git branch -M main
git remote add origin https://github.com/SEU_USUARIO/radar-fiscal.git
git push -u origin main
```

### 2. Configurar o e-mail de alerta (Secrets)
Em **Settings → Secrets and variables → Actions → New repository secret**,
crie:

| Secret | Exemplo | Observação |
|---|---|---|
| `SMTP_HOST` | `smtp.gmail.com` | ou o SMTP da sua empresa/Office 365 (`smtp.office365.com`) |
| `SMTP_PORT` | `587` | porta TLS padrão |
| `SMTP_USER` | `radar.fiscal@suaempresa.com.br` | conta usada para enviar |
| `SMTP_PASS` | `senha_de_app` | use uma **senha de app**, nunca a senha normal da conta |
| `ALERT_FROM` | `radar.fiscal@suaempresa.com.br` | remetente exibido |
| `ALERT_TO` | `thales@flag.com.br,coordenadores@flag.com.br` | destinatários, separados por vírgula |

> Se usar Gmail, gere uma ["senha de app"](https://support.google.com/accounts/answer/185833).
> Se usar Office 365/Outlook corporativo, use `smtp.office365.com`, porta
> `587` e autenticação moderna/senha de app, conforme a política da sua
> empresa.

### 3. Ativar o GitHub Pages
Em **Settings → Pages**:
- **Source**: `Deploy from a branch`
- **Branch**: `main` / pasta **`/docs`**
- Salve. Em alguns minutos o painel ficará disponível em:
  `https://SEU_USUARIO.github.io/radar-fiscal/`

### 4. Rodar manualmente pela primeira vez
Vá em **Actions → Monitor NFe/CTe/MDFe → Run workflow** para disparar a
primeira coleta sem esperar o agendamento. Depois disso, ele roda sozinho
2x por dia (09h e 15h, horário de Brasília — ajustável em
`.github/workflows/monitor.yml`).

---

## Rodando localmente (para testar/ajustar antes de subir)

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Sem e-mail configurado, o monitor roda normalmente e só avisa no log
# que o envio foi pulado.
python -m scraper.main

# Para testar o envio de e-mail localmente, exporte as variáveis antes:
export SMTP_HOST=smtp.gmail.com
export SMTP_PORT=587
export SMTP_USER=seu_email@gmail.com
export SMTP_PASS=sua_senha_de_app
export ALERT_FROM=seu_email@gmail.com
export ALERT_TO=voce@empresa.com
python -m scraper.main
```

Para ver o painel localmente:
```bash
cd docs && python -m http.server 8000
# abra http://localhost:8000
```

---

## Como adicionar uma nova fonte

1. Escreva uma função de parser em `scraper/parsers.py` que receba o HTML
   bruto e devolva uma lista de itens no formato normalizado (veja o
   cabeçalho do arquivo).
2. Registre a função no dicionário `PARSERS` no final do arquivo.
3. Adicione a fonte em `SOURCES`, em `scraper/config.py`, apontando para a
   nova `url` e para a chave (`parser`) da função criada.

Nenhum outro arquivo precisa mudar — o diff, o e-mail e o painel já
funcionam para qualquer fonte que siga o formato normalizado.

---

## ⚠️ Avisos importantes

- **Seletores podem precisar de ajuste fino.** Os parsers foram construídos
  a partir do texto real publicado nos três portais (validado via busca),
  mas sites de governo mudam de layout sem aviso. Se o número de itens
  coletados cair a zero de uma hora para outra, o primeiro lugar a olhar é
  o parser da fonte correspondente em `scraper/parsers.py` — normalmente
  basta ajustar o regex do "cabeçalho" (título + data) que separa uma
  publicação da outra.
- **Robots / termos de uso**: os portais consultados são páginas públicas
  de divulgação de atos normativos; ainda assim, respeite o `robots.txt` de
  cada site e evite aumentar a frequência de execução além do necessário
  (2x/dia já é mais do que suficiente para esse tipo de publicação).
- **Dados iniciais (seed)**: `data/state.json` e `docs/data.json` já vêm
  preenchidos com uma fotografia real das últimas publicações de cada
  portal (coletada em 30/09/2026), para o painel não começar vazio. A
  primeira execução do workflow vai atualizar tudo automaticamente.

---

## Estrutura do projeto

```
radar-fiscal/
├── .github/workflows/monitor.yml   # agendamento + automação (Actions)
├── scraper/
│   ├── config.py                   # fontes monitoradas + credenciais (env vars)
│   ├── fetcher.py                  # download das páginas
│   ├── parsers.py                  # extração/normalização por portal
│   ├── store.py                    # leitura/gravação do estado (JSON)
│   ├── notifier.py                 # montagem e envio do e-mail de alerta
│   └── main.py                     # orquestração (coleta → diff → grava → alerta)
├── data/state.json                 # histórico completo (fonte de verdade)
├── docs/                           # painel web (GitHub Pages)
│   ├── index.html
│   ├── style.css
│   ├── app.js
│   └── data.json                   # dados consumidos pelo painel
├── requirements.txt
└── README.md
```
