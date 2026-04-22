# MCP PJe-TJPI (1º Grau)

Servidor MCP ([Model Context Protocol](https://modelcontextprotocol.io)) que
permite ao Claude Desktop consultar o **Processo Judicial Eletrônico** do
Tribunal de Justiça do Piauí (PJe-TJPI) — **1º grau** — diretamente via
linguagem natural.

> ⚠️ **Este MCP acessa exclusivamente o 1º grau** (varas). Para 2º grau
> (câmaras, acórdãos, recursos), crie um MCP separado seguindo o mesmo padrão.

## ✨ Funcionalidades

- 🔐 **Login 100% automatizado**: CPF + senha + 2FA (TOTP)
- 👤 **Duas personas**: `advogado` (padrão) e `procurador` (Procuradoria do Município de Teresina)
- 📋 **Expedientes pendentes**: intimações, despachos e prazos
- ⏰ **Alertas de prazos urgentes** (3 dias)
- 🔍 **5 formas de busca de processos**:
  - Por número CNJ
  - Por nome da parte
  - Por nome do advogado
  - Por CPF
  - Por CNPJ
  - Por OAB
- 📄 **Listagem de documentos** do processo
- 📖 **Leitura de teor completo** (HTML e PDF)
- 🛡️ **Tratamento automático** do aviso da Resolução CNJ 121/2010 (processos de terceiros)

## 🛠️ Requisitos

- macOS (o projeto usa Keychain pra credenciais — em Linux/Windows funciona com `keyring` equivalente)
- Python 3.10+
- Claude Desktop instalado
- Conta ativa no PDPJ com 2FA configurado via app autenticador
  (Google Authenticator, Microsoft Authenticator, Authy, FreeOTP, etc.)

## 📦 Instalação

### 1) Clone o repositório

```bash
git clone https://github.com/SEU_USUARIO/mcp-pje-tjpi-1g.git
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
3. Na tela que mostra o QR Code, **anote a seed** (procure por "não consegue escanear? use esta chave")
   - A seed é uma string Base32 (só letras A-Z e números 2-7)
4. Escaneie o QR Code com seu app autenticador também
5. Valide a configuração digitando o código gerado pelo app
6. Guarde a seed em **lugar seguro** (ex: Notas Seguras do Keychain)

### 4) Salve as credenciais no Keychain

```bash
python3 setup_credenciais.py
```

O script pergunta CPF, senha PDPJ e seed TOTP. Tudo fica armazenado
criptografado no Keychain do macOS — nunca em arquivo.

### 5) Teste rápido (opcional mas recomendado)

Verifica se a seed está correta comparando com o app do celular:

```bash
python3 -c "import keyring, pyotp; print('Codigo:', pyotp.TOTP(keyring.get_password('mcp-pje-tjpi', 'totp_seed')).now())"
```

Compare o código retornado com o do app. Se baterem, perfeito!

### 6) Registre o MCP no Claude Desktop

Edite (ou crie) o arquivo de config:

```bash
mkdir -p ~/Library/Application\ Support/Claude
nano ~/Library/Application\ Support/Claude/claude_desktop_config.json
```

Conteúdo (ajuste os caminhos para seu sistema):

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

`Cmd+Q` para fechar completo, depois abra de novo. As ferramentas do
MCP devem aparecer disponíveis.

## 💬 Exemplos de uso no Claude Desktop

```
Tenho expedientes pendentes no TJPI 1º grau?

Quais meus prazos urgentes?

Consulte o processo 0000000-00.0000.0.00.0000

Quais as últimas 5 movimentações do processo 0000000-00.0000.0.00.0000?

Me faz um relatório do processo 0000000-00.0000.0.00.0000

Busca processos pela minha OAB 12345/PI

Busca processos do CPF 000.000.000-00

Busca processos do CNPJ 00.000.000/0000-00

Busca processos da parte "Fulano de Tal"

Liste os documentos do processo 0000000-00.0000.0.00.0000

Lê o documento 89251555 do processo 0000000-00.0000.0.00.0000

Na persona de procurador do município, consulte o processo 0000000-00.0000.0.00.0000
```

## 🏗️ Estrutura do projeto

```
mcp-pje-tjpi-1g/
├── README.md              # este arquivo
├── requirements.txt       # dependências Python
├── .gitignore
├── setup_credenciais.py   # setup inicial (rodar 1x)
└── src/
    ├── pje_client.py      # cliente Playwright (automação do PJe)
    └── server.py          # servidor MCP (expõe tools ao Claude)
```

## 🧰 Tools expostos pelo MCP

| Tool | Descrição |
|---|---|
| `expedientes_pendentes` | Lista intimações/despachos pendentes de ciência ou resposta |
| `verificar_prazos_urgentes` | Expedientes com data limite em ≤ 3 dias |
| `consultar_processo` | Dados básicos do processo por número CNJ |
| `ultimas_movimentacoes` | N últimas movimentações de um processo |
| `relatorio_processo` | Relatório completo com dados, movimentações e documentos |
| `buscar_por_nome_parte` | Busca processos por nome da parte |
| `buscar_por_nome_advogado` | Busca processos por nome do advogado |
| `buscar_por_cpf` | Busca processos por CPF |
| `buscar_por_cnpj` | Busca processos por CNPJ |
| `buscar_por_oab` | Busca processos por número OAB + UF |
| `listar_documentos` | Lista todos os documentos do processo |
| `ler_documento` | Extrai texto integral de um documento (HTML ou PDF) |

Todos aceitam o parâmetro opcional `persona`:
- `"advogado"` (padrão): perfil pessoal
- `"procurador"`: Procuradoria do Município de Teresina

## 🔒 Segurança

- **Credenciais** ficam no Keychain do macOS, nunca em arquivo
- **Seed TOTP** tratada como secret — não commitar nunca
- **Nenhuma ação de escrita** no PJe: este MCP só **lê** informação,
  nunca protocola, peticiona ou altera nada
- **Resolução CNJ 121/2010**: ao consultar processo de terceiro, o
  acesso é registrado pelo próprio PJe e o retorno inclui aviso

## ⚠️ Avisos importantes

### Validade das credenciais

- A senha do PDPJ expira periodicamente (~30 dias). Ao expirar, troque
  no site e rode `setup_credenciais.py` de novo
- Se reconfigurar o 2FA, a seed antiga fica inválida — atualize no Keychain

### Fragilidade de scraping

Este projeto depende do **HTML/JavaScript** atual do PJe-TJPI. Se o
tribunal mudar o layout, alguns seletores podem quebrar. Nesse caso:

1. Rode com `headless=False` pra ver onde trava
2. Use o DevTools do navegador pra pegar os novos seletores
3. Atualize o `pje_client.py`

### Uso responsável

- Respeite o termo de uso do PJe
- Não faça scraping massivo que sobrecarregue o servidor
- Consultas a processos de terceiros ficam registradas — use com
  responsabilidade profissional

## 🔄 Adaptando para outros tribunais

A lógica geral (login SSO do CNJ, tratamento de 2FA, estrutura do PJe)
é compartilhada por vários tribunais brasileiros. Para adaptar:

1. **Mude a URL base** em `pje_client.py`:
   ```python
   URL_BASE = "https://pje.tjXX.jus.br/1g"  # substitua tjXX
   ```
2. **Ajuste seletores** específicos que forem diferentes (inspecione com DevTools)
3. **Verifique o layout** das telas de consulta e autos
4. **Renomeie o MCP** em `server.py`:
   ```python
   mcp = FastMCP("pje-tjXX-1g")
   ```

## 🚧 Pendências / Roadmap

- [ ] Geração de minuta `.docx` a partir de despacho lido
- [ ] MCP separado para 2º grau do TJPI
- [ ] OCR para PDFs digitalizados sem texto extraível
- [ ] Pauta de audiências
- [ ] Download direto de peças específicas

## 📝 Licença

Uso pessoal e profissional. Sem garantias. Use por sua conta e risco,
respeitando as regras do tribunal e do seu cliente.

## 🙏 Créditos

Projeto construído com ajuda do [Claude](https://www.anthropic.com/claude)
da Anthropic, usando [Playwright](https://playwright.dev),
[PyOTP](https://pyauth.github.io/pyotp/) e
[pdfplumber](https://github.com/jsvine/pdfplumber).
