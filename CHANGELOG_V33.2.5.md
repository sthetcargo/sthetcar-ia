# V33.2.5

## Teste de leitura de foto

- Adicionado `scripts/test_supabase_photo_read.py`.
- Lista somente a pasta de teste configurada no Storage.
- Seleciona um arquivo existente.
- Gera uma URL assinada temporária por 60 segundos.
- Não faz upload, update ou delete.
- A URL assinada nunca é exibida no terminal.
- Pasta padrão: `before`.
