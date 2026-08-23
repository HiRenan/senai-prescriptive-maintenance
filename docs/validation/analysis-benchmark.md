# Benchmark local da análise prescritiva — SEN-65

## Objetivo entregue

O comando `scripts.analysis_benchmark` executa uma carga curta, sequencial,
inteiramente sintética e offline contra o `POST /analysis` real. A aplicação é
criada pela mesma `create_app()` usada pelo backend e recebe um
`IntegratedAnalysisService` completo por injeção explícita. O runtime padrão da
API continua usando os fakes do contrato e não descobre esse benchmark
automaticamente.

O cenário exercita as fronteiras implementadas na SEN-46:

1. contrato HTTP v1 e correlation ID;
2. `SimilarityCheckedModelPort`, incluindo a paridade exata entre decisão e
   índice sintético;
3. recuperação documental governada;
4. orquestração, currentness e guardrails;
5. provider fake ou falha controlada do provider;
6. projeção dos estados públicos e persistência transacional em memória.

Nenhum material original, arquivo de dados local, rede, AWS, credencial ou
chamada paga participa da composição.

## Cenários e aquecimento

Cada rodada contém uma execução de cada cenário, e a ordem do par é embaralhada
por um byte de SHA-256 derivado da versão da agenda, seed e ordinal da rodada.
Isso mantém a carga balanceada e independente da implementação de `random` do
runtime. O JSON registra versão, seed e os SHA-256 das agendas de aquecimento e
medição.

| Cenário | Provider | Resultado HTTP esperado |
| --- | --- | --- |
| `documented_fault` | fake determinístico retorna envelope válido | `documented_fault` |
| `provider_failure` | falha sintética antes de um envelope válido | `degraded` |

As rodadas de aquecimento usam o mesmo serviço da passagem temporizada, sempre
sem `tracemalloc`, e nunca entram nas distribuições, contagens medidas ou uso de
IA do relatório. Depois delas, a agenda medida é executada uma vez para
latência/erros/uso e repetida em runtime novo somente para memória.

## Fronteiras temporizadas

Os timers usam `time.perf_counter_ns` e são instalados como wrappers nas portas
injetadas. Portanto, a medição acontece dentro da chamada HTTP e não por uma
invocação paralela de fake fora da aplicação.

| Métrica | Escopo exato |
| --- | --- |
| `http_total` | `client.post()` local com bytes já preparados, incluindo contrato HTTP, integração, guardrails, persistência, serialização da resposta e serialização/I/O do logging operacional da aplicação |
| `model` | `SimilarityCheckedModelPort.predict`, incluindo consulta e paridade do índice sintético |
| `retrieval` | chamada da porta governada `retrieve` |
| `generation` | execução de `GenerationProvider.generate` dentro do slot limitado |

O tempo de geração não é o tempo total da orquestração: currentness, montagem de
contratos, thread limitada e validação pós-provider aparecem no total HTTP. Essa
fronteira evita atribuir ao provider o custo das demais camadas.

Cada wrapper captura o fim do timer antes de registrar em memória somente o
evento tipado necessário. Preparação do corpo HTTP, decodificação e validação da
resposta pelo harness, serialização JSON dos eventos de camada do benchmark e
I/O do sink desses eventos ficam fora dos timers. Isso não se aplica ao
middleware operacional: a serialização JSON e todos os handlers de
`prescriptive_maintenance.requests` rodam antes de `client.post()` retornar e
entram em `http_total`. Os eventos sanitizados do benchmark são emitidos em lote
somente depois da passagem exclusiva de memória; por isso, bloquear apenas esse
sink não altera nenhuma distribuição.

Para cada camada e cenário, o relatório registra tentativas, amostras válidas,
erros, taxa de erro, p50 e p95 em milissegundos. Essa é a visão principal no JSON
e no Markdown. Os percentis usam nearest-rank sobre tempos bem-sucedidos. Uma
falha de provider gera um evento com `status=error`, incrementa a taxa de erro e
produz `degraded`, mas seu tempo não entra em p50 ou p95 de geração. No cenário
de falha, esses percentis ficam explicitamente `null`.

O agregado opcional chama-se `synthetic_scenario_mix`, nunca `layers` de forma
genérica. Ele declara que mistura sucesso e falha deliberadamente: percentis
usam apenas tentativas bem-sucedidas, enquanto `error_rate` usa todas as
tentativas do mix.

## Memória e uso de IA

Depois de concluir aquecimento e passagem temporizada sem tracing, o harness cria
outros `IntegratedAnalysisService`, store, provider e aplicação. A mesma agenda
medida é então repetida sem wrappers de timer nem recorder de uso, iniciando e
encerrando `tracemalloc` ao redor de cada `client.post()`; o relatório conserva o
maior pico individual. Assim, nenhuma execução sob tracing contamina p50, p95,
erros ou contadores do provider.

Preparação da requisição, validação da resposta pelo harness, agregação e emissão
dos eventos de camada do benchmark ficam fora da janela. Serialização da resposta
e logging operacional da aplicação — inclusive serialização JSON e I/O dos
handlers reais — ficam dentro dela. É uma medição de alocações Python rastreadas,
em bytes. Ela **não mede RSS**, memória nativa de extensões, GPU, contêiner ou
consumo total do processo. Se o `tracemalloc` já estiver ativo, o benchmark falha
com erro tipado antes do aquecimento e não encerra nem reinicia o tracing do
chamador.

O provider fake devolve contadores fixos para validar o contrato. Eles são
rotulados como `simulated`, nunca como medidos ou estimados. Uma falha não possui
envelope de uso e fica `not_available`. Como não há provider faturável nem tabela
de preços, o custo também fica `not_available`; o benchmark não inventa uma
estimativa monetária.

## Rastreabilidade e conteúdo público

O resultado registra:

- commit, indicação de árvore limpa ou suja e SHA-256 canônico sanitizado do
  estado/bytes tracked e untracked relevantes;
- SHA-256 do `uv.lock`;
- runtime Python, sistema, arquitetura lógica e versões obrigatórias das
  dependências do caminho HTTP;
- seed, iterações, top-k, versão da agenda e identidades das duas agendas;
- IDs e versões sintéticos de dataset, contrato de features, modelo, índice,
  prompt, provider, recuperação, mapeamento, projeção e autorização.

O snapshot de commit, estado dirty, digest da árvore e hash do `uv.lock` é
capturado antes da composição e repetido depois da emissão dos eventos. O digest
inclui registros canônicos do index e do status, além de tipo e SHA-256 dos bytes
de cada caminho tracked alterado ou untracked; nomes participam somente da
entrada privada do hash e nunca são publicados. A captura rejeita de forma
tipada e sanitizada `assume-unchanged` e `skip-worktree`, porque essas flags do
índice podem ocultar bytes rastreados do status. Ela também reconsulta status e
index e falha fechada se eles mudarem durante a leitura. Qualquer diferença,
inclusive uma substituição dirty por outro estado dirty, impede a publicação de
proveniência ambígua. Falhas de filesystem, Git e lock usam mensagens
sanitizadas que não incluem caminho local.

Uma execução com `working_tree_dirty=true` é útil durante desenvolvimento, mas
não representa uma baseline final ligada somente ao commit informado. A
evidência comparável de entrega deve ser executada sobre árvore limpa.

Os eventos de camada do benchmark contêm apenas benchmark, correlation ID, fase,
cenário, camada, status e duração. O middleware HTTP mantém separadamente sua
allowlist existente de correlation ID, evento, método, rota e status. Requisição,
features, evidência, prompt, output, mensagem de exceção, caminho local e segredo
não são registrados. O relatório também não contém esses campos.

## Execução reproduzível

Da raiz, o JSON estável é escrito em stdout e os eventos JSON em stderr:

```powershell
uv run --frozen python -m scripts.analysis_benchmark
```

O mesmo resultado pode ser apresentado como relatório Markdown sanitizado:

```powershell
uv run --frozen python -m scripts.analysis_benchmark --format markdown
```

Parâmetros disponíveis:

```powershell
uv run --frozen python -m scripts.analysis_benchmark --help
```

Os padrões são duas iterações de aquecimento e dez medidas por cenário,
`seed=65` e `top_k=3`. O comando é curto e não representa teste de carga.

## Validação

Os testes funcionais comprovam os dois resultados HTTP, a exclusão do aquecimento,
a separação das camadas e cenários, a ausência de latência válida na falha de
provider, a passagem temporizada inteiramente sem tracing, o runtime novo da
passagem de memória, a invariância diante do sink do benchmark, a inclusão do
handler operacional real em `http_total` e memória, o tracing preexistente, a
agenda determinística, o payload privado defensivo, os bindings e versões
obrigatórios, a substituição dirty para dirty, os streams do CLI e as allowlists
completas de eventos.

```powershell
uv run --frozen pytest apps/api/tests/test_analysis_benchmark.py --no-cov -q
uv run --frozen poe check
```

## Limites

- o benchmark não mede concorrência, capacidade, throughput, SLO ou soak;
- o modelo, o índice, os documentos e o provider são sintéticos;
- os números não representam os artefatos locais avaliados na SEN-53;
- não há chamada Bedrock ou comparação de preço, região ou modelo de LLM;
- uma geração estruturalmente aceita não comprova correção semântica da
  prescrição nem autoriza manutenção industrial;
- resultados entre máquinas só são comparáveis junto com runtime, configuração,
  bindings e commit registrados.
