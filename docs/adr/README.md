# Registros de decisão arquitetural

Os ADRs registram decisões que condicionam a evolução do projeto. Cada registro
expõe contexto, decisão, alternativas, consequências e fatos que justificariam
uma nova avaliação. Uma mudança posterior não apaga o histórico: cria um novo
ADR que substitui o anterior e atualiza o status de ambos.

## Índice

| ADR | Decisão | Data | Status |
| --- | --- | --- | --- |
| [0001](0001-monorepo-and-modular-monolith.md) | Monorepo e monólito modular | 2026-08-22 | Aceito |
| [0002](0002-public-repository-and-source-boundary.md) | Repositório público, ausência de licença e fronteira dos materiais | 2026-08-22 | Aceito |
| [0003](0003-runtimes-and-workspace-tooling.md) | Python 3.13, Node.js 22, uv, pnpm e Poe | 2026-08-22 | Aceito |
| [0004](0004-gitflow-ci-and-releases.md) | GitFlow, CI e promoção repetível entre branches permanentes | 2026-08-22 | Aceito |

## Estados

- **Proposto:** em discussão, sem força normativa.
- **Aceito:** decisão vigente.
- **Substituído:** preservado como histórico e apontando para o ADR sucessor.
- **Rejeitado:** avaliado e não adotado.

## Criação e revisão

Novos ADRs usam o próximo número de quatro dígitos, título técnico em inglês no
nome do arquivo e conteúdo em português. A data registra a decisão, não a data
de uma edição meramente textual. Alterações que mudam a decisão exigem novo
registro; correções de clareza que preservam o significado podem atualizar o
arquivo existente.
