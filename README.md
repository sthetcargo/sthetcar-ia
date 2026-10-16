## V33.2.19 — suporte a vídeo

A versão atual aceita fotos e vídeos MP4/MOV em ANTES e DEPOIS, analisa as mídias com IA e prepara publicação no Instagram como mídia única ou carrossel.

# Sthetcar IA V33 Online

Esta versão prepara o aplicativo para usar o Supabase como camada central de fotos e estado, mantendo o fluxo atual de IA, revisão, aprovação, fila e publicação.

## O que foi corrigido
- Fotos novas são mantidas localmente apenas para a análise e também enviadas ao bucket privado `sthetcar-fotos`.
- O histórico, fila, rascunho, auditoria de publicação e conexão do Instagram podem ser centralizados na tabela `app_state`.
- As telas passam a gerar URLs assinadas sob demanda, em vez de guardar links temporários.
- A publicação do Instagram gera uma URL assinada nova para cada foto e não depende mais de `/uploads/...` nem de `PUBLIC_BASE_URL` para entregar a mídia.
- Descartar uma pendência também remove os objetos do Storage quando eles já estão na nuvem.
- Compatibilidade local continua existindo como fallback.

## Segurança
A chave `SUPABASE_SECRET_KEY` continua somente no backend. Ela não deve entrar no ZIP nem ser colocada no HTML. O arquivo local `generated/instagram_connection.json` foi deliberadamente removido desta distribuição.

## Primeiro uso
1. No Supabase SQL Editor, execute `supabase_app_state.sql`.
2. No novo diretório, coloque o `.env` da instalação atual sem alterar os valores secretos.
3. Rode `python migrar_v33_para_nuvem.py --source "E:\\Sthetcar IA V33"`.
4. Inicie o V33 Online. O aplicativo continuará aceitando o fluxo local como fallback caso a tabela ainda não esteja disponível.

## Observação
O pacote não contém a credencial do Instagram. A migração para a nuvem copia esse estado do diretório antigo para o Supabase sem exibir o token.
