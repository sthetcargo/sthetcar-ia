# Sthetcar IA V33.1

## Correções

- Supabase `app_state` passa a ser a fonte de verdade quando disponível.
- Em `V33_REQUIRE_CLOUD_STATE=1`, falhas do Supabase não são mascaradas por fallback local.
- Escrita dos JSON locais agora é atômica.
- Desconexão do Instagram limpa o estado também no Supabase.
- Descartes da fila de publicação passam pelo mesmo mecanismo de estado centralizado.
- Adicionado `/health` para verificar a disponibilidade do `app_state` sem expor segredos.
- `debug` do Flask deixou de ficar ativado por padrão.
- `.env` real não faz parte da entrega V33.1.

## Próxima etapa

- autenticação da aplicação;
- proteção CSRF das operações POST;
- revisão do ciclo de vida dos objetos no Supabase Storage;
- testes automatizados do fluxo aprovação → preparação → publicação.


## V33.1 — Segurança web
- Adicionada autenticação administrativa por usuário/senha via variáveis de ambiente.
- Adicionado CSRF para todas as operações POST protegidas.
- Adicionadas sessões com HttpOnly, SameSite=Lax e cookie Secure configurável.
- Adicionadas rotas `/login` e `/logout`.
- Rotas administrativas e arquivos enviados passam a exigir autenticação quando habilitada.
- `/health` permanece público para diagnóstico de deploy.
- Credenciais não são armazenadas no código.
