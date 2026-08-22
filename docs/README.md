# Convenções do projeto

## Idioma

Código, nomes de pacotes, módulos, tarefas e demais identificadores técnicos são
escritos em inglês. Documentação, decisões, instruções e explicações do projeto
são escritas em português.

## Separação de conteúdo

- Código-fonte versionável pertence a `apps/`, `infra/` e `scripts/`, conforme a
  responsabilidade de cada área.
- Materiais fornecidos permanecem fora do Git e, quando usados localmente,
  ficam em `data/raw/original/`.
- Fixtures públicas ficam em `data/fixtures/` e devem ser inteiramente
  sintéticas, sem reprodução dos materiais originais.
- Dados intermediários ou processados, caches, builds e demais artefatos gerados
  ficam somente nos caminhos ignorados pelo Git.
- Experimentos ficam em `experiments/` e não constituem código de produção.

Novos módulos devem respeitar os limites do monólito modular e ser adicionados
somente quando a tarefa responsável definir seu comportamento.
