# V33.2.3

## Validação segura de produção
- Adicionado `scripts/test_supabase_read.py`.
- O teste faz apenas `SELECT key,data` em `public.app_state`.
- Nunca imprime o conteúdo de `data`, apenas nomes de chaves e tipos.
- Não executa INSERT, UPDATE, DELETE, ALTER ou operações no Instagram.

## Produção
- Nenhuma alteração foi feita no Supabase.
