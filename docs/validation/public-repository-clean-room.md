# Auditoria clean-room do repositório público

- Data: 2026-08-24
- Escopo: SEN-71
- Branch avaliada: `develop`
- Baseline avaliada: `65b2729dd8b37d1922f1c6b6a3f6984e44f868c3`
- Resultado: aprovado, com as limitações explícitas neste relatório

## Conclusão

A baseline pública pôde ser obtida por HTTPS sem autenticação e validada sem
arquivos da worktree de implementação, serviços locais preexistentes,
credenciais, dados locais ou resolução de dependências fora dos locks. O clone
completo passou nos gates de qualidade, segurança, política, smoke, golden set,
navegador, Terraform offline e aplicações em Compose isolado.

Os oito materiais protegidos não foram lidos, movidos, copiados ou incluídos na
auditoria. As verificações compararam somente metadados, caminhos, fingerprints
e objetos Git já rastreados. A garantia de não reproduzir nomes ou hashes
protegidos vale para este relatório e para outras saídas públicas e versionadas
sanitizadas; logs operacionais globais, transitórios e não versionados não fazem
parte dessa garantia.

## Clone anônimo e isolamento

O clone definitivo `sen71-cleanroom-7c4e9a1d6b32` foi criado em 1,467 s, em um
diretório único sob o
temporário do sistema, fora desta worktree, a partir de
`https://github.com/HiRenan/senai-prescriptive-maintenance.git`, com checkout de
`develop`. O remote permaneceu exatamente nessa URL, `HEAD` e
`refs/remotes/origin/develop` apontaram para a baseline acima, o repositório não
era shallow. `git fsck --full --strict` havia passado no clone integral anterior
e não foi repetido nesta correção focada de clone e setup.

Antes do clone foram removidas do processo as variáveis de token e credenciais.
Também foram usados `GIT_TERMINAL_PROMPT=0`, `GIT_CONFIG_NOSYSTEM=1`,
`GIT_CONFIG_GLOBAL=NUL`, helper e `askpass` vazios e ausência de cabeçalho HTTP
adicional. A inspeção da configuração Git efetiva encontrou zero origem de
configuração do usuário e zero helper de credenciais.

Antes da criação foram testados o diretório da auditoria, o destino do clone e os
dez destinos isolados de cache, configuração e store: nenhum dos 12 existia.
Isso inclui os destinos de `pnpm_config_store_dir` e `npm_config_store_dir`.
`HOME` e `USERPROFILE` permaneceram com os valores nativos durante clone e setup.

Foram isolados `UV_CACHE_DIR`, `PRE_COMMIT_HOME`, `COREPACK_HOME`,
`npm_config_cache`, `PLAYWRIGHT_BROWSERS_PATH`, `GH_CONFIG_DIR`, `DOCKER_CONFIG`,
`PNPM_HOME`, `pnpm_config_store_dir` e `npm_config_store_dir`. O pnpm materializou
o store no destino novo de `npm_config_store_dir`, com zero pacote reutilizado e
quatro baixados; o destino também novo de `pnpm_config_store_dir` permaneceu sem
uso. Não houve consulta ao store do usuário.

A primeira tentativa de provar o setup, inclusive sua repetição aquecida, foi
descartada e não sustenta os resultados abaixo. O segundo clone executou o setup
frio aceito. As suítes pesadas já concluídas não foram repetidas.

| Lock rastreado | SHA-256 antes do setup frio | Resultado depois |
| --- | --- | --- |
| `uv.lock` | `037dd5fd8abb9149d5a4ff364fe68f48434e1691d0315056f0e46b14b8879630` | idêntico |
| `pnpm-lock.yaml` | `32f038a89fd6c23993cd07388e1746ae3e42af97ed48d90f1facea84cfd17a84` | idêntico |
| `infra/aws/demo/.terraform.lock.hcl` | `fc8fd36a17fe95a7311e30019837d5b32a36681adc90b093c50ee4377a405ea0` | idêntico |

`git status --porcelain` permaneceu vazio no segundo clone antes e depois dos
dois comandos.

## Ambiente observado

| Ferramenta | Versão |
| --- | --- |
| Sistema | Windows |
| Git | 2.50.1.windows.1 |
| Python | 3.13.5 |
| uv | 0.9.4 |
| Node.js | 22.18.0 |
| Corepack | 0.33.0 |
| pnpm | 10.15.1 |
| Gitleaks | 8.30.1 |
| Terraform | 1.15.9 windows_amd64 |
| Docker Engine | 28.4.0 |
| Docker Compose | 2.39.2-desktop.1 |

## Gates executados no clone

Os tempos são aproximados e correspondem à execução real nesse ambiente.

| Comando | Resultado observado | Duração |
| --- | --- | ---: |
| `uv lock --check` | lock válido; 84 pacotes resolvidos | 0,2 s |
| `uv run --frozen poe setup` | segundo clone frio: 82 pacotes Python instalados, quatro pacotes pnpm baixados sem reutilização e hooks instalados | 53,6 s |
| `uv run --frozen poe public-repository-audit` | 717 blobs e 59 árvores examinados; 289 caminhos rastreados; histórico completo e zero material protegido | 3,7 s |
| `uv run --frozen poe failure-matrix` | 76 testes aprovados e 1.239 fora do marcador | 52,2 s |
| `gitleaks git . --redact` | 59 commits auditados; zero vazamento | 0,8 s |
| `gitleaks git . --pre-commit --redact` | índice aprovado; zero vazamento | 0,5 s |
| `uv run --frozen poe check` | format-check e Ruff aprovados; Pyright com zero erro; 1.272 testes Python aprovados e 43 ignorados; 150 testes Node aprovados | 126,8 s |
| `uv run --frozen poe hooks` | 11 hooks aprovados, inclusive o gate de segredos aplicável | 5,1 s |
| `node --test .github/scripts/pull-request-policy.test.js` | 31 testes de política aprovados em 3 suítes | 44,0 s |
| `uv run --frozen poe smoke` | runtimes, configuração, liveness e readiness offline aprovados | 7,1 s |
| `uv run --frozen poe golden-e2e` | cinco resultados, duas jornadas documentais e três probes de segurança aprovados com dados sintéticos | 3,5 s |
| `corepack pnpm exec playwright install chromium` | Chromium instalado no diretório isolado | 21,5 s |
| `uv run --frozen poe web-browser-test` | 14 testes Chromium aprovados | 10,5 s |

## Terraform offline

Foi usado exclusivamente o executável 1.15.9 instalado em
`$env:LOCALAPPDATA/Programs/HashiCorp/Terraform/1.15.9/terraform.exe`. O JSON do
plano ficou fora do clone, no diretório temporário da auditoria. O plano offline
usou valores sintéticos, sem credenciais reais ou do usuário, chamada live,
backend remoto ou `apply`.

| Comando ou script existente | Resultado observado | Duração |
| --- | --- | ---: |
| `terraform -chdir=infra/aws/demo fmt -check -recursive` | formatação aprovada | 0,3 s |
| `static_plan.py --terraform <terraform-1.15.9> --plan-json <temporário>` | init, validate e plano offline aprovados; casos inválidos rejeitados | 25,5 s |
| `plan_audit.py <temporário>` | allowlists e contrato do plano aprovados | 0,2 s |
| `security_regression.py <temporário>` | baseline e regressões de segurança aprovadas | 0,5 s |
| `delivery_policy.py` | política de entrega aprovada | 0,2 s |
| `delivery_regression.py` | 442 casos adversariais aprovados | 1,3 s |
| `frontend_delivery_regression.py` | regressão de entrega do frontend aprovada | 0,6 s |

Todos os scripts Python foram chamados com
`uv run --frozen python infra/aws/demo/scripts/<script>`; `static_plan.py` foi a
única interface usada para criar o plano offline.

## Docker e aplicações

Como as duas tags canônicas já existiam no daemon, elas não foram sobrescritas.
Um override temporário fora do clone alterou somente `services.api.image` e
`services.web.image` para tags derivadas do projeto Compose exclusivo
`sen71-cleanroom-f0957eb57ac8`. `COMPOSE_FILE` apontou primeiro para o
`compose.yaml` rastreado e depois para esse override. Três portas altas livres de
loopback foram escolhidas dinamicamente, sem registrar seus números.

| Etapa | Resultado observado | Duração |
| --- | --- | ---: |
| `uv run --frozen poe applications-audit` | composição canônica, contextos e builders aprovados | 21,5 s |
| `uv run --frozen poe services-up` | PostgreSQL saudável no projeto isolado | 7,2 s |
| `uv run --frozen poe smoke --with-services` | PostgreSQL e pgvector aprovados | 7,6 s |
| `uv run --frozen poe applications-build` | API e web construídas sob tags exclusivas | 3,5 s |
| `uv run --frozen poe applications-up` | PostgreSQL, API e web saudáveis | 21,7 s |
| `uv run --frozen poe smoke --with-services --with-applications` | banco, pgvector, API, web e OpenAPI aprovados | 7,7 s |
| limpeza em `finally` | projeto, órfãos, volumes e duas imagens exclusivas removidos | 3,3 s |

Os IDs das duas tags canônicas preexistentes e dos 60 containers alheios ficaram
idênticos antes e depois. A prova final encontrou zero container, rede, volume,
imagem ou listener do projeto e zero tag exclusiva. O override foi removido com
validação e nenhum comando `prune` foi usado.

## Auditorias complementares

- A ancestralidade completa de `origin/develop` continha 57 commits. Foram
  verificados 289 caminhos rastreados, sem material protegido, `.env` indevido,
  dump, cache, artefato gerado ou licença inesperada.
- O Gitleaks cobriu o histórico e o gate de pre-commit. Nenhum segredo foi
  encontrado.
- Os 289 arquivos de texto decodificaram como UTF-8 e terminaram com newline.
  `git ls-files --eol` confirmou zero CRLF ou conteúdo misto no índice. Dez
  conversões vistas apenas no checkout Windows não existem nos blobs versionados.
- Foram verificados 214 links Markdown relativos e 25 tarefas Poe documentadas;
  não houve link ausente nem comando desconhecido.
- O histórico entregue por `origin/develop` tem um único autor, Renan Mocelin.
  O segundo committer observado é a infraestrutura legítima do GitHub. Não há
  trailer de coautoria nem atribuição de autoria a IA.

## Correspondência com o README

Os quickstarts e comandos Poe documentados têm interfaces existentes. Os smokes
provaram o pacote, a API, a aplicação web, o PostgreSQL e o pgvector; o golden set
provou somente jornadas sintéticas; e a validação AWS permaneceu estritamente
offline. A limitação genérica de autenticação e autorização era contraditória
com o perfil AWS demo já integrado com Cognito Code+PKCE e JWT. A frase foi
restringida ao que realmente falta: autenticação e autorização fora desse perfil.

## Como reproduzir

1. Crie um diretório vazio e único sob o temporário do sistema. Sem alterar
   `HOME` ou `USERPROFILE`, remova tokens do processo e configure as travas Git e
   os dez diretórios específicos de cache, configuração e store descritos acima.
2. Execute o clone anônimo com:

   ```powershell
   git -c credential.helper= -c core.askPass= -c http.extraHeader= clone `
     --branch develop `
     https://github.com/HiRenan/senai-prescriptive-maintenance.git <destino>
   ```

3. Confirme remote, `refs/remotes/origin/develop`, baseline, ausência de shallow,
   `git fsck --full --strict`, locks e estado limpo. Execute, na ordem, os comandos
   da tabela de gates.
4. Defina o Terraform por `$env:LOCALAPPDATA` e execute os sete comandos da tabela
   offline, mantendo o plano JSON fora do clone.
5. Se Docker estiver disponível, use projeto, tags e portas exclusivos. Quando
   houver tags canônicas preexistentes, aplique o override mínimo fora do clone,
   rode as tarefas existentes e faça a limpeza em `finally`, sem `prune`.
6. Compare locks e arquivos rastreados antes e depois e remova o diretório
   temporário somente após registrar resultados sanitizados.

## Limites e limpeza

A prova foi executada em um host Windows e no estado público disponível na data
acima. Terraform foi validado apenas offline; não há evidência de provisionamento
AWS live. O navegador coberto foi Chromium. O daemon Docker era compartilhado e
o build operacional pôde reutilizar camadas imutáveis já presentes; o isolamento
por projeto e tags, a auditoria canônica dos builders e as comparações antes e
depois impedem atribuir a esta execução qualquer recurso preexistente.

A auditoria prova presença, ausência, integridade e execução das interfaces
públicas; ela não avalia semanticamente materiais originais ausentes nem qualidade
de modelo com dados privados. Ao final, recursos Docker exclusivos, override,
plano, clone, caches e diretório temporário da auditoria foram removidos. Esta
worktree e serviços alheios não foram usados como dependência nem alterados.
