# V33.2.10

## Smoke test independente do ambiente

- Mantida a correção do `sys.path`.
- Se `FLASK_SECRET_KEY` não estiver configurada, o smoke test cria uma chave temporária apenas no processo.
- Se `STHETCAR_ADMIN_PASSWORD` não estiver configurada, cria uma senha temporária apenas no processo.
- Nenhuma credencial é gravada em arquivo ou enviada ao Supabase.
- Continua sem executar IA, upload ou Instagram.
- Sintaxe validada antes do empacotamento.
