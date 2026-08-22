# Validação clean-room da Foundation

- Data: 2026-08-22
- Escopo: SEN-18
- Branch avaliada: `develop`
- Commit avaliado: `514ca43070d64e704664b076bbb09a7e509f1a83`
- Resultado: aprovado sem ajuste de código, dependências ou configuração

Este relatório registra três fontes separadas de evidência: o que foi executado
em um clone temporário sem contexto local, o que foi consultado no GitHub e o
que ainda depende da integração desta tarefa. Ele descreve somente a Foundation
existente no commit avaliado.

## Clone anônimo e ambiente

O clone foi feito da URL HTTPS pública em um diretório novo, único, vazio,
resolvido sob o diretório temporário do sistema e fora de qualquer checkout Git.
No processo de clone, tokens de GitHub foram removidos, prompts e `askpass`
foram desabilitados, configurações Git de usuário e sistema não foram carregadas,
o helper de credenciais foi esvaziado e cabeçalhos HTTP adicionais foram
neutralizados. Nenhuma sessão autenticada foi reutilizada.

O clone completo terminou em aproximadamente 1,0 s. `origin/HEAD` resolveu para
`origin/develop`, o checkout inicial foi `develop` no commit avaliado e
`origin/main` estava acessível no commit
`9bbb22b2c9bc82bc94fbc2b8170e708fd575921d`. O repositório não era shallow.

| Ferramenta | Versão efetiva |
| --- | --- |
| Sistema local | Windows |
| Git | 2.50.1.windows.1 |
| Python | 3.13.5 |
| uv | 0.9.4 |
| Node.js | 22.18.0 |
| pnpm via Corepack | 10.15.1 |
| Docker | 28.4.0 |
| Docker Compose | 2.39.2-desktop.1 |

Nenhum `.env` pessoal, cache, ambiente virtual, dado original ou arquivo da
worktree de implementação foi levado para o clone.

## Execução local

Os tempos são aproximados e correspondem a uma execução no ambiente acima.

| Etapa | Comando | Resultado observado | Duração |
| --- | --- | --- | ---: |
| Integridade do lock Python | `uv lock --check` | 43 pacotes resolvidos pelo lock, sem divergência | 0,2 s |
| Setup documentado | `uv run --frozen poe setup` | executou `uv sync --all-packages --frozen`, `corepack pnpm install --frozen-lockfile` e instalou os hooks; 41 pacotes Python auditados e pnpm sem alteração | 8,7 s |
| Qualidade completa | `uv run --frozen poe check` | format-check aprovou 27 arquivos; Ruff aprovado; Pyright strict com 0 erros; 9 testes aprovados; cobertura 100% | 7,9 s |
| Políticas locais | `uv run --frozen poe hooks` | 11 hooks aprovados, incluindo YAML, JSON, TOML, conflitos, arquivos grandes, chave privada, whitespace, EOF, line endings e Ruff | 55,3 s |
| Política de PR | `node --test .github/scripts/pull-request-policy.test.js` | 7 testes aprovados para título e origem da branch | 0,2 s |
| Smoke sem serviços | `uv run --frozen poe smoke` | runtimes, pacote, `.env.example`, Compose, Uvicorn e `GET /health/live` aprovados | 4,0 s |
| PostgreSQL isolado | `uv run --frozen poe services-up` | serviço saudável em porta alta de loopback | 7,1 s |
| Consulta vetorial | `docker compose exec ... psql ...` | PostgreSQL 17, extensão pgvector 0.8.6 e operação com o tipo `vector` aprovados | 0,3 s |
| Smoke com serviços | `uv run --frozen poe smoke --with-services` | health do banco, pgvector, operação vetorial e liveness aprovados | 6,4 s |
| Limpeza isolada | `docker compose -p sen18-cleanroom-e195ab0e29ee down --volumes --remove-orphans` | contêiner, rede e volume exclusivos removidos | 1,3 s |

As instalações congeladas não alteraram `pyproject.toml`, os manifests de
pacote, `uv.lock` ou `pnpm-lock.yaml`; nenhuma dependência foi resolvida fora dos
locks. `git status --short` permaneceu vazio depois do setup, dos checks e dos
smokes. `.venv`, `node_modules`, caches do Pytest e Ruff e cobertura foram
gerados apenas em caminhos ignorados.

## PostgreSQL, pgvector e isolamento Docker

Foi escolhida dinamicamente a porta alta livre `60279`, nunca `5432`, e o projeto
Compose exclusivo `sen18-cleanroom-e195ab0e29ee`. O bind permaneceu em
`127.0.0.1`, o `up --wait` confirmou health real e uma consulta validou
PostgreSQL 17, pgvector 0.8.6 e uma distância entre dois valores `vector`.

Antes do `up`, foram obtidas impressões digitais somente leitura dos listeners
da porta 5432 e dos conjuntos de contêineres, redes e volumes. Depois do
`down --volumes --remove-orphans`, não restou recurso com o rótulo do projeto
exclusivo, a porta alta voltou a ficar livre e todas as impressões digitais
preexistentes continuaram idênticas. O serviço PostgreSQL preexistente
permaneceu intacto na porta 5432.

## Auditoria do clone

- Nenhum dos oito nomes dos materiais aparece como caminho no checkout, no
  índice ou nos 11 commits alcançáveis. Não há caminho PDF, assinatura `%PDF-`,
  CSV bruto nem arquivo `LICENSE` nesse histórico.
- As ocorrências textuais permitidas dos nomes permanecem restritas ao manifesto
  e à documentação de fronteira; os oito binários originais estão
  intencionalmente ausentes.
- [`data/source-manifest.json`](../../data/source-manifest.json) conserva oito
  entradas válidas com tamanho e SHA-256, e as duas fixtures rastreadas em
  `data/fixtures/` continuam pequenas, explicitamente sintéticas e inalteradas.
- A busca no checkout e no histórico por formatos de tokens, chaves privadas,
  caminhos do usuário local e atribuição indevida não encontrou ocorrências.
  O hook de chave privada também passou.
- Foram verificados 52 arquivos de texto: todos decodificaram como UTF-8,
  usaram LF e terminaram com newline. `git diff --check` passou.
- Foram verificados 19 arquivos Markdown e 32 links relativos, sem destino
  ausente. Os 11 comandos Poe documentados correspondem a tarefas existentes.

Essa auditoria verifica caminhos, assinaturas, padrões e fronteiras públicas;
ela não lê nem tenta validar o conteúdo dos materiais originais ausentes.

## Evidências do GitHub

### Repositório e proteções

A [API do repositório](https://api.github.com/repos/HiRenan/senai-prescriptive-maintenance)
confirmou visibilidade pública, `develop` como branch padrão, somente squash
merge, merge commit e rebase merge desabilitados e exclusão automática da
branch integrada. As consultas REST de proteção foram somente leitura e não
alteraram settings.

As proteções de `develop` e `main` são equivalentes:

| Controle | Valor verificado nas duas branches |
| --- | --- |
| Pull request | obrigatório, inclusive para administradores |
| Aprovações externas | 0; revisão de `CODEOWNERS` não obrigatória |
| Status checks | estritos e com branch atualizada |
| Conversas | resolução obrigatória |
| Histórico | linear obrigatório |
| Force push | bloqueado |
| Exclusão | bloqueada |

Os oito contextos estritos retornados pela API foram:

1. `CI / Ubuntu quality`;
2. `CI / Windows smoke`;
3. `Policy / Title and branch origin`;
4. `Security / CodeQL (python)`;
5. `Security / CodeQL (javascript-typescript)`;
6. `Security / Dependency review`;
7. `Security / Secret scan`;
8. `CodeQL`.

Não foi tentado push direto em branch permanente. A rejeição foi verificada
pela configuração de proteção e pelo histórico de mudanças integradas por pull
request, sem uma tentativa destrutiva.

### Execuções no commit avaliado

No `HEAD` de `develop`, a execução
[CI 32556288871](https://github.com/HiRenan/senai-prescriptive-maintenance/actions/runs/32556288871)
terminou verde, incluindo
[Ubuntu quality](https://github.com/HiRenan/senai-prescriptive-maintenance/actions/runs/32556288871/job/96990869249)
e
[Windows smoke](https://github.com/HiRenan/senai-prescriptive-maintenance/actions/runs/32556288871/job/96990869197).
A execução
[Security 32556288877](https://github.com/HiRenan/senai-prescriptive-maintenance/actions/runs/32556288877)
também terminou verde, com CodeQL para
[Python](https://github.com/HiRenan/senai-prescriptive-maintenance/actions/runs/32556288877/job/96990869212)
e
[JavaScript/TypeScript](https://github.com/HiRenan/senai-prescriptive-maintenance/actions/runs/32556288877/job/96990869243),
além da
[varredura de segredos](https://github.com/HiRenan/senai-prescriptive-maintenance/actions/runs/32556288877/job/96990869122).

A revisão de dependências foi corretamente ignorada nesse evento `push`, pois o
workflow a restringe a pull requests. Seu sucesso real, assim como o dos demais
oito contextos, foi confirmado no PR de tarefa descrito a seguir.

### Fluxo de tarefa, squash e promoção

Sete PRs de tarefa — #2 a #7 e #10 — já foram integrados a `develop`. O
[PR #10](https://github.com/HiRenan/senai-prescriptive-maintenance/pull/10)
é a evidência mais recente antes desta validação: saiu de
`docs/sen-17-project-governance`, teve como base `develop` e continha um único
commit baseado em `f0cd01e2f8f29b3bbcb160906af1fcc5ced796da`. O squash gerou
`514ca43070d64e704664b076bbb09a7e509f1a83`, também com
`f0cd01e2f8f29b3bbcb160906af1fcc5ced796da` como único pai. A branch de origem não
existe mais no remoto, coerente com a exclusão automática.

Todos os contextos obrigatórios do PR #10 ficaram verdes:

- [Ubuntu quality](https://github.com/HiRenan/senai-prescriptive-maintenance/actions/runs/32555937781/job/96989923939);
- [Windows smoke](https://github.com/HiRenan/senai-prescriptive-maintenance/actions/runs/32555937781/job/96989923815);
- [política de título e origem](https://github.com/HiRenan/senai-prescriptive-maintenance/actions/runs/32555972834/job/96990015014);
- [CodeQL Python](https://github.com/HiRenan/senai-prescriptive-maintenance/actions/runs/32555937840/job/96989924232);
- [CodeQL JavaScript/TypeScript](https://github.com/HiRenan/senai-prescriptive-maintenance/actions/runs/32555937840/job/96989924336);
- [revisão de dependências](https://github.com/HiRenan/senai-prescriptive-maintenance/actions/runs/32555937840/job/96989924122);
- [varredura de segredos](https://github.com/HiRenan/senai-prescriptive-maintenance/actions/runs/32555937840/job/96989924246);
- [CodeQL](https://github.com/HiRenan/senai-prescriptive-maintenance/runs/96990010041).

O [PR #8](https://github.com/HiRenan/senai-prescriptive-maintenance/pull/8)
comprova a promoção `develop` → `main`: foi validado e integrado por squash no
commit `9bbb22b2c9bc82bc94fbc2b8170e708fd575921d`, também com um único pai. A árvore
desse commit é idêntica à árvore de
`f0cd01e2f8f29b3bbcb160906af1fcc5ced796da`; assim, `main` era uma baseline
estável, deliberadamente um commit documental atrás.

O [PR Dependabot #9](https://github.com/HiRenan/senai-prescriptive-maintenance/pull/9)
continuava aberto, isolado em sua própria branch, com destino a `develop` e sem
merge.

## Correspondência com os critérios da SEN-1

| Critério da Foundation | Evidência verificável |
| --- | --- |
| Acesso e clone sem autenticação | Método de clone acima e repositório público confirmado pela API. |
| Preparação por clone limpo e comandos documentados | Tabela de execução, locks congelados e `git status` vazio. |
| Validação local e no CI sem etapa oculta | Execução local completa e runs Ubuntu, Windows e Security vinculadas. |
| Ausência de segredo, dataset bruto e PDF original | Auditoria de checkout, índice e histórico, hooks e Secret scan. |
| `develop` padrão com PR e checks | API do repositório, proteções e PR #10. |
| `main` recebe promoção estável por PR | PR #8 e equivalência da árvore promovida. |
| Tarefas posteriores ao bootstrap não entram direto nas permanentes | PRs #2–#7 e #10 para `develop`, com #10 detalhado. |
| Estrutura, convenções, responsabilidades e conteúdo público | [`README.md`](../../README.md), [`CONTRIBUTING.md`](../../CONTRIBUTING.md), [`SECURITY.md`](../../SECURITY.md) e [inventário](../architecture/README.md). |
| Decisões com justificativa, alternativas e consequências | [ADRs 0001–0004](../adr/README.md). |
| Repositório público sem licença de reutilização | Ausência de `LICENSE`, declaração do README e [ADR 0002](../adr/0002-public-repository-and-source-boundary.md). |

O mapa comprova apenas a fundação presente no commit avaliado. Não comprova nem
antecipa funcionalidades das épicas seguintes.

## Limitações e etapa pendente

- Docker com Compose v2 é pré-requisito para validar a configuração e os
  serviços.
- Os materiais originais são intencionalmente ausentes; o clone valida o
  manifesto e a fronteira pública, não o conteúdo desses arquivos.
- O Windows foi validado neste clone e pelo smoke do GitHub. O job oficial de
  qualidade completa roda em Ubuntu; o job Windows cobre testes essenciais e
  smoke.
- PostgreSQL/pgvector é validado como infraestrutura local. A aplicação ainda
  não integra persistência, e liveness não equivale a readiness.
- UI, domínio prescritivo, ingestão e demais capacidades futuras não estão
  implementados.
- A proteção contra push direto foi verificada por API e histórico de PRs, não
  por tentativa de violação.
- A integração deste relatório em `develop` e a promoção final da Foundation de
  `develop` para `main` permanecem sob responsabilidade do coordenador. Nenhuma
  promoção foi executada nesta validação.

## Reprodução resumida

Abra um PowerShell temporário, crie um diretório único e vazio sob o diretório
temporário do sistema e confirme o caminho resolvido. No mesmo processo, remova
somente as variáveis de autenticação indicadas, desabilite prompts e configurações
Git de usuário/sistema e execute o clone. Encerre esse shell ao final; não imprima
nem restaure valores de tokens nele.

```powershell
Remove-Item Env:GH_TOKEN -ErrorAction SilentlyContinue
Remove-Item Env:GITHUB_TOKEN -ErrorAction SilentlyContinue
Remove-Item Env:GIT_ASKPASS -ErrorAction SilentlyContinue
Remove-Item Env:SSH_ASKPASS -ErrorAction SilentlyContinue
$env:GIT_TERMINAL_PROMPT = "0"
$env:GIT_CONFIG_NOSYSTEM = "1"
$env:GIT_CONFIG_GLOBAL = "NUL"

git -c credential.helper= -c core.askPass= -c http.extraHeader= clone `
  https://github.com/HiRenan/senai-prescriptive-maintenance.git `
  <diretorio-temporario-vazio>
```

No clone, siga somente arquivos rastreados:

```powershell
uv lock --check
uv run --frozen poe setup
uv run --frozen poe check
uv run --frozen poe hooks
uv run --frozen poe smoke
node --test .github/scripts/pull-request-policy.test.js
```

Para os serviços, capture localmente as impressões digitais dos recursos
preexistentes, escolha uma porta alta livre e use um projeto exclusivo:

```powershell
$composeProject = "sen18-cleanroom-$([guid]::NewGuid().ToString('N').Substring(0, 12))"
do {
    $probe = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
    $probe.Start()
    $hostPort = ([Net.IPEndPoint]$probe.LocalEndpoint).Port
    $probe.Stop()
} while ($hostPort -lt 49152 -or $hostPort -eq 5432)

$env:COMPOSE_PROJECT_NAME = $composeProject
$env:PRESCRIPTIVE_MAINTENANCE_POSTGRES_HOST_PORT = "$hostPort"

try {
    uv run --frozen poe services-up
    docker compose -p $composeProject exec -T postgres psql `
      --username prescriptive_maintenance `
      --dbname prescriptive_maintenance `
      --set ON_ERROR_STOP=1 `
      --command "SELECT ('[1,2,3]'::vector <-> '[3,2,1]'::vector) > 0;"
    uv run --frozen poe smoke --with-services
} finally {
    docker compose -p $composeProject down --volumes --remove-orphans
}
```

Ao final, confirme `git status --short` vazio, os recursos exclusivos ausentes e
as impressões digitais preexistentes inalteradas. Remova somente o diretório
temporário único depois de resolver novamente seu caminho e confirmar que ele
continua contido no diretório temporário do sistema.
