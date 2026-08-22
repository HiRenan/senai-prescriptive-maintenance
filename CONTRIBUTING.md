# Contribuição

Este documento define o fluxo mínimo para evoluir o projeto com mudanças
isoladas, revisáveis e rastreáveis. Regras de segurança e dados permanecem
obrigatórias mesmo quando uma tarefa pedir um caminho mais curto.

## Papéis

- O **P.O./coordenador** detalha o problema, o escopo, as dependências e os
  critérios de aceite; despacha o trabalho, acompanha o progresso, revisa as
  evidências e integra a entrega.
- O **implementador responsável** altera e valida somente a tarefa recebida,
  dentro da worktree designada. Também prepara o commit e o pull request, mas
  não amplia o escopo nem integra a própria entrega sem autorização explícita.

O projeto é individual e, por decisão consciente, exige **zero aprovações
externas**. Essa escolha evita uma dependência impossível de revisão por outra
pessoa, mas não dispensa pull request, checks obrigatórios, resolução das
conversas de revisão, evidências ou revisão do escopo pelo coordenador. O
`CODEOWNERS` identifica `@HiRenan` como responsável sem criar uma exigência de
aprovação externa.

## Branches permanentes

- `main`: baselines e releases estáveis;
- `develop`: integração e branch padrão do trabalho corrente.

Nenhuma implementação é feita diretamente em `main` ou `develop`, e não se faz
push direto para essas branches. Toda alteração entra por pull request.

## Nomes de branches

Use nomes curtos, em inglês, minúsculos e separados por hífen. Quando a mudança
corresponder a uma tarefa Linear, adote `<type>/sen-<id>-<slug>`.

| Prefixo | Uso |
| --- | --- |
| `feat/` | Capacidade nova. |
| `fix/` | Correção de defeito. |
| `docs/` | Documentação. |
| `chore/` | Manutenção sem alteração funcional. |
| `ci/` | Integração contínua e automação de repositório. |
| `test/` | Testes sem nova capacidade de produção. |
| `refactor/` | Reestruturação sem mudança intencional de comportamento. |
| `release/` | Reconciliação curta entre a `develop` validada e a `main` vigente, sem desenvolvimento. |
| `hotfix/` | Correção urgente criada excepcionalmente de `main`. |

Exemplos: `feat/sen-21-asset-registry`, `docs/sen-17-project-governance` e
`hotfix/sen-42-reject-invalid-token`.

A SEN-11 foi uma exceção única de bootstrap criada a partir de `main`. Ela não
é precedente para novas tarefas.

## Fluxo de uma tarefa

1. Leia a tarefa Linear, os critérios de aceite e a documentação relacionada.
2. Atualize as referências remotas e confirme que `origin/develop` é a base
   mais recente disponível.
3. Crie uma worktree isolada e uma branch de tarefa a partir desse commit.
4. Confirme a branch correta, a árvore limpa e a ancestralidade antes de editar.
5. Faça a menor alteração que satisfaça a tarefa e preserve mudanças alheias.
6. Execute a validação mais próxima da mudança e depois as verificações
   agregadas aplicáveis.
7. Crie um commit Conventional Commit em inglês e publique somente a branch de
   tarefa.
8. Abra um pull request para `develop`, vincule a tarefa Linear e registre
   escopo, validações, dados, segurança e limitações.
9. Resolva as conversas e aguarde todos os checks obrigatórios antes da
   integração por squash.
10. Remova a branch de tarefa somente depois da confirmação do merge.

Exemplo de criação manual, após `git fetch origin`:

```powershell
git worktree add ..\sen-21-asset-registry -b feat/sen-21-asset-registry origin/develop
```

Uma worktree criada por outro gerenciador deve preservar a mesma base e o mesmo
isolamento; o comando acima documenta o contrato Git, não uma ferramenta
obrigatória.

## Commits e pull requests

Commits seguem Conventional Commits:

```text
<type>(<optional-scope>): <description>
```

O tipo, o escopo e a descrição são escritos em inglês. Exemplos:

```text
docs: document project governance and architecture decisions
fix(api): reject unsupported environment values
```

O título do pull request também é escrito em inglês e deve resumir a mudança.
O workflow `Pull Request Policy` exige um título Conventional Commits com até
120 caracteres, tipo permitido, descrição em ASCII iniciada por letra minúscula
ou número e sem ponto final. Os tipos aceitos são `build`, `chore`,
`ci`, `docs`, `feat`, `fix`, `perf`, `refactor`, `revert` e `test`; um escopo
técnico opcional pode ser incluído entre parênteses.

A descrição é escrita em português e registra:

- contexto e identificador Linear;
- tipo de fluxo e destino correto;
- alterações dentro do escopo e exclusões deliberadas;
- comandos executados e resultados reais;
- impacto em documentação, segurança e dados;
- riscos conhecidos e trabalho restante.

O histórico da branch pode conter somente o necessário para revisão. Tarefas
comuns entram em `develop` por squash, hotfixes entram em `main` por squash e
releases entram em `main` por merge commit, conforme os fluxos abaixo.

## Controles do repositório

`develop` é a branch padrão. As duas branches permanentes exigem:

- pull request obrigatório, inclusive para o administrador;
- branch atualizada em relação à base;
- resolução de todas as conversas;
- zero aprovações obrigatórias, sem exigir revisão do `CODEOWNERS`;
- force push e exclusão da branch permanente bloqueados;
- oito checks obrigatórios:
  - `CI / Ubuntu quality`;
  - `CI / Windows smoke`;
  - `Policy / Title and branch origin`;
  - `Security / CodeQL (python)`;
  - `Security / CodeQL (javascript-typescript)`;
  - `Security / Dependency review`;
  - `Security / Secret scan`;
  - `CodeQL`.

`develop` mantém a exigência de histórico linear, compatível com o squash de
tarefas. `main` não exige histórico linear exclusivamente para receber merge
commits de release. A configuração do repositório permite squash e merge
commit, mantém rebase merge desabilitado e remove automaticamente a branch
integrada. Os demais controles acima permanecem iguais.

A política automatizada aceita branches de tarefa com destino a `develop`.
Pull requests para `main` são aceitos somente de branches locais com nomes
exatos `release/sen-<id>-<slug>` ou `hotfix/sen-<id>-<slug>`; `develop` direta,
branches de tarefa, nomes malformados, forks e outros destinos são rejeitados.
O mesmo check `Policy / Title and branch origin` executa o gate Git apenas no
fluxo `release/*` → `main`.

Os workflows rastreados e o Dependabot são descritos no
[inventário de arquitetura](docs/architecture/README.md). A decisão e as
alternativas desses controles estão no
[ADR 0004](docs/adr/0004-gitflow-ci-and-releases.md).

## Releases

Uma release promove somente o conteúdo já validado em `develop`; a branch de
release não é lugar de implementação, ajuste preparatório ou correção tardia.
Execute a promoção em uma nova worktree isolada. A worktree principal deve
permanecer em `develop` e limpa durante todo o fluxo.

Partindo da worktree principal, depois de confirmar essas duas condições, crie
o checkout isolado diretamente da `origin/develop` atual:

```powershell
git branch --show-current
git status --short
git fetch --no-tags origin
git worktree add <caminho-isolado> -b release/sen-<id>-<slug> origin/develop
Set-Location <caminho-isolado>
```

Os dois primeiros comandos devem mostrar `develop` e nenhuma alteração. O
`git worktree add` cria outro checkout e não troca a branch da worktree
principal. Uma worktree criada pelo Orca deve preservar a mesma base
`origin/develop`, o isolamento e o nome final da branch; não se reutiliza a
worktree principal para a reconciliação.

Use o fluxo abaixo:

1. atualize as referências, valide a ponta vigente de `origin/develop` e crie
   `release/sen-<id>-<slug>` exatamente desse commit;
2. antes do merge, execute `git merge-tree --write-tree origin/develop
   origin/main`. Em uma release normal, o comando deve concluir sem conflito e
   produzir exatamente a árvore de `origin/develop`; isso prova que incorporar
   `main` é neutro. Se falhar ou produzir outra árvore, sincronize `main` em
   `develop` por um pull request próprio antes de continuar;
3. somente a reparação inicial da divergência legada criada pelo squash do PR
   #8 admite o fallback em que a árvore vigente de `origin/main` já existe em
   um commit alcançável de `origin/develop`. Nesse caso, o merge pode ser
   resolvido conservadoramente, arquivo a arquivo, até reproduzir a árvore de
   `develop`. Não use esse fallback para acomodar uma mudança exclusiva de
   `main`;
4. nessa branch, incorpore somente a `origin/main` vigente com um merge local
   `--no-ff`. Um conflito em uma release normal interrompe o fluxo: aborte o
   merge e sincronize `develop`, sem resolver o conflito na branch de release;
5. confirme que o `HEAD` é exatamente um merge commit com dois pais: a ponta
   vigente de `origin/develop` como primeiro pai e a ponta vigente de
   `origin/main` como segundo pai. Pais adicionais, ordem invertida, commit
   comum ou qualquer commit próprio fazem o gate falhar;
6. prove que `origin/main` é ancestral de `HEAD` e que os hashes de árvore de
   `HEAD` e `origin/develop` são idênticos;
7. publique somente a branch `release/*`, abra o pull request para `main` e
   registre versão, intervalo promovido, oito checks, validações e limitações;
8. depois dos oito checks obrigatórios e das conversas resolvidas, integre o
   pull request com **merge commit**, nunca com squash ou rebase;
9. após a integração, confirme que `main` e `develop` continuam com árvores
   idênticas. A promoção não autoriza push ou alteração direta em nenhuma branch
   permanente.

O gate obrigatório busca novamente as refs de `main`, `develop` e da release
sem persistir credenciais, valida o SHA do evento antes de passá-lo ao Git e
falha fechado quando não consegue comprovar refs, ancestralidade, pais exatos ou
equivalência da árvore da release com `develop`. Para excluir hotfix ainda não
sincronizado, ele aceita uma de duas provas: o merge virtual entre as pontas
vigentes é limpo e produz exatamente a árvore de `develop`, caso normal; ou a
árvore vigente de `main` já aparece no histórico alcançável de `develop`,
fallback restrito à reconciliação da divergência legada. Todos os subprocessos
Git usam `GIT_TERMINAL_PROMPT=0` e `GCM_INTERACTIVE=Never`; falta de acesso, de
refs ou de saída Git válida falha o check em vez de abrir prompt.

Antes do merge, teste literalmente a condição de sincronização no PowerShell:

```powershell
$developTree = git rev-parse 'origin/develop^{tree}'
if ($LASTEXITCODE -ne 0 -or $developTree -notmatch '^[0-9a-f]{40}$') { throw 'origin/develop inválida' }
$mainTree = git rev-parse 'origin/main^{tree}'
if ($LASTEXITCODE -ne 0 -or $mainTree -notmatch '^[0-9a-f]{40}$') { throw 'origin/main inválida' }
$developTrees = git log --format='%T' origin/develop
if ($LASTEXITCODE -ne 0 -or $developTrees.Count -eq 0) { throw 'histórico de origin/develop indisponível' }
$legacyFallback = $mainTree -in $developTrees
$mergeTree = @(git merge-tree --write-tree origin/develop origin/main)
$neutralMerge = $LASTEXITCODE -eq 0 -and $mergeTree.Count -eq 1 -and $mergeTree[0] -eq $developTree
if (-not ($neutralMerge -or $legacyFallback)) { throw 'origin/main não foi sincronizada em develop' }
```

Em seguida, crie o merge e confira o resultado:

```powershell
git merge --no-ff --no-edit origin/main
git merge-base --is-ancestor origin/main HEAD
git rev-list --parents -n 1 HEAD
git rev-parse 'HEAD^{tree}'
git rev-parse 'origin/develop^{tree}'
git diff --exit-code origin/develop HEAD
```

Os dois hashes de árvore devem ser iguais. A linha de `rev-list` deve conter
exatamente três SHAs: o commit da release, seu primeiro pai correspondente a
`origin/develop` e seu segundo pai correspondente a `origin/main`, nessa ordem.
Em uma release normal, `$neutralMerge` deve ser verdadeiro; o
`$legacyFallback` existe somente para reparar a topologia deixada pelo squash
antigo. O workflow repete essas provas contra as pontas remotas vigentes no
momento do check.

## Hotfixes

Hotfix é a única branch de implementação que nasce de `main`:

1. atualize as referências e crie `hotfix/sen-<id>-<slug>` a partir do
   `origin/main` vigente;
2. implemente somente a correção urgente, valide-a e abra um pull request para
   `main`;
3. aguarde os checks e as conversas de revisão, então integre por squash;
4. crie uma nova branch `chore/sen-<id>-sync-hotfix` a partir do
   `origin/develop` atualizado;
5. aplique nessa branch o commit squash que entrou em `main` e abra outro pull
   request rastreável para `develop`;
6. valide e integre a sincronização por squash, sem push direto nas permanentes.

Esse fluxo continua válido quando branches integradas são removidas
automaticamente: a sincronização parte do commit preservado em `main`, não da
branch `hotfix/` já removida. Os dois pull requests devem se referenciar.

## Idioma e documentação

- código, identificadores técnicos, nomes de branches, commits e títulos de
  pull request: inglês;
- documentação, ADRs, apresentações, explicações e descrições de pull request:
  português claro;
- comentários: somente para explicar decisões não óbvias.

Atualize a documentação no mesmo pull request quando o comportamento, um
comando ou uma decisão mudar. Não descreva capacidades futuras como existentes.

## Dados e segurança

- Não leia nem versione materiais originais sem uma tarefa que autorize
  expressamente esse acesso.
- Não force a inclusão de arquivos cobertos pelo `.gitignore`.
- Fixtures públicas devem ser inteiramente sintéticas.
- Nunca versione `.env`, credenciais, tokens, chaves, dumps, volumes, caches ou
  artefatos gerados.
- Use somente valores obviamente fictícios em exemplos locais.
- Em caso de suspeita de vulnerabilidade, siga [`SECURITY.md`](SECURITY.md).

## Critério de revisão

Uma entrega está pronta para integração quando o diff corresponde à tarefa, os
links existem, a documentação descreve o estado real, os checks obrigatórios
passam, as conversas estão resolvidas e não há material local, segredo ou
mudança incidental no commit. A autoria pública é de Renan Mocelin.
