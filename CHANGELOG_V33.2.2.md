# Sthetcar IA — V33.2.2

## Compatibilidade com o estado de produção

- Mantida a tabela `public.app_state` existente (`key`, `data`, `updated_at`).
- `discarded_posts` passou a ser tratado como estado opcional.
- Quando `discarded_posts` não existe no Supabase, a aplicação usa `[]` apenas em memória.
- O estado opcional não é recriado automaticamente e nenhum JSON local antigo é reintroduzido.
- Quando houver um descarte novo, o salvamento normal criará/atualizará a chave no Supabase.
- Chaves essenciais continuam falhando de forma explícita quando ausentes em modo cloud obrigatório.

## Produção

Nenhuma alteração SQL é necessária. Nenhum dado existente do Supabase é migrado ou sobrescrito por esta versão.
