# Sthetcar IA — V33.2.18

## Correção da reescrita de legendas

- Corrigido o fluxo `Reescrever legenda com IA` para exigir uma redação realmente nova.
- A reescrita agora recebe a legenda atualmente exibida no editor como fonte principal dos fatos.
- Cada nova tentativa alterna a estrutura narrativa para evitar repetição da abertura e da sequência de frases.
- Foi adicionada comparação de similaridade entre a legenda atual e a nova resposta.
- Respostas excessivamente semelhantes são rejeitadas e novas tentativas são feitas automaticamente.
- A contagem de reescritas é salva no conteúdo pendente, permitindo variar a estratégia também entre cliques consecutivos.
- Se a IA falhar, a legenda atual é preservada em vez de substituída pelo fallback genérico "Resultado de mais um atendimento".
- Mantidos os requisitos comerciais: fatos observáveis, serviço, processo técnico pertinente, benefícios plausíveis e CTA.

## Não alterado

- Supabase e bucket `sthetcar-fotos`.
- Fotos e análise visual já salvas.
- Fluxo de aprovação e publicação.
- Dados de produção.

## Melhoria — escolha do foco da reescrita
- O botão `Reescrever legenda com IA` agora abre um seletor de abordagem antes de chamar a IA.
- Disponíveis os focos: problema do cliente, processo técnico, transformação antes/depois, benefícios, educativa e comercial.
- O foco escolhido é enviado ao redator como prioridade narrativa, sem eliminar informações técnicas, benefícios pertinentes ou CTA.
- A escolha pode ser diferente a cada nova reescrita, evitando a necessidade de clicar várias vezes sem saber o que será produzido.
