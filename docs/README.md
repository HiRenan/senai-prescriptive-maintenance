# Documentação do projeto

O README raiz apresenta o problema, o estado implementado e o caminho de
execução. Os documentos desta área aprofundam responsabilidades específicas
sem repetir extensamente a mesma orientação.

## Índice

| Documento | Responsabilidade |
| --- | --- |
| [`../README.md`](../README.md) | Orientação inicial, quickstart e limites do produto atual. |
| [`../CONTRIBUTING.md`](../CONTRIBUTING.md) | GitFlow, papéis, branches, commits, pull requests, releases e hotfixes. |
| [`../SECURITY.md`](../SECURITY.md) | Reporte privado, segredos, vazamentos e práticas proibidas em produção. |
| [`adr/README.md`](adr/README.md) | Índice das decisões arquiteturais aceitas. |
| [`architecture/README.md`](architecture/README.md) | Inventário dos componentes que realmente existem. |
| [`../data/README.md`](../data/README.md) | Preparação local, integridade e fronteira dos materiais e fixtures. |

## Idioma

Código, nomes de pacotes, módulos, tarefas, branches, commits e títulos de pull
request são escritos em inglês. Documentação, ADRs, apresentações, instruções,
explicações e descrições de pull request são escritas em português claro.

## Separação de conteúdo

- Código-fonte versionável pertence a `apps/`, `infra/` e `scripts`, conforme a
  responsabilidade de cada área.
- Materiais fornecidos permanecem fora do Git e, quando usados localmente,
  ficam em `data/raw/original/`.
- Fixtures públicas ficam em `data/fixtures/` e devem ser inteiramente
  sintéticas, sem reprodução dos materiais originais.
- Dados intermediários ou processados, caches, builds e demais artefatos
  gerados ficam somente nos caminhos ignorados pelo Git.
- Experimentos ficam em `experiments/` e não constituem código de produção.

Novos módulos devem respeitar os limites do monólito modular e ser adicionados
somente quando a tarefa responsável definir comportamento e critérios
verificáveis. Visões futuras devem estar identificadas como não implementadas;
o inventário de arquitetura descreve apenas o que pode ser comprovado no
repositório.
