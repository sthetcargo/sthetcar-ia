# Sthetcar IA — aplicação online

Aplicativo Flask para preparar publicações de estética automotiva a partir de fotos de ANTES e DEPOIS. A IA analisa as imagens, ajuda a gerar a legenda e aguarda a revisão e a aprovação do responsável.

## Fluxo atual

- Aceita até 6 fotos de ANTES e 6 fotos de DEPOIS (JPG, JPEG, PNG ou WEBP).
- Comprime as imagens no navegador e cria cópias protegidas de publicação em JPEG quadrado de 1080 × 1080, preservando os arquivos originais.
- A proteção de placas é uma ajuda de privacidade e precisa ser conferida visualmente; a detecção automática não é infalível.
- A preparação de mídia do Instagram não publica por si só. A publicação final exige aprovação e confirmação explícita.
- O aplicativo pode preparar conteúdo para Instagram e Facebook, com resultados e auditorias separados por plataforma.
- Vídeos MP4/MOV estão temporariamente bloqueados na criação de posts até que exista proteção de placas por quadro de vídeo.

## Estado e armazenamento

O histórico, a fila de aprovação, os rascunhos e as auditorias podem ser centralizados na tabela `app_state` do Supabase. As fotos são armazenadas no bucket privado `sthetcar-fotos` e as telas usam URLs assinadas sob demanda.

O fluxo online requer a tabela `app_state` e as variáveis de ambiente configuradas no serviço. Não habilite migração automática de JSON local para Supabase sem revisar os dados e executar uma migração controlada.

## Variáveis de ambiente

As credenciais devem ficar no ambiente do Render, nunca no HTML, no repositório ou em arquivos enviados ao usuário. Entre as variáveis exigidas estão `FLASK_SECRET_KEY`, `STHETCAR_ADMIN_PASSWORD`, `SUPABASE_URL`, `SUPABASE_SECRET_KEY` e `GEMINI_API_KEY`. Consulte `FACEBOOK_SETUP_V33.2.21.md` para as variáveis específicas da Página do Facebook.

## Desenvolvimento e testes

- Versão Python usada em produção e nos testes: `3.14.3` (`.python-version`).
- Instale as dependências com `python -m pip install -r requirements.txt`.
- Execute os testes locais offline com `python -m unittest discover -s tests -v`.
- Execute as verificações estáticas dos fluxos e templates com `python scripts/check_facebook_integration.py`.

Os testes do Gemini usam clientes simulados e não precisam de chave de API nem publicam conteúdo.
