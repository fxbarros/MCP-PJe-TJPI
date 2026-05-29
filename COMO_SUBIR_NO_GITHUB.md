# Como subir este projeto para o GitHub

Este guia te ajuda a criar um repositório no GitHub e subir o código.

## Pré-requisitos

1. **Conta no GitHub** ([github.com](https://github.com))
2. **Git instalado** no seu Mac (geralmente já vem)
   Confirme rodando: `git --version`

## Passo 1: Criar repositório no GitHub (pela web)

1. Acesse [github.com/new](https://github.com/new)
2. Preencha:
   - **Repository name**: `mcp-pje-tjpi-1g`
   - **Description**: `MCP para consultar o PJe-TJPI 1º grau via Claude Desktop`
   - **Visibility**:
     - 🔒 **Private** (recomendado) — só você vê
     - 🌐 **Public** — qualquer um pode ver (mas ele NÃO contém credenciais)
   - **NÃO** marque "Add a README file" (já temos um)
   - **NÃO** marque "Add .gitignore"
3. Clique em **Create repository**
4. Na próxima tela, **copie a URL HTTPS** do repo (algo como `https://github.com/SEU_USUARIO/mcp-pje-tjpi-1g.git`)

## Passo 2: Configurar Git localmente (só 1x)

Se nunca usou Git no Mac, configure seu nome e email:

```bash
git config --global user.name "Seu Nome"
git config --global user.email "seu_email@exemplo.com"
```

## Passo 3: Fazer o primeiro commit

Dentro da pasta do projeto (`~/mcp-pje-tjpi-1g` depois do download desses
arquivos), execute:

```bash
cd ~/mcp-pje-tjpi-1g

# Inicializa Git
git init

# Adiciona todos os arquivos (respeitando .gitignore)
git add .

# Conferir o que SERA commitado (não deve aparecer venv/, credenciais, etc.)
git status

# Primeiro commit
git commit -m "MCP PJe-TJPI 1o grau - versao inicial funcional"

# Define branch principal como 'main'
git branch -M main
```

## Passo 4: Conectar ao GitHub e enviar

```bash
# Troque SEU_USUARIO pelo seu usuario no GitHub
git remote add origin https://github.com/SEU_USUARIO/mcp-pje-tjpi-1g.git

# Envia para o GitHub
git push -u origin main
```

Na primeira vez, o Git vai pedir autenticação:

- **Usuário**: seu login do GitHub
- **Senha**: **NÃO use sua senha!** Use um **Personal Access Token (PAT)**.
  Crie um em: [github.com/settings/tokens](https://github.com/settings/tokens/new)
  - Scope: marque `repo`
  - Expiração: 90 dias ou mais
  - Copie o token e use como senha no Git

Dica: pra não ficar pedindo o token a cada push, após a primeira vez:

```bash
git config --global credential.helper osxkeychain
```

O macOS passa a guardar o token no Keychain e não pergunta mais.

## Passo 5: Conferir no GitHub

Abra `https://github.com/SEU_USUARIO/mcp-pje-tjpi-1g` no navegador.
Você deve ver todos os arquivos lá, exceto:

- ❌ `venv/` (ignorado por estar no .gitignore)
- ❌ Credenciais (nunca existiram em arquivo)
- ❌ `__pycache__/` (ignorado)

## 🔄 Atualizações futuras

Quando melhorar algo, rode:

```bash
cd ~/mcp-pje-tjpi-1g
git add .
git commit -m "descricao curta da mudanca"
git push
```

## 📋 Checklist antes de fazer repositório PÚBLICO

Se decidir deixar o repo público, **confira antes**:

- [ ] Nenhum CPF seu aparece em código (só placeholders como `SEU_USUARIO`)
- [ ] Nenhuma senha em lugar nenhum
- [ ] Nenhuma seed TOTP em lugar nenhum
- [ ] `.gitignore` está ignorando `venv/`, `.env`, etc.
- [ ] README não expõe seus dados reais

Rode esta verificação pra confirmar:

```bash
# Deve retornar vazio ou só matches triviais (ex: no README)
# Troque os termos abaixo pelos SEUS dados reais ao rodar (CPF, OAB):
grep -r "SEU_INICIO_DE_CPF" --include="*.py" .
grep -r "SEU_NUMERO_OAB" --include="*.py" .
```

## 🆘 Se algo der errado

### "Permission denied (publickey)"

Está tentando usar SSH sem ter configurado. Use URL HTTPS em vez de SSH:

```bash
git remote set-url origin https://github.com/SEU_USUARIO/mcp-pje-tjpi-1g.git
```

### "Support for password authentication was removed"

Você precisa usar Personal Access Token (ver Passo 4).

### Commitei credencial sem querer!

1. **Troque imediatamente** a credencial exposta no sistema de origem (senha do PDPJ, seed do 2FA, etc.)
2. Remova do histórico do Git:
   ```bash
   git rm --cached arquivo_com_credencial
   git commit -m "remove credencial"
   git push
   ```
   Isso apenas remove do commit atual. Para limpar do histórico completo,
   pesquise "BFG Repo Cleaner" ou `git filter-repo`.
3. Se o repo já é público, considere **apagar e recriar** o repositório.

## 🎁 Clonar em outra máquina

Pra usar em outro Mac:

```bash
git clone https://github.com/SEU_USUARIO/mcp-pje-tjpi-1g.git
cd mcp-pje-tjpi-1g
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
playwright install chromium
python3 setup_credenciais.py  # roda setup de credenciais de novo
```

E segue o README normalmente.
