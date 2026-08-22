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

A primeira promoção entre as branches permanentes, no PR #8, usou squash. A
árvore produzida em `main` (`9bbb22b`) era idêntica à árvore promovida de
`develop` (`f0cd01e`), mas o novo commit não passou a fazer parte da
ancestralidade de `develop`: o ancestral comum permaneceu em `59149d`. Quando a
branch longa foi reutilizada, o PR #12 `develop` → `main` voltou a comparar o
histórico desde esse ancestral antigo e ficou conflitante. A
[documentação do GitHub](https://docs.github.com/en/pull-requests/reference/pull-request-merges#squashing-and-merging-a-long-running-branch)
registra que squash de uma branch longa preserva o ancestral comum anterior e
torna conflitos recorrentes mais prováveis.

O fluxo precisa, portanto, manter o benefício do squash para branches curtas de
tarefa sem repetir squash entre `develop` e `main`. Também precisa provar que a
promoção não recebe implementação própria nem conteúdo diferente do que já foi
validado em `develop`.

## Decisão

Adotar o seguinte GitFlow:

- `develop` é a branch padrão de integração;
- `main` contém apenas baselines e releases estáveis;
- toda tarefa comum usa uma worktree isolada e branch curta criada da versão
  mais recente de `origin/develop`;
- nenhuma implementação ou push ocorre diretamente em `develop` ou `main`;
- tarefas entram em `develop` por pull request vinculado ao Linear;
- checks de CI e conversas de revisão precisam ser concluídos antes do merge;
- tarefas comuns entram em `develop` por squash, e a branch é removida somente
  depois do merge;
- a exigência de aprovação externa é zero, deliberadamente compatível com o
  único autor, sem dispensar checks, conversas ou revisão de evidências;
- toda promoção ocorre em uma worktree isolada, mantendo a worktree principal
  em `develop` e limpa, e cria uma branch curta local
  `release/sen-<id>-<slug>` exatamente da ponta validada de `origin/develop`;
  essa branch não recebe desenvolvimento;
- a branch de release incorpora somente a ponta vigente de `origin/main`, por
  merge local; em uma release normal, o merge deve ser limpo e neutro, e seu
  `HEAD` deve ser exatamente um merge commit de dois pais: `origin/develop`
  primeiro e `origin/main` segundo;
- `origin/main` deve ser ancestral do `HEAD` da release, e a árvore desse `HEAD`
  deve ser idêntica à árvore da ponta vigente de `origin/develop`;
- a sincronização é comprovada quando `git merge-tree --write-tree
  origin/develop origin/main` conclui sem conflito e produz exatamente a árvore
  de `origin/develop`; somente a reparação da divergência legada admite como
  fallback a árvore vigente de `origin/main` já existir em um commit alcançável
  de `origin/develop`;
- a promoção usa pull request da branch local `release/*` para `main`, passa
  pelos oito checks obrigatórios e é integrada por merge commit;
- hotfix nasce de `main` como `hotfix/sen-<id>-<slug>`, retorna a `main` por
  pull request validado com squash e é obrigatoriamente sincronizado de volta a
  `develop` por outro pull request rastreável antes da release seguinte.

Quando a branch de hotfix for removida automaticamente após o squash, a
sincronização usa uma nova branch `chore/sen-<id>-sync-hotfix` criada de
`origin/develop`; nela se aplica o commit squash preservado em `main`. Os dois
pull requests se referenciam e nenhum passo exige push direto.

Qualquer avanço de `origin/develop`, `origin/main` ou da própria branch depois da
reconciliação invalida a prova contra refs vigentes. Nesse caso a reconciliação é
refeita com as pontas atuais; não se acrescenta um commit corretivo ou conteúdo
de implementação à branch de release. Em releases normais, um conflito ou uma
árvore diferente no merge virtual obriga sincronizar `main` em `develop` antes
da promoção; o conflito não é resolvido na branch de release. Na reparação
inicial do squash antigo, o fallback de árvore alcançável permite somente
reconciliar a topologia de um conteúdo que já passou por `develop`.

Não se faz pull request direto de `develop`, branch de tarefa ou fork para
`main`, nem push direto em uma branch permanente. A branch curta de release é o
único lugar da reconciliação de ancestralidade.

A SEN-11 permanece registrada como exceção única de bootstrap criada de `main`.
Ela não estabelece precedente.

### CI e política automatizada vigentes

Três workflows versionados implementam a decisão:

- `.github/workflows/ci.yml` executa em push e pull request para `develop` e
  `main`; valida qualidade, hooks e smoke em Ubuntu, além de testes e smoke em
  Windows;
- `.github/workflows/pull-request-policy.yml` executa em pull requests para as
  branches permanentes, testa sua própria regra e valida o título e a origem da
  branch, além da integridade Git das releases;
- `.github/workflows/security.yml` executa em push e pull request para as
  branches permanentes, semanalmente e sob acionamento manual; aplica CodeQL a
  Python e JavaScript/TypeScript, revisão de dependências em pull requests e
  varredura de segredos com Gitleaks.

A política de pull request aceita branches de tarefa com destino a `develop`.
Para `main`, aceita somente branches do próprio repositório com nomes exatos
`release/sen-<id>-<slug>` ou `hotfix/sen-<id>-<slug>` e rejeita `develop` direta,
branches de tarefa, nomes inválidos, forks e destinos não suportados.

No fluxo `release/*` → `main`, o mesmo job obrigatório valida o SHA da head do
evento como hash Git antes de usá-lo, busca por argumentos estruturados as
pontas vigentes de `main`, `develop` e da release, e falha fechado se não puder
provar: correspondência da head com a ref remota; ancestralidade de
`origin/main`; exatamente dois pais na ordem `origin/develop`, `origin/main`; e
igualdade dos hashes de árvore da release e de `origin/develop`. Separadamente,
executa um merge virtual estruturado entre `origin/develop` e `origin/main` e
exige que ele seja limpo e produza a árvore de `develop`. Se essa prova não for
possível, aceita somente o fallback legado em que a árvore validada de `main`
aparece no histórico alcançável de `develop`; fora dessas duas condições, falha
fechado. O gate não roda para tarefa → `develop` nem hotfix → `main` e não
persiste credenciais. Todos os subprocessos Git recebem
`GIT_TERMINAL_PROMPT=0` e `GCM_INTERACTIVE=Never`, portanto indisponibilidade de
ref, autenticação ou saída válida falha fechado sem abrir interação.

A política humana exige que o título seja escrito em inglês. O gate automático
não infere idioma: valida somente a sintaxe Conventional Commits, os tipos
permitidos, uma descrição ASCII iniciada por letra minúscula ou número, o limite
de 120 caracteres e a ausência de ponto final.

O Dependabot executa semanalmente para os ecossistemas uv, npm, GitHub Actions e
Docker Compose, agrupa atualizações por ecossistema e direciona todos os pull
requests a `develop`.

### Proteções e estratégia de merge decididas

`develop` é a branch padrão. `develop` e `main` exigem branch atualizada,
resolução de conversas e pull request também para o administrador. Force push e
exclusão das branches permanentes estão bloqueados.

`develop` mantém a exigência de histórico linear, necessária ao squash de
tarefas. `main` deixa de exigir histórico linear somente para aceitar merge
commits de release; os demais controles permanecem. Essa diferença não autoriza
outros fluxos para `main`, que continuam limitados pela política automatizada e
pelas regras de integração.

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

O repositório permite squash merge e merge commit, mantém rebase merge
desabilitado e remove automaticamente branches integradas. Tarefas e hotfixes
usam squash; somente releases usam merge commit para tornar os commits de
`develop` ancestrais de `main`.

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

### Continuar com squash entre branches longas

Manteria uma única estratégia visual de merge, mas repetiria a causa do PR #12:
o commit squash em `main` não torna os commits promovidos ancestrais e o
ancestral comum de `develop` permanece antigo. Foi rejeitada para promoções;
squash continua correto para branches curtas, que não são reutilizadas.

### Force push, rebase ou reset das branches permanentes

Poderiam alinhar ponteiros ou reescrever a topologia, mas apagariam ou
substituiriam histórico compartilhado, contrariariam as proteções e exigiriam
bypass administrativo. Foram rejeitados porque a promoção deve ser aditiva,
rastreável e repetível sem reescrita de `main` ou `develop`.

### Merge commits ou rebase merge para todas as mudanças

Preservariam todo o histórico intermediário, mas acrescentariam ruído ao
histórico de integração e perderiam a unidade de cada tarefa. Squash permanece
para branches curtas; merge commit fica restrito à reconciliação e à promoção
de releases entre branches permanentes. Rebase merge continua desabilitado.

### Corrigir o hotfix somente em `main`

Reduziria o tempo imediato, mas faria `develop` reintroduzir a falha na próxima
release. A sincronização rastreável é obrigatória.

## Consequências

- `main` e `develop` comunicam estados distintos e auditáveis.
- Cada mudança tem tarefa, branch, worktree, pull request, checks e evidências.
- Squash mantém o histórico permanente conciso, enquanto detalhes permanecem
  no pull request.
- `develop` continua linear; `main` passa a registrar merge commits de release
  para preservar a ancestralidade necessária entre as branches permanentes.
- Cada release acrescenta o merge de reconciliação em `release/*` e o merge
  commit do pull request em `main`. A topologia adicional é um custo deliberado
  para obter repetibilidade, prova de conteúdo e ausência de reescrita.
- Uma alteração concorrente em `main`, `develop` ou na release invalida o gate e
  exige nova reconciliação, em vez de promover refs defasadas.
- Uma mudança exclusiva de `main` impede a promoção enquanto o merge virtual
  não for limpo e neutro. Depois da sincronização rastreável em `develop`, essa
  prova continua válida mesmo que `develop` já contenha trabalhos posteriores.
- O fallback de árvore alcançável fica limitado à reconciliação da topologia
  legada; releases normais não resolvem conflitos na branch de release.
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
