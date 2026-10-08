# Sthetcar IA — V33.2.1

## Correções de produção

- `app_state` continua sendo a fonte oficial do estado online.
- Uma chave ausente no Supabase não faz mais o aplicativo restaurar silenciosamente um JSON local antigo.
- O fallback local só pode ser usado com `V33_SEED_MISSING_FROM_LOCAL=1`, em uma migração controlada.
- Em produção com autenticação habilitada, `FLASK_SECRET_KEY` agora é obrigatório; não existe mais segredo de sessão aleatório gerado a cada reinício.
- Mantida a compatibilidade com o `app_state` e o bucket `sthetcar-fotos` já existentes.
- Sintaxe Python validada com `compileall`.
