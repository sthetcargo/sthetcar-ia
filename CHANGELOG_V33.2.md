# Sthetcar IA — V33.2

V33.2 é uma evolução direta da V33/V33.1. Não recria `app_state`, não muda o bucket `sthetcar-fotos` e não migra os dados existentes.

## Estado online
- Supabase `public.app_state` é a fonte de verdade em produção.
- `V33_REQUIRE_CLOUD_STATE=1` por padrão.
- Falhas transitórias do Supabase não ficam presas em um cache de indisponibilidade permanente.
- JSON local funciona como espelho/cache após leitura ou gravação bem-sucedida no cloud.
- Uma chave ausente no cloud não sobrescreve silenciosamente o cloud com JSON local.
- `V33_SEED_MISSING_FROM_LOCAL=1` existe apenas para uma migração controlada.
- Descartes da fila de publicação passam pelo mesmo estado centralizado.

## Segurança preservada
- Autenticação administrativa.
- CSRF nos POST.
- Cookies HttpOnly/SameSite/Secure configuráveis.
- Segredos fora do pacote.
- Debug desligado por padrão.
- `/health` sem exposição de segredos.

## Compatibilidade
- Bucket: `sthetcar-fotos`.
- Referências permanentes: `supabase://before/...` e `supabase://after/...`.
- Fluxo Instagram e histórico existentes preservados.
