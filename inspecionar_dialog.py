"""Abre o autos, clica no dropdown de download e imprime o HTML do dialog.

Totalmente automatico - nao precisa interacao do usuario.
"""
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "src")
import keyring
from pje_client import PJeClient

NUMERO_CNJ = "0000000-00.0000.0.00.0000"
SAIDA = Path("/tmp/inspecao_dialog.json")


async def main():
    cpf = keyring.get_password("mcp-pje-tjpi", "cpf")
    senha = keyring.get_password("mcp-pje-tjpi", "senha")
    seed = keyring.get_password("mcp-pje-tjpi", "totp_seed")

    pje = PJeClient(cpf, senha, seed, persona="advogado", headless=True)
    await pje._iniciar()

    print(">>> Login...")
    await pje._login()
    print(">>> Trocar perfil...")
    await pje._trocar_perfil()
    print(">>> Abrindo autos...")
    aba = await pje._abrir_autos_processo(NUMERO_CNJ)
    await asyncio.sleep(2)

    # Tenta varios seletores pro botao do dropdown
    seletores_trigger = [
        "a[title='Download autos do processo']",
        "a.btn-menu-abas.dropdown-toggle[title*='Download']",
        "li.dropdown.drop-menu.filtros-download a.dropdown-toggle",
        "li.filtros-download a",
    ]

    print(">>> Procurando botao de download...")
    trigger_encontrado = None
    for sel in seletores_trigger:
        try:
            el = aba.locator(sel)
            count = await el.count()
            if count > 0:
                print(f"    encontrado via: {sel} (count={count})")
                trigger_encontrado = sel
                break
        except Exception as e:
            print(f"    falhou {sel}: {e}")

    if not trigger_encontrado:
        print(">>> ERRO: nenhum dos seletores funcionou")
        await pje._fechar()
        return

    # Clica no trigger pra abrir o dropdown
    print(f">>> Clicando no trigger: {trigger_encontrado}")
    await aba.click(trigger_encontrado)
    await asyncio.sleep(1)

    # Captura o HTML completo do <li class="dropdown drop-menu filtros-download">
    print(">>> Capturando HTML do dropdown aberto...")
    html_li = await aba.evaluate("""
        () => {
            const el = document.querySelector('li.dropdown.drop-menu.filtros-download');
            return el ? el.outerHTML : null;
        }
    """)

    # Tambem captura o dropdown-menu interno
    html_menu = await aba.evaluate("""
        () => {
            const el = document.querySelector('li.filtros-download .dropdown-menu');
            return el ? el.outerHTML : null;
        }
    """)

    # Captura tambem todos os inputs/selects/buttons dentro
    elementos_form = await aba.evaluate("""
        () => {
            const out = [];
            document.querySelectorAll(
                'li.filtros-download .dropdown-menu input, ' +
                'li.filtros-download .dropdown-menu select, ' +
                'li.filtros-download .dropdown-menu button, ' +
                'li.filtros-download .dropdown-menu a'
            ).forEach(el => {
                out.push({
                    tag: el.tagName,
                    type: el.type || null,
                    id: el.id || null,
                    name: el.getAttribute('name') || null,
                    class: el.className || null,
                    value: el.value || null,
                    placeholder: el.placeholder || null,
                    text: (el.innerText || '').slice(0, 80),
                    onclick: el.getAttribute('onclick') || null,
                    title: el.getAttribute('title') || null,
                });
            });
            return out;
        }
    """)

    relatorio = {
        "trigger_seletor": trigger_encontrado,
        "html_li_completo": html_li,
        "html_menu": html_menu,
        "elementos_form": elementos_form,
    }

    SAIDA.write_text(json.dumps(relatorio, indent=2, ensure_ascii=False))
    print(f"\n>>> Salvo em: {SAIDA}")
    print(f">>> Elementos do form encontrados: {len(elementos_form)}")
    for e in elementos_form:
        print(f"    {e['tag']} type={e['type']} id={e['id']} name={e['name']} text='{e['text']}'")

    await pje._fechar()


if __name__ == "__main__":
    asyncio.run(main())
