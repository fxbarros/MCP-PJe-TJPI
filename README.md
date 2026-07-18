<h1 align="center">
    <img alt="MCP PJe-TJPI" src="https://raw.githubusercontent.com/fxbarros/MCP-PJe-TJPI-1g/main/docs/assets/banner.svg?sanitize=true">
    <br>
    <small>Expedientes, prazos e autos do PJe em linguagem natural — sem nunca escrever no tribunal</small>
</h1>

<p align="center">
    <img alt="Python" src="https://img.shields.io/badge/python-3.10+-3776AB?logo=python&logoColor=white">
    <img alt="Ferramentas" src="https://img.shields.io/badge/ferramentas-22-brightgreen">
    <img alt="MCP" src="https://img.shields.io/badge/MCP-Claude%20Desktop-d97757">
    <img alt="Login" src="https://img.shields.io/badge/login-CPF%20%2B%20senha%20%2B%20TOTP%20autom%C3%A1tico-black">
    <img alt="Somente leitura" src="https://img.shields.io/badge/PJe-somente%20leitura-8b0000">
</p>

<p align="center">
    <a href="#-funcionalidades"><strong>Funcionalidades</strong></a>
    &middot;
    <a href="#%EF%B8%8F-as-22-ferramentas"><strong>Ferramentas</strong></a>
    &middot;
    <a href="#-instala%C3%A7%C3%A3o"><strong>Instalação</strong></a>
    &middot;
    <a href="#-exemplos-de-uso"><strong>Exemplos</strong></a>
    &middot;
    <a href="#-seguran%C3%A7a"><strong>Segurança</strong></a>
    &middot;
    <a href="#%EF%B8%8F-avisos-importantes"><strong>Avisos</strong></a>
</p>

Servidor [MCP](https://modelcontextprotocol.io) que permite ao Claude Desktop consultar o **Processo Judicial Eletrônico** do Tribunal de Justiça do Piauí (PJe-TJPI) — **1º e 2º graus** — diretamente em linguagem natural.

> 🏛️ **Um MCP, dois graus**: todas as ferramentas aceitam o parâmetro `grau` — `"1"` (varas, padrão) ou `"2"` (câmaras, acórdãos, recursos). Basta dizer "no 2º grau" na conversa.

## ✨ Funcionalidades

- 🔐 **Login 100% automatizado**: CPF + senha + 2FA (TOTP)
- 🏛️ **1º e 2º graus** no mesmo servidor (parâmetro `grau`; autos baixados em pastas separadas por grau)
- 👤 **Duas personas**: `advogado` (padrão) e `procurador` (Procuradoria do Município de Teresina)
- 📋 **Expedientes pendentes**: intimações, despachos e prazos
- ⏰ **Alertas de prazos urgentes** (3 dias)
- 🔍 **6 formas de busca**: nº CNJ, nome da parte, nome do advogado, CPF, CNPJ e OAB
- 📄 **Listagem, leitura e download de documentos** (HTML e PDF), incluindo os autos completos
- 📝 **Fluxo de produção**: modelos de petição, salvamento de petições e relatórios na pasta do processo
- 🛡️ **Tratamento automático** do aviso da Resolução CNJ 121/2010 (processos de terceiros)

## 🛠️ As 24 ferramentas

**Painel e prazos**

| Ferramenta | O que faz |
|---|---|
| `expedientes_pendentes` | intimações/despachos pendentes de ciência ou resposta |
| `verificar_prazos_urgentes` | expedientes com data limite em ≤ 3 dias |
| `pendencias_processo` | pendências (expedientes + prazos) de UM processo |
| `expedientes_do_processo` | histórico COMPLETO de expedientes de UM processo (aba Expedientes dos autos): ato, destinatário, via, expedição, ciência, prazo e data limite — inclusive já fechados/vencidos. Leitura passiva: não registra ciência |

**Consulta e busca**

| Ferramenta | O que faz |
|---|---|
| `consultar_processo` | dados básicos do processo por nº CNJ |
| `ultimas_movimentacoes` | N últimas movimentações |
| `relatorio_processo` | relatório completo: dados, movimentações e documentos |
| `buscar_por_nome_parte` / `buscar_por_nome_advogado` | busca por nome |
| `buscar_por_cpf` / `buscar_por_cnpj` / `buscar_por_oab` | busca por identificador |

**Documentos e autos**

| Ferramenta | O que faz |
|---|---|
| `listar_documentos` | todos os documentos do processo |
| `ler_documento` | texto integral de um documento (HTML ou PDF) |
| `ultima_decisao` | teor da última decisão/sentença/despacho/ato ordinatório |
| `ultimo_despacho` | teor do último despacho (só despacho) |
| `baixar_documento` | baixa UM documento e salva na pasta do processo |
| `baixar_processo` | baixa os autos COMPLETOS (padrão: em background, imune ao timeout do protocolo MCP) |
| `status_download` | acompanha um download disparado em background |
| `preparar_processo` | baixa o processo e decide a estratégia de análise |

**Produção de peças (grava só no SEU disco, nunca no PJe)**

| Ferramenta | O que faz |
|---|---|
| `listar_modelos_peticao` / `ler_modelo_peticao` | modelos de petição/relatório |
| `salvar_peticao_processo` | salva petição (.docx) na pasta do processo |
| `salvar_relatorio_processo` | salva relatório de análise na pasta do processo |

Todas aceitam os parâmetros opcionais `persona` (`"advogado"` padrão ou `"procurador"`) e `grau` (`"1"` padrão ou `"2"`).

## 🧰 Requisitos

- macOS (o projeto usa Keychain para credenciais — em Linux/Windows funciona com `keyring` equivalente)
- Python 3.10+
- Claude Desktop instalado
- Conta ativa no PDPJ com 2FA configurado via app autenticador (Google Authenticator, Microsoft Authenticator, Authy, FreeOTP etc.)

## 📦 Instalação

### 1) Clone o repositório

```bash
git clone https://github.com/fxbarros/MCP-PJe-TJPI-1g.git mcp-pje-tjpi-1g
cd mcp-pje-tjpi-1g
```

### 2) Ambiente virtual + dependências

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

### 3) Configure o 2FA no PJe (só se ainda não fez)

1. Acesse o PJe-TJPI e clique em **"Configurar novo dispositivo"**
2. Você receberá um e-mail com um link — abra
3. Na tela do QR Code, **anote a seed** ("não consegue escanear? use esta chave") — string Base32 (A-Z, 2-7)
4. Escaneie o QR Code com seu app autenticador também
5. Valide digitando o código gerado pelo app
6. Guarde a seed em **lugar seguro** (ex.: Notas Seguras do Keychain)

### 4) Salve as credenciais no Keychain

```bash
python3 setup_credenciais.py
```

O script pergunta CPF, senha PDPJ e seed TOTP. Tudo fica criptografado no Keychain do macOS — nunca em arquivo.

### 5) Teste rápido (opcional mas recomendado)

```bash
python3 -c "import keyring, pyotp; print('Codigo:', pyotp.TOTP(keyring.get_password('mcp-pje-tjpi', 'totp_seed')).now())"
```

Se o código bater com o do app do celular, perfeito.

### 6) Registre o MCP no Claude Desktop

`~/Library/Application Support/Claude/claude_desktop_config.json` (ajuste os caminhos):

```json
{
  "mcpServers": {
    "pje-tjpi-1g": {
      "command": "/Users/SEU_USUARIO/mcp-pje-tjpi-1g/venv/bin/python",
      "args": ["/Users/SEU_USUARIO/mcp-pje-tjpi-1g/src/server.py"]
    }
  }
}
```

### 7) Reinicie o Claude Desktop

`Cmd+Q` e abra de novo — as ferramentas devem aparecer.

## 💬 Exemplos de uso

```
Tenho expedientes pendentes no TJPI 1º grau?

Quais meus prazos urgentes?

Consulte o processo 0000000-00.0000.0.00.0000

Me faz um relatório do processo 0000000-00.0000.0.00.0000

Busca processos pela minha OAB 12345/PI

Liste os documentos do processo e lê a última decisão

Baixa os autos completos e prepara o processo para análise

Na persona de procurador do município, consulte o processo 0000000-00.0000.0.00.0000

Consulte a apelação 0000000-00.0000.0.00.0000 no 2º grau

Quais os expedientes do processo 0000000-00.0000.0.00.0000? Com datas de ciência e prazo
```

## 🏗️ Estrutura do projeto

```
mcp-pje-tjpi-1g/
├── README.md              # este arquivo
├── requirements.txt       # dependências Python
├── setup_credenciais.py   # setup inicial (rodar 1x)
└── src/
    ├── pje_client.py         # cliente Playwright (automação do PJe, 1g/2g)
    ├── cliente_singleton.py  # 1 sessão viva por (persona, grau) entre tool calls
    ├── pje_downloader.py     # download de documentos/autos (+ jobs em background)
    ├── minutas.py            # salvamento de petições/relatórios
    ├── modelos.py            # modelos de petição no iCloud
    └── server.py             # servidor MCP (expõe as tools ao Claude)
```

## 🔒 Segurança

- **Credenciais** ficam no Keychain do macOS, nunca em arquivo
- **Seed TOTP** tratada como secret — não commitar nunca
- **Nenhuma ação de escrita no PJe**: este MCP só **lê** informação do tribunal — nunca protocola, peticiona ou altera nada (as ferramentas de "salvar" gravam apenas no seu disco local)
- **Resolução CNJ 121/2010**: consulta a processo de terceiro é registrada pelo próprio PJe e o retorno inclui o aviso

## ⚠️ Avisos importantes

**Validade das credenciais** — a senha do PDPJ expira periodicamente (~30 dias); ao expirar, troque no site e rode `setup_credenciais.py` de novo. Se reconfigurar o 2FA, a seed antiga fica inválida — atualize no Keychain.

**Fragilidade de scraping** — o projeto depende do HTML/JavaScript atual do PJe-TJPI. Se o tribunal mudar o layout: rode com `headless=False` para ver onde trava, pegue os novos seletores no DevTools e atualize o `pje_client.py`.

**Uso responsável** — respeite o termo de uso do PJe; nada de scraping massivo; consultas a processos de terceiros ficam registradas — use com responsabilidade profissional.

## 🔄 Adaptando para outros tribunais

A lógica geral (login SSO do CNJ, 2FA, estrutura do PJe) é compartilhada por vários tribunais. Para adaptar: mude o host em `PJeClient.url_base` (`pje_client.py`; o grau já é parametrizado), ajuste os seletores diferentes com o DevTools, verifique o layout das telas e renomeie o MCP em `server.py` (`FastMCP("pje-tjXX")`).

## 🚧 Roadmap

- [x] ~~MCP separado para 2º grau do TJPI~~ → incorporado neste MCP via parâmetro `grau` (07/2026)
- [ ] OCR para PDFs digitalizados sem texto extraível
- [ ] Pauta de audiências

## 📝 Licença e créditos

Uso pessoal e profissional, sem garantias — use por sua conta e risco, respeitando as regras do tribunal e do seu cliente. Construído por [Fábio Ximenes Barros](https://github.com/fxbarros) com ajuda do [Claude](https://www.anthropic.com/claude), usando [Playwright](https://playwright.dev), [PyOTP](https://pyauth.github.io/pyotp/) e [pdfplumber](https://github.com/jsvine/pdfplumber).

<p align="center"><sub>Arte do banner: original — marca dos projetos MCP do autor.</sub></p>
