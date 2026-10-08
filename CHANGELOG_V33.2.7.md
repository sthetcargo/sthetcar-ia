# V33.2.7

## Smoke test da aplicação

- Adicionado `scripts/smoke_test_app.py`.
- Testa a própria aplicação Flask.
- Verifica `/health`.
- Verifica redirecionamento de rota protegida.
- Obtém e envia CSRF real do login.
- Testa autenticação e sessão.
- Não executa IA, upload ou Instagram.
- Não altera dados de produção.
