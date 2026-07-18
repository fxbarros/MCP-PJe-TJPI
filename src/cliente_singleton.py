"""Singleton de PJeClient com sessao persistente.

Mantem 1 unica sessao do PJe ativa entre tool calls do MCP server,
evitando logins repetidos (que disparam rate-limiting do TJ-PI).

Comportamento:
- Startup do server: warmup() faz login antecipado em background (evita
  -32001 na 1a tool call a frio)
- Chamadas seguintes em <5min, mesma persona+grau: reusa (~5-10s, sem login)
- Troca de persona/grau OU >5min sem uso: fecha o anterior e cria novo
- Watchdog do server chama fechar_se_ocioso() periodicamente: fecha o
  Chromium DE FATO apos o timeout (nao so na proxima chamada)
- Shutdown (lifespan do server) + atexit (backstop): cleanup

Trade-offs aceitos:
- Cookie do PJe vivo em RAM por ate 5min (nao em disco - usa context normal,
  nao launch_persistent_context). Aceitavel.
- Se o servidor PJe expirar a sessao no meio (timeout proprio), proxima tool
  call retorna erro - nao implementamos retry automatico no meio de scraping.
"""
import asyncio
import atexit
import os
import sys
import time
from typing import Optional

import keyring

from pje_client import PJeClient

KEYRING_SERVICE = "mcp-pje-tjpi"
TIMEOUT_INATIVIDADE_S = 300  # 5 minutos

# Estado global do singleton
_cliente: Optional[PJeClient] = None
_chave_ativa: Optional[tuple] = None  # (persona, grau)
_ultimo_uso: float = 0
_lock = asyncio.Lock()


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _get_creds():
    cpf = keyring.get_password(KEYRING_SERVICE, "cpf")
    senha = keyring.get_password(KEYRING_SERVICE, "senha")
    seed = keyring.get_password(KEYRING_SERVICE, "totp_seed")
    if not all([cpf, senha, seed]):
        raise RuntimeError(
            "Credenciais nao encontradas no Keychain. "
            "Execute setup_credenciais.py primeiro."
        )
    return cpf, senha, seed


async def get_cliente(persona: str = "advogado", grau: str = "1g") -> PJeClient:
    """Retorna um PJeClient pronto pra uso.

    Reusa a sessao existente se ainda valida (mesma persona e grau, <5min
    de inatividade). Senao cria uma nova.
    """
    global _cliente, _chave_ativa, _ultimo_uso
    chave = (persona, grau)

    async with _lock:
        agora = time.time()
        idade = agora - _ultimo_uso if _ultimo_uso else float("inf")

        # Reusa se: existe + mesma persona + dentro do timeout + browser vivo.
        # Health-check do browser e' importante porque o usuario pode ter
        # fechado a janela manualmente (X), ou o processo Chrome pode ter
        # morrido. Sem o check, o singleton entrega referencia morta e a
        # proxima tool call falha com "Target page, context or browser has
        # been closed".
        browser_vivo = False
        if _cliente is not None:
            try:
                browser_vivo = (
                    _cliente._browser is not None
                    and _cliente._browser.is_connected()
                )
            except Exception:
                browser_vivo = False

        if (
            _cliente is not None
            and _chave_ativa == chave
            and idade < TIMEOUT_INATIVIDADE_S
            and browser_vivo
        ):
            _log(f"[SINGLETON] Reusando sessao ({idade:.0f}s desde ultimo uso)")
            _ultimo_uso = agora
            return _cliente

        # Caso contrario: fecha o atual (se houver) e cria novo
        if _cliente is not None:
            if not browser_vivo:
                motivo = "browser fechado/desconectado"
            elif _chave_ativa != chave:
                motivo = "troca de persona/grau"
            else:
                motivo = f"timeout ({idade:.0f}s > {TIMEOUT_INATIVIDADE_S}s)"
            _log(f"[SINGLETON] Fechando sessao anterior ({motivo})")
            try:
                await _cliente._fechar()
            except Exception as e:
                _log(f"[SINGLETON] Erro ao fechar (ignorado): {e}")
            _cliente = None
            _chave_ativa = None

        # Cria novo cliente + login.
        # HEADLESS=True por default (descoberto em 2026-05-05): com Playwright
        # puro + Chromium padrao, o headless funciona no PJe-TJPI. A "armadilha"
        # original do bloqueio de headless nao se reproduziu mais. Pode ter sido
        # flake do servidor ou efeito colateral do bug do networkidle/doc_a_doc
        # que ja foram corrigidos.
        # _login() tem retry automatico (1x) pra cobrir flakes pontuais.
        # Pra forcar HEADED de novo (debug visual): PJE_HEADLESS=0 ...
        headless_env = os.environ.get("PJE_HEADLESS", "1") == "1"

        _log(f"[SINGLETON] Criando nova sessao (persona={persona}, grau={grau}, headless={headless_env})")
        cpf, senha, seed = _get_creds()
        novo = PJeClient(cpf, senha, seed, persona=persona, headless=headless_env, grau=grau)
        await novo._iniciar()
        await novo._login()
        await novo._trocar_perfil()

        _cliente = novo
        _chave_ativa = chave
        _ultimo_uso = agora
        return _cliente


async def warmup(persona: str = "advogado") -> None:
    """Faz o login ANTECIPADO, em background, no startup do servidor MCP.

    Motivo: a 1a tool call a frio (login ~25s + acao ~15s) estoura o timeout
    do protocolo MCP (-32001). Com warm-up, quando a 1a chamada chegar a
    sessao ja esta quente. Se a chamada chegar DURANTE o warm-up, ela espera
    no _lock do get_cliente e reusa o login em andamento (nao duplica).
    Falha de warm-up nao e' fatal: o login acontece na 1a chamada como antes.
    """
    try:
        _log("[SINGLETON] Warm-up: login antecipado em background...")
        await get_cliente(persona)
        _log("[SINGLETON] Warm-up concluido - sessao pronta")
    except Exception as e:
        _log(f"[SINGLETON] Warm-up falhou ({e.__class__.__name__}: {e}); "
             "login ocorrera na 1a tool call")


async def fechar_se_ocioso() -> bool:
    """Fecha a sessao se passou do timeout de inatividade. Retorna True se fechou.

    Chamado periodicamente pelo watchdog do server. Sem isso, o Chromium
    ficava vivo indefinidamente entre chamadas (o timeout so era checado
    lazy, na chamada seguinte).
    """
    global _cliente, _chave_ativa
    async with _lock:
        if _cliente is None:
            return False
        idade = time.time() - _ultimo_uso
        if idade < TIMEOUT_INATIVIDADE_S:
            return False
        # Espera operacao em andamento terminar antes de fechar (op_lock).
        # Ordem de aquisicao (_lock -> _op_lock) e' a mesma do fluxo das
        # tools (get_cliente -> metodo), entao nao ha deadlock.
        async with _cliente._op_lock:
            _log(f"[SINGLETON] Watchdog: fechando sessao ociosa ({idade:.0f}s)")
            try:
                await _cliente._fechar()
            except Exception as e:
                _log(f"[SINGLETON] Erro ao fechar (ignorado): {e}")
        _cliente = None
        _chave_ativa = None
        return True


async def fechar_cliente():
    """Fecha a sessao atual (chamado no shutdown do server/atexit)."""
    global _cliente, _chave_ativa, _ultimo_uso
    if _cliente is not None:
        _log("[SINGLETON] Cleanup atexit - fechando sessao")
        try:
            await _cliente._fechar()
        except Exception as e:
            _log(f"[SINGLETON] Erro no cleanup: {e}")
        _cliente = None
        _chave_ativa = None
        _ultimo_uso = 0


def _atexit_handler():
    """Wrapper sync pro atexit (que nao aceita coroutine)."""
    if _cliente is None:
        return
    try:
        loop = asyncio.new_event_loop()
        loop.run_until_complete(fechar_cliente())
        loop.close()
    except Exception as e:
        # atexit nao deve falhar
        print(f"[SINGLETON] Erro no atexit handler: {e}", file=sys.stderr)


# Registra cleanup automatico ao desligar o processo
atexit.register(_atexit_handler)
