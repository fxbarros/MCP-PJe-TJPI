"""Singleton de PJeClient com sessao persistente.

Mantem 1 unica sessao do PJe ativa entre tool calls do MCP server,
evitando logins repetidos (que disparam rate-limiting do TJ-PI).

Comportamento:
- 1a chamada: cria cliente + login (~30s)
- Chamadas seguintes em <5min, mesma persona: reusa (~5-10s, sem login)
- Troca de persona OU >5min sem uso: fecha o anterior e cria novo
- atexit: cleanup ao desligar o MCP server

Trade-offs aceitos:
- Cookie do PJe vivo em RAM por ate 5min (nao em disco - usa context normal,
  nao launch_persistent_context). Aceitavel.
- Se o servidor PJe expirar a sessao no meio (timeout proprio), proxima tool
  call retorna erro - nao implementamos retry automatico no meio de scraping.
"""
import asyncio
import atexit
import sys
import time
from typing import Optional

import keyring

from pje_client import PJeClient

KEYRING_SERVICE = "mcp-pje-tjpi"
TIMEOUT_INATIVIDADE_S = 300  # 5 minutos

# Estado global do singleton
_cliente: Optional[PJeClient] = None
_persona_ativa: Optional[str] = None
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


async def get_cliente(persona: str = "advogado") -> PJeClient:
    """Retorna um PJeClient pronto pra uso.

    Reusa a sessao existente se ainda valida (mesma persona, <5min de
    inatividade). Senao cria uma nova.
    """
    global _cliente, _persona_ativa, _ultimo_uso

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
            and _persona_ativa == persona
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
            elif _persona_ativa != persona:
                motivo = "troca de persona"
            else:
                motivo = f"timeout ({idade:.0f}s > {TIMEOUT_INATIVIDADE_S}s)"
            _log(f"[SINGLETON] Fechando sessao anterior ({motivo})")
            try:
                await _cliente._fechar()
            except Exception as e:
                _log(f"[SINGLETON] Erro ao fechar (ignorado): {e}")
            _cliente = None
            _persona_ativa = None

        # Cria novo cliente + login.
        # HEADLESS=True por default (descoberto em 2026-05-05): com Playwright
        # puro + Chromium padrao, o headless funciona no PJe-TJPI. A "armadilha"
        # original do bloqueio de headless nao se reproduziu mais. Pode ter sido
        # flake do servidor ou efeito colateral do bug do networkidle/doc_a_doc
        # que ja foram corrigidos.
        # _login() tem retry automatico (1x) pra cobrir flakes pontuais.
        # Pra forcar HEADED de novo (debug visual): PJE_HEADLESS=0 ...
        import os
        headless_env = os.environ.get("PJE_HEADLESS", "1") == "1"

        _log(f"[SINGLETON] Criando nova sessao (persona={persona}, headless={headless_env})")
        cpf, senha, seed = _get_creds()
        novo = PJeClient(cpf, senha, seed, persona=persona, headless=headless_env)
        await novo._iniciar()
        await novo._login()
        await novo._trocar_perfil()

        _cliente = novo
        _persona_ativa = persona
        _ultimo_uso = agora
        return _cliente


async def fechar_cliente():
    """Fecha a sessao atual (chamado no atexit)."""
    global _cliente, _persona_ativa, _ultimo_uso
    if _cliente is not None:
        _log("[SINGLETON] Cleanup atexit - fechando sessao")
        try:
            await _cliente._fechar()
        except Exception as e:
            _log(f"[SINGLETON] Erro no cleanup: {e}")
        _cliente = None
        _persona_ativa = None
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
