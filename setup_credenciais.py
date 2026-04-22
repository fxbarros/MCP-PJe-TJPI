"""Script de configuracao inicial: salva credenciais no Keychain do macOS.

Execute UMA UNICA VEZ apos instalar o projeto:
    python3 setup_credenciais.py

Este script:
1. Pede CPF, senha PDPJ e seed TOTP (2FA)
2. Salva tudo de forma segura no Keychain nativo do macOS
3. Nunca grava credenciais em arquivo

Para trocar uma credencial depois, basta executar este script novamente.
"""
import getpass

import keyring

KEYRING_SERVICE = "mcp-pje-tjpi"


def main():
    print("=" * 60)
    print("  Setup de credenciais - MCP PJe-TJPI 1o Grau")
    print("=" * 60)
    print()
    print("Suas credenciais serao salvas no Keychain do macOS,")
    print("de forma criptografada e segura.")
    print()
    print("IMPORTANTE: Antes de rodar este script, configure o 2FA")
    print("no PJe e guarde a seed TOTP em lugar seguro. Instrucoes")
    print("completas no README.md.")
    print()

    cpf = input("CPF (apenas numeros): ").strip()
    if not cpf or not cpf.isdigit() or len(cpf) != 11:
        print("ERRO: CPF deve ter 11 digitos numericos.")
        return

    senha = getpass.getpass("Senha PDPJ (nao aparece na tela): ")
    if not senha:
        print("ERRO: senha vazia.")
        return

    seed_raw = getpass.getpass("Seed TOTP (nao aparece na tela): ")
    # Limpa espacos, quebras de linha e converte pra maiuscula
    seed = (
        seed_raw.strip()
        .replace(" ", "")
        .replace("\n", "")
        .replace("\t", "")
        .upper()
    )
    if not seed:
        print("ERRO: seed vazia.")
        return

    keyring.set_password(KEYRING_SERVICE, "cpf", cpf)
    keyring.set_password(KEYRING_SERVICE, "senha", senha)
    keyring.set_password(KEYRING_SERVICE, "totp_seed", seed)

    print()
    print(f"OK - credenciais salvas no Keychain (service='{KEYRING_SERVICE}').")
    print(f"     Seed TOTP armazenada com {len(seed)} caracteres.")
    print()
    print("Teste rapido: execute")
    print('    python3 -c "import keyring, pyotp; '
          f'print(pyotp.TOTP(keyring.get_password(\'{KEYRING_SERVICE}\', \'totp_seed\')).now())"')
    print()
    print("Compare o codigo retornado com o que aparece no seu app")
    print("autenticador (Google Authenticator, Authy, etc.).")
    print("Se baterem, a seed esta correta!")


if __name__ == "__main__":
    main()
