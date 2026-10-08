# V33.2.15

## Correções
- Corrigido o fluxo **Reescrever legenda com IA** para usar um prompt dedicado, sem reaproveitar o gerador principal de forma ambígua.
- Adicionada validação contra vazamento de análise interna, listas, instruções em inglês e legendas truncadas.
- Se a primeira resposta de reescrita for inválida, o sistema faz uma segunda tentativa com instruções ainda mais restritivas.
- A reescrita mantém o foco comercial: transformação, serviço, benefício visual/funcional plausível e CTA.
- Para revitalização/restauração de faróis, a IA pode mencionar lixamento e polimento como etapas usuais e benefícios de iluminação sem promessas técnicas absolutas.
- Nenhuma alteração de Supabase ou Instagram foi feita nesta correção.
