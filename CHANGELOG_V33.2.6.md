# V33.2.6

## Teste controlado de escrita

- Adicionado `scripts/test_supabase_write_cycle.py`.
- Usa exclusivamente a chave temporária `_v332_test`.
- Executa upsert, leitura e exclusão.
- Confirma a limpeza ao final.
- Não toca nas 7 chaves reais do `app_state`.
