# V33.2.4

## Teste de Storage — somente leitura

- Adicionado `scripts/test_supabase_storage_read.py`.
- Verifica acesso ao bucket `sthetcar-fotos`.
- Não executa upload, delete, update ou criação de bucket.
- Bucket pode ser sobrescrito por `STHETCAR_STORAGE_BUCKET`.
