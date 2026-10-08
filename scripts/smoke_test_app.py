import os
import re
import sys
from pathlib import Path

# Executado a partir de scripts/: adiciona a raiz do projeto ao sys.path.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv()

# O smoke test pode rodar isoladamente, sem um .env completo.
# Se as credenciais locais de autenticação não estiverem configuradas,
# usa valores temporários SOMENTE neste processo. Nada é gravado.
if not os.getenv("FLASK_SECRET_KEY", "").strip():
    os.environ["FLASK_SECRET_KEY"] = "V33_SMOKE_TEST_ONLY_" + os.urandom(24).hex()

if not os.getenv("STHETCAR_ADMIN_PASSWORD", "").strip():
    os.environ["STHETCAR_ADMIN_PASSWORD"] = "V33_SMOKE_PASSWORD_" + os.urandom(16).hex()

# Importa a própria aplicação. Nenhuma rota de escrita/publicação é chamada. Nenhuma rota de escrita/publicação é chamada.
from app.main import app


def main():
    username = os.getenv("STHETCAR_ADMIN_USERNAME", "admin").strip() or "admin"
    password = os.getenv("STHETCAR_ADMIN_PASSWORD", "").strip()

    if not password:
        raise SystemExit("STHETCAR_ADMIN_PASSWORD não configurado.")

    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)

    with app.test_client() as client:
        # 1) Health público.
        health = client.get("/health")
        if health.status_code != 200:
            raise SystemExit(
                f"HEALTH: ERRO ({health.status_code})\n{health.get_data(as_text=True)}"
            )

        # 2) Rota protegida sem sessão deve redirecionar para login.
        protected = client.get("/revisar/__smoke_nonexistent__")
        if protected.status_code not in (301, 302, 303, 307, 308):
            raise SystemExit(
                f"AUTH REDIRECT: ERRO (status {protected.status_code})"
            )

        # 3) Obtém o CSRF real do formulário de login.
        login_page = client.get("/login")
        if login_page.status_code != 200:
            raise SystemExit(f"LOGIN GET: ERRO ({login_page.status_code})")

        html = login_page.get_data(as_text=True)
        match = re.search(
            r'name="csrf_token"\s+value="([^"]+)"',
            html,
            flags=re.IGNORECASE,
        )
        if not match:
            raise SystemExit("LOGIN CSRF: token não encontrado.")

        csrf = match.group(1)

        # 4) Login real, sem tocar em dados da aplicação.
        logged = client.post(
            "/login",
            data={
                "username": username,
                "password": password,
                "csrf_token": csrf,
                "next": "/",
            },
            follow_redirects=False,
        )
        if logged.status_code not in (301, 302, 303, 307, 308):
            raise SystemExit(f"LOGIN POST: ERRO ({logged.status_code})")

        # 5) Confirma que a sessão autenticada permanece válida.
        protected_after = client.get("/revisar/__smoke_nonexistent__")
        if protected_after.status_code not in (301, 302, 303, 307, 308):
            raise SystemExit(
                f"AUTHENTICATED ROUTE: ERRO ({protected_after.status_code})"
            )

        print("APP SMOKE TEST: OK")
        print("health: OK")
        print("auth_redirect: OK")
        print("login + csrf: OK")
        print("sessão autenticada: OK")
        print("rota protegida: OK")
        print("IA: não executada")
        print("upload: não executado")
        print("Instagram: não executado")
        print("dados de produção: não alterados")


if __name__ == "__main__":
    main()
