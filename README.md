# Sthetcar IA V33 Online

Esta versÃ£o prepara o aplicativo para usar o Supabase como camada central de fotos e estado, mantendo o fluxo atual de IA, revisÃ£o, aprovaÃ§Ã£o, fila e publicaÃ§Ã£o.

## O que foi corrigido
- Fotos novas sÃ£o mantidas localmente apenas para a anÃ¡lise e tambÃ©m enviadas ao bucket privado `sthetcar-fotos`.
- O histÃ³rico, fila, rascunho, auditoria de publicaÃ§Ã£o e conexÃ£o do Instagram podem ser centralizados na tabela `app_state`.
- As telas passam a gerar URLs assinadas sob demanda, em vez de guardar links temporÃ¡rios.
- A publicaÃ§Ã£o do Instagram gera uma URL assinada nova para cada foto e nÃ£o depende mais de `/uploads/...` nem de `PUBLIC_BASE_URL` para entregar a mÃ­dia.
- Descartar uma pendÃªncia tambÃ©m remove os objetos do Storage quando eles jÃ¡ estÃ£o na nuvem.
- Compatibilidade local continua existindo como fallback.

## SeguranÃ§a
A chave `SUPABASE_SECRET_KEY` continua somente no backend. Ela nÃ£o deve entrar no ZIP nem ser colocada no HTML. O arquivo local `generated/instagram_connection.json` foi deliberadamente removido desta distribuiÃ§Ã£o.

## Primeiro uso
1. No Supabase SQL Editor, execute `supabase_app_state.sql`.
2. No novo diretÃ³rio, coloque o `.env` da instalaÃ§Ã£o atual sem alterar os valores secretos.
3. Rode `python migrar_v33_para_nuvem.py --source "E:\\Sthetcar IA V33"`.
4. Inicie o V33 Online. O aplicativo continuarÃ¡ aceitando o fluxo local como fallback caso a tabela ainda nÃ£o esteja disponÃ­vel.

## ObservaÃ§Ã£o
O pacote nÃ£o contÃ©m a credencial do Instagram. A migraÃ§Ã£o para a nuvem copia esse estado do diretÃ³rio antigo para o Supabase sem exibir o token.
