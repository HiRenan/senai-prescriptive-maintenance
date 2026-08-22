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
| `release/` | Preparação de release que ainda será integrada em `develop`. |
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

O histórico da branch pode conter somente o necessário para revisão. A
integração usa squash para produzir um único commit coerente em `develop` ou,
nas exceções abaixo, em `main`.

## Controles do repositório

`develop` é a branch padrão. `develop` e `main` possuem as mesmas proteções:

- pull request obrigatório, inclusive para o administrador;
- branch atualizada em relação à base e histórico linear;
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

O repositório permite somente squash merge: merge commits e rebase merge estão
desabilitados. O título do PR forma o título do commit squash, o corpo do PR
forma sua mensagem e a branch integrada é removida automaticamente.

A política automatizada aceita qualquer branch de tarefa com destino a
`develop`. Pull requests para `main` são aceitos somente quando partem de
`develop` ou de uma branch local `hotfix/*`; branches de forks e quaisquer
outras origens são rejeitadas.

Os workflows rastreados e o Dependabot são descritos no
[inventário de arquitetura](docs/architecture/README.md). A decisão e as
alternativas desses controles estão no
[ADR 0004](docs/adr/0004-gitflow-ci-and-releases.md).

## Releases

Uma branch `release/` serve apenas para ajustes preparatórios que entram em
`develop` pelo fluxo comum. A promoção ocorre **exclusivamente** por pull request
com origem `develop` e destino `main`, depois que `develop` estiver validada.

Não faça push direto, merge local ou promoção de uma branch de tarefa para
`main`. O pull request de release deve descrever a versão, o intervalo promovido,
as validações e as limitações conhecidas. A integração também usa squash.

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
