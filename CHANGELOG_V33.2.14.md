# V33.2.14

## Correção do erro `bad character range`

Corrigido o regex de higienização do nome do veículo em `app/main.py`.

O padrão anterior continha um intervalo de caracteres corrompido (`Ã€-Ã¿`), que fazia o Python/regex retornar `bad character range`.

A validação agora usa `\w` com Unicode, evitando o intervalo inválido e mantendo nomes de veículos com acentos.

Nenhum dado de produção, Supabase ou Instagram foi alterado.
