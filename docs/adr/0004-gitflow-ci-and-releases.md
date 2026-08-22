# ADR 0004 — GitFlow, CI, proteções, releases e hotfixes

- Data: 2026-08-22
- Status: Aceito

## Contexto

Mesmo em um projeto individual, a banca e o responsável precisam distinguir
trabalho em andamento, integração e baseline estável. Push direto em branches
permanentes eliminaria o espaço de revisão e dificultaria associar mudanças às
tarefas Linear e às evidências de validação.

O fluxo também precisa acomodar correções urgentes sobre `main`, remoção
automática de branches integradas e ausência deliberada de revisores externos,
sem abrir exceção para integração não rastreável.

## Decisão

Adotar o seguinte GitFlow:

- `develop` é a branch padrão de integração;
- `main` contém apenas baselines e releases estáveis;
- toda tarefa comum usa uma worktree isolada e branch curta criada da versão
  mais recente de `origin/develop`;
- nenhuma implementação ou push ocorre diretamente em `develop` ou `main`;
- tarefas entram em `develop` por pull request vinculado ao Linear;
- checks de CI e conversas de revisão precisam ser concluídos antes do merge;
- a integração usa squash, e a branch é removida somente depois do merge;
- a exigência de aprovação externa é zero, deliberadamente compatível com o
  único autor, sem dispensar checks, conversas ou revisão de evidências;
- releases são promovidas exclusivamente por pull request de `develop` para
  `main`;
- hotfix nasce de `main` como `hotfix/sen-<id>-<slug>`, retorna a `main` por
  pull request validado e é obrigatoriamente sincronizado de volta a `develop`
  por outro pull request rastreável.

Quando a branch de hotfix for removida automaticamente após o squash, a
sincronização usa uma nova branch `chore/sen-<id>-sync-hotfix` criada de
`origin/develop`; nela se aplica o commit squash preservado em `main`. Os dois
pull requests se referenciam e nenhum passo exige push direto.

Branches `release/` servem apenas para preparação integrada primeiro em
`develop`; elas não substituem o pull request final `develop` → `main`.

A SEN-11 permanece registrada como exceção única de bootstrap criada de `main`.
Ela não estabelece precedente.

### CI e política automatizada vigentes

Três workflows versionados implementam a decisão:

- `.github/workflows/ci.yml` executa em push e pull request para `develop` e
  `main`; valida qualidade, hooks e smoke em Ubuntu, além de testes e smoke em
  Windows;
- `.github/workflows/pull-request-policy.yml` executa em pull requests para as
  branches permanentes, testa sua própria regra e valida o título e a origem da
  branch;
- `.github/workflows/security.yml` executa em push e pull request para as
  branches permanentes, semanalmente e sob acionamento manual; aplica CodeQL a
  Python e JavaScript/TypeScript, revisão de dependências em pull requests e
  varredura de segredos com Gitleaks.

A política de pull request aceita qualquer origem com destino a `develop`. Para
`main`, aceita somente `develop` ou uma branch `hotfix/*` do próprio
repositório. A política humana exige que o título seja escrito em inglês. O gate
automático não infere idioma: valida somente a sintaxe Conventional Commits, os
tipos permitidos, uma descrição ASCII iniciada por letra minúscula ou número, o
limite de 120 caracteres e a ausência de ponto final.

O Dependabot executa semanalmente para os ecossistemas uv, npm, GitHub Actions e
Docker Compose, agrupa atualizações por ecossistema e direciona todos os pull
requests a `develop`.

### Proteções e estratégia de merge vigentes

`develop` é a branch padrão. `develop` e `main` exigem histórico linear, branch
atualizada, resolução de conversas e pull request também para o administrador.
Force push e exclusão das branches permanentes estão bloqueados.

As duas proteções exigem zero aprovações e não tornam a revisão de `CODEOWNERS`
obrigatória. Os oito checks requeridos são:

- `CI / Ubuntu quality`;
- `CI / Windows smoke`;
- `Policy / Title and branch origin`;
- `Security / CodeQL (python)`;
- `Security / CodeQL (javascript-typescript)`;
- `Security / Dependency review`;
- `Security / Secret scan`;
- `CodeQL`.

O repositório permite somente squash merge, usando o título do pull request no
título do commit e o corpo do pull request na mensagem. Merge commit e rebase
merge estão desabilitados, e branches integradas são removidas automaticamente.

## Alternativas consideradas

### GitHub Flow somente com `main`

Seria mais simples, mas misturaria integração corrente e baseline da banca,
reduzindo a clareza da promoção de releases.

### Desenvolvimento direto nas branches permanentes

Evitaria branches curtas, porém removeria isolamento, revisão, checks antes da
integração e vínculo objetivo entre tarefa e mudança.

### Exigir uma aprovação externa

Fortaleceria segregação de funções em uma equipe, mas bloquearia
deliberadamente um projeto de autor único. Checks obrigatórios, conversas e
revisão explícita preservam o controle verificável disponível.

### Merge commits ou rebase merge

Preservariam todo o histórico intermediário, mas acrescentariam ruído ao
histórico de integração. Squash representa cada tarefa como uma unidade e
mantém a rastreabilidade pelo pull request.

### Corrigir o hotfix somente em `main`

Reduziria o tempo imediato, mas faria `develop` reintroduzir a falha na próxima
release. A sincronização rastreável é obrigatória.

## Consequências

- `main` e `develop` comunicam estados distintos e auditáveis.
- Cada mudança tem tarefa, branch, worktree, pull request, checks e evidências.
- Squash mantém o histórico permanente conciso, enquanto detalhes permanecem
  no pull request.
- A ausência de aprovação externa permite fluxo individual sem esconder que a
  revisão depende do próprio responsável e dos controles automatizados.
- Releases exigem uma etapa explícita de promoção.
- Hotfixes exigem dois pull requests e validação duplicada, custo aceito para
  impedir divergência entre `main` e `develop`.
- Proteções e CI passam a ser parte do contrato do repositório; mudanças nesses
  controles devem atualizar este ADR e a documentação relacionada.

## Gatilhos de revisão

Reavaliar quando houver mais responsáveis ativos, necessidade de releases
paralelas, requisitos formais de segregação de funções, tempo de CI
materialmente impeditivo, mudança da estratégia de entrega ou limitação
comprovada da plataforma que impeça o fluxo descrito.

Qualquer revisão deve preservar rastreabilidade, proibição de push direto e
sincronização de hotfixes, ou justificar explicitamente controles equivalentes.
