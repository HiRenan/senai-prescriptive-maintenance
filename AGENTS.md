# Instruções para agentes

Este arquivo é a fonte canônica de instruções para qualquer agente que trabalhe
neste repositório. Ele se aplica a toda a árvore. Instruções mais específicas de
uma tarefa podem restringir este documento, mas não podem enfraquecer as regras de
segurança, dados ou GitFlow sem uma decisão explícita do responsável pelo projeto.

## 1. Comece pelo problema

- Leia a tarefa do Linear, o `README.md` e os arquivos diretamente relacionados
  antes de editar.
- Declare premissas e riscos relevantes. Se duas interpretações produzirem
  resultados materialmente diferentes, peça uma decisão ao P.O./coordenador.
- Converta a tarefa em um plano curto, com um critério verificável por etapa.
- Prefira a menor solução que satisfaça os critérios de aceite. Não antecipe
  funcionalidades, abstrações, configurações ou integrações de tarefas futuras.
- Faça mudanças cirúrgicas: não reformate, refatore, renomeie ou remova conteúdo
  alheio ao escopo. Limpe apenas resíduos criados pela própria alteração.
- Toda linha modificada deve ser justificável pela tarefa atual.

## 2. Contexto e arquitetura

- O produto é uma plataforma de manutenção prescritiva para ativos industriais.
- O repositório é um monorepo com `apps/api`, `apps/web`, `infra`, `docs`, `data`,
  `experiments` e `scripts`.
- O backend segue um monólito modular: uma aplicação implantável com fronteiras
  internas claras. Não crie microserviços sem uma decisão arquitetural aprovada.
- O pacote Python de produção usa o namespace `prescriptive_maintenance` e fica em
  `apps/api` com layout `src`.
- `apps/web` é a fronteira do frontend. Só implemente UI, componentes, estilos,
  assets ou dependências visuais quando a tarefa os atribuir explicitamente.
- Descreva apenas funcionalidades existentes. Não apresente planos futuros como
  se já estivessem implementados.

## 3. Papéis e execução

- O P.O./coordenador define e detalha escopo, dependências e critérios; despacha,
  acompanha, revisa e integra a entrega.
- O agente implementador atua apenas na tarefa e worktree recebidas, valida a
  mudança, cria o commit e o pull request e entrega evidências ao coordenador.
- O implementador não amplia escopo, não altera outras tarefas no Linear, não
  modifica configurações do repositório e não integra o próprio pull request sem
  autorização explícita.

## 4. GitFlow obrigatório

- `develop` é a branch padrão de integração. `main` contém somente baselines e
  releases estáveis.
- Toda tarefa nasce da versão mais recente de `origin/develop` em uma worktree
  isolada. Antes de editar, confira branch, árvore limpa e ancestralidade.
- Use branch em inglês no formato `<type>/sen-<id>-<short-description>`, com tipos
  como `feat`, `fix`, `docs`, `chore`, `test` ou `ci`.
- Nunca desenvolva diretamente em `develop` ou `main` e nunca faça push direto
  nessas branches.
- Commits usam Conventional Commits em inglês. O pull request da tarefa aponta
  para `develop`, referencia o identificador Linear e registra validações.
- A integração é feita por squash após revisão. A branch de tarefa só é removida
  depois da confirmação do merge.
- Uma release promove `develop` para `main` por pull request. Hotfix é a única
  exceção: nasce de `main`, retorna a `main` por PR e deve ser sincronizado de
  volta para `develop`.
- A SEN-11 foi uma exceção única de bootstrap criada de `main`; ela não é
  precedente para novas tarefas.

## 5. Dados, segredos e conteúdo público

- O repositório é público e não possui licença de reutilização. Não adicione um
  arquivo `LICENSE` sem decisão explícita.
- Os oito materiais originais são locais: `11 - prova prtica.pdf`, `banner.csv`
  e `Doc1.pdf` a `Doc6.pdf`. Nunca os mova, copie, exclua, versione, publique ou
  reproduza em logs, fixtures, testes ou documentação.
- Só leia os materiais originais quando a tarefa exigir isso expressamente, pelo
  caminho local aprovado e sem expor seu conteúdo. Trate-os como somente leitura.
- Dados públicos de teste devem ser inteiramente sintéticos. Preserve o manifesto
  de hashes e as proteções estabelecidas em `.gitignore` e `data/README.md`.
- Nunca versione `.env`, credenciais, tokens, chaves, dumps, volumes, dados brutos,
  dados processados locais, caches ou artefatos gerados.
- Use valores locais obviamente fictícios em exemplos e identifique-os como
  exclusivos de desenvolvimento.
- Não inclua atribuição a IA, agentes ou ferramentas, nem trailers de coautoria,
  em commits, código, documentação ou pull requests. O conteúdo público deve ser
  profissional e ter Renan Mocelin como autor.

## 6. Convenções do projeto

- Código, identificadores técnicos, branches e commits são escritos em inglês.
- Documentação, ADRs, explicações e descrições de pull request são escritos em
  português claro.
- Comentários explicam decisões não óbvias; não narram o que o código já diz.
- Use UTF-8, fim de linha LF, newline final e as regras de `.editorconfig` e
  `.gitattributes`.
- Preserve mudanças preexistentes do usuário. Não use operações destrutivas para
  descartar trabalho e não remova arquivos fora do escopo.

## 7. Runtimes e dependências

- Python é fixado em `>=3.13,<3.14`; Node.js em `>=22,<23`; pnpm em `10.15.1`.
- `uv.lock` e `pnpm-lock.yaml` vivem somente na raiz e devem permanecer
  reproduzíveis.
- Use `uv` para o workspace Python, Corepack/pnpm para o workspace Node e Poe the
  Poet para a interface de automação do projeto.
- Adicione dependências apenas quando a tarefa demonstrar necessidade. Prefira a
  biblioteca padrão e reutilize convenções existentes antes de introduzir outra
  ferramenta.
- Não invente comandos. Consulte `README.md`, `pyproject.toml` e `package.json` e
  atualize documentação e locks quando um comando ou dependência mudar.

Interface canônica existente na raiz:

```powershell
uv run --frozen poe setup
uv run --frozen poe format
uv run --frozen poe format-check
uv run --frozen poe lint
uv run --frozen poe typecheck
uv run --frozen poe test
uv run --frozen poe check
uv run --frozen poe hooks
uv run --frozen poe services-up
uv run --frozen poe services-down
uv run --frozen poe smoke
uv run --frozen poe smoke --with-services
```

`format` é a única tarefa Poe de qualidade que reescreve código. `check` é uma
sequência fail-fast somente leitura; `services-down` preserva o volume local.

## 8. Validação e entrega

- Valide primeiro o critério mais próximo da mudança e depois a verificação
  agregada disponível. Uma presença de arquivo não substitui um teste funcional.
- Comandos de verificação não devem alterar arquivos rastreados. Se alterarem,
  investigue antes de continuar.
- Confirme locks congelados, `git diff --check`, codificação, ausência de segredos,
  proteção dos materiais originais e escopo do diff.
- Execute todas as verificações exigidas pela tarefa nos ambientes disponíveis.
  Registre limitações reais; não declare como executado o que não foi executado.
- Antes do handoff, confirme o destino do PR, autoria, estado limpo da worktree e
  que `main` não foi alterada.
- O handoff informa branch, commit, URL do PR, arquivos alterados, comandos e
  resultados de validação, riscos e trabalho restante.

Estas regras funcionam quando reduzem mudanças desnecessárias, tornam as decisões
auditáveis e permitem verificar objetivamente se a tarefa terminou.
