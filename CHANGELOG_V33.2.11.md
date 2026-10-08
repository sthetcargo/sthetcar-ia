# V33.2.11

## Correção do envio das fotos

- O formulário de criação de conteúdo agora usa `enctype="multipart/form-data"` corretamente.
- Removido o `enctype` que estava incorretamente colocado em um campo oculto.
- O JavaScript agora localiza o formulário por `id="uploadForm"`.
- Mantida a compressão no navegador e o envio via `FormData`.
- Nenhuma alteração foi feita no Supabase ou nos dados de produção.
