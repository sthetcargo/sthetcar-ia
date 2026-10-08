# Auditoria da V33 enviada

## Diagnóstico encontrado

1. A V33 enviada ainda usa `uploads/before` e `uploads/after` como armazenamento real das fotos.
2. `upload_file()` estava apenas importado em `main.py`; o fluxo real de upload não o chamava.
3. A fila, histórico, rascunho e auditoria usavam arquivos JSON locais em `generated/`.
4. As telas gravavam e exibiam `/uploads/...` diretamente.
5. A publicação no Instagram convertia `/uploads/...` em uma URL pública local, o que não resolve o armazenamento online.
6. O ZIP original continha `generated/instagram_connection.json`, que contém a credencial do Instagram. Esse arquivo foi deliberadamente excluído do pacote Online.
7. O `main.py` e `main.py.v33-backup` do ZIP original diferiam somente por linhas em branco, portanto a tentativa anterior de integrar `upload_file()` não havia sido persistida no arquivo entregue.

## Correções aplicadas nesta versão

- Fotos novas continuam sendo salvas localmente durante a análise da IA, mas também são enviadas ao Storage privado do Supabase.
- As referências armazenadas passam a usar `supabase://...` estável, em vez de URLs temporárias.
- As páginas geram URLs assinadas sob demanda para exibir as fotos.
- A função de publicação do Instagram gera uma URL assinada nova para cada imagem e não depende mais de `/uploads/...` para mídia armazenada na nuvem.
- Histórico, fila, rascunho, auditoria de publicação, descartes e conexão do Instagram passam a usar `app_state` no Supabase quando a tabela existe, mantendo o JSON local como fallback.
- O descarte de um pendente remove também o objeto correspondente do Storage quando a referência já é online.
- Foi adicionado um script único para migrar as fotos e os estados da V33 antiga para a nuvem.
- Foi adicionado o SQL da tabela `app_state`.

## Limitação consciente

Esta entrega prepara a aplicação para usar armazenamento e estado centralizados. Ela ainda precisa ser hospedada em um servidor público para que PC e celular acessem a mesma instância pela internet. O V32 local não é alterado.
