# Roteiro final da demonstração — SEN-70

- Responsável: Renan Mocelin
- Status: roteiro pré-release reconciliado com as entregas finais da SEN-64 e da
  SEN-75; aceite temporal técnico aprovado
- Baseline confrontado: `origin/develop` em
  `039c0f83819f4cc673e45a960c409a7e31d0f48b`
- Janela principal: 15 minutos, composta por 14 minutos e 30 segundos de
  conteúdo e 30 segundos de reserva; o ensaio técnico integral mediu 06:53,535
  sem cortes

> O baseline indicado contém o painel local responsivo da SEN-64, cinco outcomes
> visuais por fixtures sintéticas no modo offline, zero chamadas à API nesse
> modo e regressões de resiliência e acessibilidade. A gestão documental aparece
> explicitamente indisponível offline: o ciclo e o efeito de aprovação são
> demonstrados pelo harness HTTP/CLI e pela prova RAG sintética, não simulados na
> tela. A SEN-75 acrescentou o perfil publicado fechado, configuração pública de
> runtime sem segredos, login Cognito por Authorization Code + PKCE S256, sessão
> em memória, transporte JWT allowlisted e regressões de publicação e navegador.
> Essas capacidades foram validadas com serviços controlados locais; não houve
> deploy, login humano ou smoke AWS live. Para a SEN-70, AWS permanece arquitetura
> e validação offline no estado não implantado, com custo estimado. A SEN-74 é uma
> execução opcional e autorizada estritamente pós-release, fora do aceite e do
> ensaio.

## Premissas e riscos do recorte

- As dependências já foram instaladas pelos locks antes da apresentação. O
  primeiro setup pode baixar pacotes e não pertence à contingência offline.
- Todos os comandos partem da raiz e usam somente fixtures sintéticas. O
  painel offline e o harness não configuram chamadas externas; a demo não abre
  materiais originais nem exige AWS, credenciais ou provider pago.
- O comando principal percorre a factory FastAPI real com uma composição
  explicitamente injetada e in-process, persistência em memória e fakes
  determinísticos. Ele prova contratos, decisões e guardrails; não prova
  qualidade preditiva ou semântica em operação real. As flags `limits` do
  relatório são declarativas, não telemetria de rede. [A02] [A04]
- Os números do dataset e do modelo são medições históricas identificadas nos
  respectivos cards, não medições refeitas pela apresentação. [A10] [A11]
- A UI offline aceita somente os cinco exemplos sintéticos gerados do OpenAPI e
  recusa entrada modificada. O teste Chromium observa zero chamadas a
  `/api/analysis` e às rotas documentais; isso é evidência de navegador, não uma
  inferência da flag declarativa do relatório golden. [A15]
- O ciclo documental demonstrado no golden registra metadados, conclui os gates
  sintéticos e compara aprovação com rejeição. A prova RAG dinâmica cria um JSON
  de extração novo em memória e vincula a citação ao chunk aprovado realmente
  ranqueado. Nenhuma das duas provas usa documento original. [A07] [A20]
- O limite de conteúdo é 14:30. O ensaio técnico cronometrado do artefato mediu
  06:53,535 ao somar sequencialmente fala sintetizada deliberadamente lenta e a
  regressão Chromium com setup, sem aplicar cortes. Restaram 07:36,465 dentro do
  conteúdo, além da reserva de 00:30. Isso não afirma que Renan falou nem mede
  perguntas da banca; um ensaio pessoal continua recomendado. [A24]
- A SEN-64 está integrada em
  `3e83dd8965668b126c4e86d9d2f26d47ed5f443a` e a SEN-75 em
  `039c0f83819f4cc673e45a960c409a7e31d0f48b`. Os dois gates de conteúdo estão
  concluídos. A ordem confirmada é SEN-64 + SEN-75 → SEN-70 → SEN-71 → SEN-72 →
  SEN-74. A SEN-77 fornece evidência sintética reutilizável, mas não é gate. Uma
  futura SEN-74 pode gerar um adendo pós-release, sem condicionar ou reabrir o
  aceite.

## Vocabulário obrigatório durante a apresentação

Use os rótulos públicos de forma literal:

- **implementado**: comportamento presente em código e coberto por teste;
- **medido**: resultado de uma execução identificada, com ambiente e limites;
- **estimado**: cálculo por hipóteses explícitas, não gasto observado;
- **futuro** ou **bloqueado**: capacidade ausente no baseline.

Frases seguras para este recorte:

- “O painel local demonstra cinco outcomes offline com fixtures do contrato e o
  teste de navegador observa zero chamadas à API nesse modo.”
- “O harness offline exercita os contratos HTTP com dados e providers
  sintéticos por composição in-process.”
- “A aprovação torna uma versão documental elegível para o fluxo sintético; o
  registro HTTP contém metadados, não bytes.”
- “A abstenção e a recusa evitam prescrição quando não existe base segura.”
- “O resultado apoia revisão humana; não autoriza manutenção.”
- “O perfil publicado implementa Authorization Code com PKCE S256, mantém os
  tokens em memória e limita o `Bearer` à API configurada; a prova executada usa
  origens e respostas sintéticas controladas.”
- “A origem HTTPS está fixada no contrato de publicação, mas isso não comprova
  que ela esteja implantada ou acessível na AWS.”
- “O ensaio técnico do artefato mediu 06:53,535 sem cortes; a voz foi sintética
  e não representa uma fala executada por Renan.”

Não confundir implementação pré-release com evidência live:

- “A gestão documental funciona offline.” — o painel informa justamente que ela
  está indisponível nesse modo.
- “O retry foi provado sem API.” — a regressão de retry usa respostas HTTP
  controladas no navegador; o modo offline não chama a API.
- “A citação comprova semanticamente toda a recomendação.”
- “O login Cognito foi realizado por uma pessoa na AWS.” — o fluxo foi exercitado
  no Chromium contra endpoints sintéticos controlados.

Não usar na apresentação pré-release da SEN-70:

- “Existe uma URL CloudFront live.”
- “O login ou o smoke foi executado no ambiente live.”
- “O ambiente AWS foi implantado.”
- “O custo foi observado.”
- “O teardown foi executado.”

Essas afirmações só poderiam aparecer em um adendo de evidência posterior à
release e a uma SEN-74 autorizada; elas não são lacunas do aceite da SEN-70.

## Roteiro principal de 15 minutos

As janelas abaixo distribuem 14:30 de conteúdo e preservam 00:30 de reserva até
o limite de 15:00. O ensaio técnico descrito adiante aprovou o artefato sem
cortes. Prepare o recorte determinístico das jornadas e deixe sua saída aberta
antes da apresentação; o ensaio pessoal permanece uma recomendação operacional.

| Janela | Ação e fala-base | Evidência | Corte permitido |
| --- | --- | --- | --- |
| 00:00–00:45 | Abra pelo problema: medições isoladas não bastam; a plataforma organiza condição, casos similares e evidência documental, mas mantém a decisão com uma pessoa. | [A01] | Não cortar o limite humano. |
| 00:45–01:35 | Mostre o diagrama lógico. Explique o monólito modular FastAPI e a composição explícita de modelo, índice, recuperação, guardrails e persistência. Diferencie `synthetic_demo` da composição `artifacts`, que exige manifesto e SHA-256 autorizados. | [A02] [A19] | Em rota curta, resuma em uma frase. |
| 01:35–02:40 | Apresente o objetivo correto: recuperação de históricos semelhantes e prescrição documental governada, não um classificador “com 97% de acurácia”. Mostre 18 features, split temporal, cobertura de 39,7408%, abstenção e decisão de não aprovar automação. Resuma lineage, hashes, artefato não executável, avaliação temporal, model card e promoção manual por manifesto. | [A10] [A11] [A19] | Preserve objetivo e decisão; IDs e hashes podem ser cortados. |
| 02:40–03:20 | Explique o RAG: só uma falha com mapping e evidência aprovada vigente pode chegar ao provider; schema, citações e vigência são revalidados. Ressalve que conformidade estrutural não prova groundedness semântico. | [A12] [A20] | Não cortar a ressalva semântica. |
| 03:20–04:10 | Explique o login pré-release sem abrir a origem configurada: configuração pública fechada e sem segredos; Authorization Code com PKCE S256; material de redirecionamento de uso único; tokens somente em memória; `Bearer` limitado à API allowlisted; expiração, `401` ou `403` limpam a sessão sem repetir a operação. Diga que o Chromium usa serviços sintéticos controlados, não Cognito ou AWS live. | [A22] | Preserve protocolo, limite da prova e ausência de login humano. |
| 04:10–04:50 | Abra `/?mode=offline#analysis`, confirme o indicador offline e cite os cinco exemplos do seletor. Diga que o teste Chromium observa zero chamadas às rotas de análise e documentos e que uma entrada alterada é recusada. | [A15] | Mostre somente os três exemplos usados nas jornadas. |
| 04:50–06:05 | **Jornada 1 — normal:** execute “Condição normal” na UI e mostre diagnóstico, vizinhos e ausência de prescrição. Na projeção golden já aberta, confirme `normal`, round-trip e zero chamadas a recuperação, geração e provider. | [A05] [A15] | Não cortar a ausência de prescrição. |
| 06:05–08:05 | **Jornada 2 — prescrição governada:** execute “Falha documentada” e mostre prescrição e citação sintéticas. No golden, confirme uma chamada por camada e `approved_citation_match: true`; use a prova RAG dinâmica para explicar que o chunk novo aprovado chega à citação sem resposta pronta. | [A06] [A20] | Em rota curta, omita IDs opacos e mantenha aprovação e citação. |
| 08:05–09:50 | **Jornada 3 — revisão humana e falha segura:** compare `received` → `approved` com `rejected`, mostre que a decisão humana altera a elegibilidade e não autoriza manutenção. Em seguida, execute OOD e diferencie a abstenção das recusas `no_evidence`, `stale_evidence` e `invalid_provider_output`. | [A07] [A08] [A09] [A15] | Preserve revisão humana, OOD e pelo menos uma recusa antes do provider. |
| 09:50–10:50 | Explique resiliência e acessibilidade: último laudo preservado durante loading/erro, retry explícito, resposta fora de ordem descartada, teclado/foco, alvos de 44 px, reflow até o equivalente a 400% e reduced motion. Esclareça que a gestão documental não é simulada offline. | [A15] | Pode resumir os viewports; não omita o limite documental. |
| 10:50–11:40 | Apresente AWS somente como arquitetura validada offline, não implantada; USD 2,72 para oito horas é estimativa, sem gasto observado. Explique o gate de operador único: `workflow_dispatch` e, depois, `Approve and deploy`, sem revisão independente. | [A16] [A21] [A23] | Corte o custo antes do estado não implantado e do limite da prova. |
| 11:40–12:50 | Reforce limites: fakes não equivalem a artefatos reais; `support_score` não é probabilidade; citações válidas não garantem correção semântica; não há SageMaker, registry, retreino automático, drift online ou autorização automática de manutenção. | [A01] [A11] [A12] [A19] | Não cortar. |
| 12:50–14:30 | Feche pelo valor: a entrega demonstrável é similaridade, evidência governada e falha segura. Convide a banca a auditar as afirmações pelos IDs da matriz e diferencie testes executados de campos mostrados nos relatórios. | [A03]–[A24] | Não cortar a mensagem de falha segura. |
| 14:30–15:00 | Reserva para troca de tela ou uma interrupção curta; não foi necessária no ensaio técnico. | — | Se execução ou rolagem consumirem a reserva na apresentação, aplique os pontos de corte antes desta janela. |

### Pontos de corte

Se houver somente 12 minutos:

1. resuma arquitetura em 30 segundos;
2. mantenha objetivo de similaridade, cobertura e decisão de não aprovação;
3. omita o valor estimado da AWS;
4. reduza a matriz de falhas a uma frase.

Se houver somente 10 minutos, preserve nesta ordem:

1. problema, decisão humana e limite do baseline;
2. execução de `golden-e2e`;
3. jornada normal;
4. falha documentada com aprovado versus rejeitado;
5. OOD e as três recusas;
6. limite documental offline, login PKCE validado com serviços controlados e
   ausência de AWS live, seguida da conclusão.

Nunca corte as três jornadas, a distinção entre implementação e medição, nem a
declaração de que gestão documental offline, login humano, qualidade semântica e
AWS live não estão comprovados.

## Operação visual local e offline

Com dependências já instaladas, inicie somente o servidor web em um terminal:

```powershell
corepack pnpm --filter @senai-prescriptive-maintenance/web start
```

Abra `http://127.0.0.1:3000/?mode=offline#analysis`. O modo offline não requer
API em execução. Sem editar código ou fixture:

1. escolha `normal`, execute e confira “Condição normal”, diagnóstico, vizinhos,
   origem “Fixture sintética offline” e ausência de prescrição;
2. escolha `documented_fault`, execute e confira “Falha documentada”, prescrição,
   prioridade, ações e citação sintética;
3. escolha `out_of_distribution`, execute e confira “Fora da distribuição”, o
   motivo da abstenção e a ausência de prescrição;
4. se houver tempo, percorra também `undocumented_fault` e `degraded` para fechar
   os cinco outcomes do contrato.

Navegue por teclado até “Documentos”. A mensagem esperada é “Gestão documental
indisponível offline”; nenhuma lista, aprovação ou rejeição é simulada. O teste
Chromium `offline demonstra cinco outcomes e toda a navegação sem chamar a API`
observa as rotas de análise e documentos e exige a lista de requisições vazia.
As regressões seguintes, com respostas HTTP controladas, provam foco, retry,
preservação do último resultado durante loading/erro, descarte de resposta fora
de ordem e reflow. Não atribua essas provas às fixtures offline. [A15]

## Login PKCE e caminho autenticado pré-release

Não abra `https://senai.maib.com.br` como se fosse uma URL implantada. Ela é a
origem exata configurada no contrato de publicação; a prova reproduzível da
SEN-70 intercepta essa origem no Chromium e serve os módulos estáticos, o
runtime config, o Hosted UI e a API com respostas sintéticas controladas. [A22]
[A23]

O fluxo implementado e testado é:

1. somente a origem publicada exata carrega `runtime-config.v1.json`; loopback,
   host local, LAN e `?mode=offline` preservam os perfis locais sem login;
2. o runtime config aceita apenas schema, API regional `us-east-1`, client
   público, Hosted UI allowlisted, escopo `openid` e callback/logout exatos; uma
   configuração ausente, tardia ou inválida mantém análise e documentos inertes;
3. “Entrar com Cognito” gera `state`, verifier e challenge S256. Verifier, state e
   timestamp ficam em uma única entrada de `sessionStorage` somente durante o
   redirecionamento, expiram em dez minutos e são consumidos antes da troca do
   código;
4. o callback é limpo da barra de endereço antes da troca. Access token e refresh
   token ficam somente na memória da página; não há renovação automática;
5. o `Bearer` segue apenas para a origem, caminho e método allowlisted, com
   `credentials: omit`, `cache: no-store` e redirects recusados. Expiração,
   `401` ou `403` limpa a sessão e exige novo login sem repetir a operação;
6. logout limpa a memória, tenta revogar o refresh token e conclui no endpoint de
   logout mesmo se a revogação falhar.

Execute a prova controlada com:

```powershell
uv run --frozen poe web-browser-test
```

O cenário `callback publicado libera uma analise exata antes dos documentos`
percorre o request de autorização, valida challenge/verifier, troca o código,
confere ausência do material PKCE no storage, envia uma única análise com
`Bearer` e recusa a reutilização do callback. Outros cenários fecham duplo clique,
runtime config indisponível, expiração e falhas de autenticação. Isso comprova o
frontend preparado para publicação, não login humano, disponibilidade da origem,
JWT aceito pela AWS ou smoke remoto. [A22]

## Operação das três jornadas no harness HTTP/CLI

O comando canônico, sem edição de fixture ou código, é:

```powershell
uv run --frozen poe golden-e2e
```

Na preparação da projeção, use o recorte abaixo no lugar de uma execução bruta;
não é necessário executar os dois comandos.

### Recorte determinístico para projeção

Para evitar rolagem improvisada, gere na preparação a visão abaixo e deixe a
saída aberta. O comando não grava arquivo nem cria script auxiliar; ele mantém
somente configuração, declarações de limite e os campos usados nas três
jornadas. Se a saída for preparada antes da banca, identifique-a como execução
pré-demo, não como execução feita naquele momento.

```powershell
Get-Command uv -ErrorAction Stop | Out-Null
$goldenJson = uv run --frozen poe golden-e2e 2>$null
$goldenExitCode = $LASTEXITCODE
if ($goldenExitCode -ne 0) {
  throw "golden-e2e failed with exit code $goldenExitCode. Rerun without stderr redirection for diagnostics."
}
$goldenReport = $goldenJson | ConvertFrom-Json
$projectionView = [ordered]@{
  configuration = [ordered]@{
    environment = $goldenReport.configuration.environment
    persistence_backend = $goldenReport.configuration.persistence_backend
    provider_mode = $goldenReport.configuration.provider_mode
  }
  declared_limits = $goldenReport.limits
  analysis_journeys = @(
    $goldenReport.analysis_journeys |
      Select-Object id, observed_outcome, round_trip, layer_calls,
        approved_citation_match
  )
  document_journeys = @(
    $goldenReport.document_journeys |
      Select-Object decision, registered_status, result_status
  )
  safety_probes = @(
    $goldenReport.safety_probes |
      Select-Object id, status, refusal, provider_calls
  )
}
$projectionView | ConvertTo-Json -Depth 6
```

O redirecionamento vale somente para a preparação da projeção e suprime da tela
o banner do Poe e os logs HTTP enviados a stderr. O exit code é verificado antes
da conversão: qualquer falha interrompe o recorte e deve ser diagnosticada com o
comando canônico, sem redirecionamento. Não reutilize JSON anterior ou parcial.

`declared_limits.network_calls: false` descreve a configuração declarada pelo
harness; não é instrumentação nem observação independente de tráfego. A prova
executável relevante é a composição FastAPI/TestClient in-process com memória e
fakes, sem endpoint ou provider externo configurado. [A04]

Para a prova dinâmica de lineage documental, use separadamente:

```powershell
uv run --frozen pytest apps/api/tests/test_dynamic_rag_e2e.py -q --no-cov
```

O teste cria evento e extrações inteiramente sintéticos em memória, percorre
chunking, indexação, sete estados documentais, aprovação vigente, ranking,
guardrails e persistência, e confere a identidade exata da citação. Ele não é
necessário para navegar a UI e a SEN-77 não é gate da SEN-70; serve como prova
reproduzível para explicar o efeito da aprovação sem acessar material original.
[A20]

### 1. Jornada normal

Na seção `analysis_journeys`, confirme:

- `id` e `observed_outcome` iguais a `normal`;
- `round_trip: true` para o `POST` seguido do `GET`;
- `model: 1`, `retrieval: 0`, `generation: 0` e `provider: 0`.

Leitura sustentada: o modelo participa, mas uma condição normal não inicia
recuperação documental nem geração. Não interpretar isso como decisão
industrial validada. [A05] [A11]

### 2. Jornada de prescrição governada

Em `analysis_journeys`, confirme para `documented_fault`:

- `observed_outcome: documented_fault` e `round_trip: true`;
- uma chamada a modelo, recuperação, geração e provider;
- `approved_citation_match: true`.

Leitura sustentada: a prescrição sintética só é formada depois de mapping,
recuperação de evidência aprovada, provider e validação da citação. A identidade
do chunk citado é verificada; isso não prova correção semântica da redação. [A06]
[A12] [A20]

### 3. Jornada de revisão humana e falha segura

Na seção `document_journeys`, compare uma identidade que passa de registro
`received` ao resultado `approved` com outra que termina `rejected`. Depois, em
`safety_probes`, mostre que `rejected_evidence` termina com `stale_evidence` e
`provider_calls: 0`. A decisão humana muda a elegibilidade documental; ela não
autoriza manutenção e não é simulada pela tela offline.

Não afirmar upload, inspeção de bytes, OCR pela API ou aprovação offline pela
tela: o contrato HTTP registra somente metadados e o painel informa que a gestão
documental está indisponível nesse modo. [A07] [A09] [A15]

Na seção `analysis_journeys`, confirme para `out_of_distribution`:

- uma chamada ao modelo;
- zero chamadas a recuperação, geração e provider;
- round-trip HTTP concluído.

Mostre então os três probes separadamente:

| Probe | Resultado | Chamadas ao provider | Interpretação permitida |
| --- | --- | ---: | --- |
| `missing_evidence` | `no_evidence` | 0 | Não existe base documental elegível. |
| `rejected_evidence` | `stale_evidence` | 0 | Evidência rejeitada/obsoleta não cruza o gate. |
| `invented_citation` | `invalid_provider_output` | 1 | Uma resposta estruturalmente inválida é descartada depois da chamada. |

OOD é abstenção do modelo; os probes são recusas dos guardrails. Eles têm a
mesma consequência segura — não publicar prescrição sem base — por razões
distintas e auditáveis. A jornada termina em revisão humana, nunca em comando de
manutenção. [A01] [A08] [A09]

## MLOps pragmático e objetivo do modelo

O objetivo demonstrável não é classificar manutenção com “97% de acurácia”. O
k-NN v3 recupera históricos semelhantes e forma uma condição candidata; a
prescrição só existe para falha problemática com mapping, evidência documental
aprovada vigente e guardrails satisfeitos. O número de 97,3756% é uma leitura
binária pós-hoc da candidata **antes** da abstenção, dominada pela classe
problema e inferior à baseline constante no mesmo recorte. A cobertura final é
39,7408%. Portanto, esse número não descreve a decisão emitida nem aprova o
modelo como classificador. [A11]

A disciplina atual é deliberadamente pequena e auditável:

| Etapa | Controle existente | Leitura permitida |
| --- | --- | --- |
| Lineage de dados | manifesto, SHA-256 pre/post, `dataset_id`, contrato, política, inventário e lock vinculados | Prova identidade e transformação reproduzível; não prova correção de domínio. |
| Treino | k-NN determinístico, 18 features, `StandardScaler` ajustado só no treino e limiares congelados com validação | A baseline é reproduzível e fixa; o holdout não participa do fit ou do tuning. |
| Artefato | schema/model 3, manifesto JSON e arrays NumPy carregados com `allow_pickle=False`, conjunto fechado e hashes verificados | É um artefato local versionado por schema e não executável; seus bytes por registro permanecem ignorados e fora do Git. |
| Avaliação | split temporal, protocolo materializado, métricas agregadas e abstenção por distância/voto | A avaliação expõe mudança temporal e limites; não é aprovação operacional independente. |
| Registro | data card, model card v3 e relatório de avaliação v2 com IDs, hashes, contagens e decisão de uso | A decisão pública é demonstração com revisão humana, não automação. |
| Promoção | modo `artifacts` sem descoberta nem fallback, autorizado manualmente por caminho local explícito e SHA-256 do manifesto | Somente os bytes aprovados podem compor o runtime; isso não equivale a model registry. |

Não há SageMaker, model registry, retreino automático, monitoramento de drift
online ou promoção automática. Mudança de fonte, contrato, política, artefato ou
lock exige novo build, nova identidade, nova avaliação e autorização manual. O
modelo não é aprovado para automação ou decisão industrial. [A10] [A11] [A19]

## Comandos offline existentes

Todos partem da raiz. O setup deve ter sido concluído antes; `--frozen` preserva
o lock, mas não transforma a primeira instalação em uma operação sem download.

| Comando | Uso na preparação ou recuperação | O que comprova | Limite |
| --- | --- | --- | --- |
| `uv run --frozen poe check` | Gate agregado antes da demo. | Formatação somente leitura, lint, tipagem, suíte Pytest, testes do painel, build do bundle e regressão local do frontend AWS. | Não inclui o teste Chromium nem prova AWS live ou qualidade semântica. |
| `uv run --frozen poe smoke` | Diagnóstico do ambiente antes de culpar a jornada. | Runtimes, configuração, Compose estático e health HTTP offline em loopback; não inicia serviços. | Não percorre as jornadas de produto. |
| `uv run --frozen poe web-browser-test` | Gate visual e autenticado controlado da SEN-64/SEN-75. | Cinco outcomes offline, zero API, retry, último resultado, acessibilidade e o fluxo PKCE publicado contra serviços sintéticos. | Não realiza login humano, smoke remoto ou deploy. |
| `uv run --frozen poe aws-demo-frontend-regression` | Gate offline de publicação da SEN-75. | Gramática fechada do bundle construído, runtime config, headers, publicação/invalidação e smoke do frontend com fakes locais. | Exige `poe web-build` antes; não consulta S3, CloudFront, Cognito ou API Gateway reais. |
| `uv run --frozen python infra/aws/demo/scripts/delivery_regression.py` | Gate adversarial da entrega AWS. | Policies, workflows, plano/state, smoke e inventário com entradas sintéticas e subprocessos locais. | Não prova OIDC, IAM, backend, billing ou teardown live. |
| `uv run --frozen poe golden-e2e` | Prova HTTP/CLI das jornadas e do ciclo documental. | Cinco estados, aprovação/rejeição, recusas, bindings e contagens por camada com fakes sintéticos. | Não substitui a prova visual e não mede rede. |
| `uv run --frozen pytest apps/api/tests/test_dynamic_rag_e2e.py -q --no-cov` | Prova dinâmica para citação e aprovação. | JSON novo, chunking, índice, ciclo de sete estados, ranking e lineage da citação com memória/fakes. | Não prova qualidade semântica, provider real ou documento original. |
| `uv run --frozen poe failure-matrix` | Reserva para perguntas de segurança ou regressão. | Seleção P0/P1 e auditoria Git sanitizada. | Não substitui `check` e não demonstra interface. |
| `uv run --frozen poe public-repository-audit` | Auditoria pública direcionada. | Nomes e fingerprints protegidos somente nos objetos Git publicáveis. | Não lê materiais locais ignorados nem substitui revisão do diff. |
| `uv run --frozen python scripts/generate_openapi.py --check` | Reserva para pergunta sobre contrato. | Igualdade byte a byte entre OpenAPI gerado e snapshot v1. | Não gera cliente nem valida experiência visual. |

### Checkpoint executado nesta worktree

Em 24/08/2026, sobre o baseline
`039c0f83819f4cc673e45a960c409a7e31d0f48b`, foram obtidos estes resultados
finais reais:

| Verificação | Resultado |
| --- | --- |
| `web-test` | 150 testes web aprovados. |
| `web-browser-test` | 14/14 testes Chromium aprovados, incluindo PKCE e offline. |
| `aws-demo-frontend-regression` | staging, allowlist, MIME, runtime config, CORS e POST autenticado aprovados somente com fakes locais. |
| `delivery_regression.py` | 442 casos adversariais aprovados. |
| projeção PowerShell do golden | `offline`, 5 jornadas, 2 documentos e 3 probes; stderr suprimido somente na preparação e exit code verificado antes da conversão. |
| prova RAG dinâmica | 2 testes aprovados. |
| `smoke` | runtimes, pacote, configuração, Compose e health aprovados. |
| `check` | 1.272 testes Python aprovados, 43 skips, 81,08% de cobertura e 150 testes web aprovados. |

### Ensaio técnico cronometrado do artefato

Em 24/08/2026, o coordenador executou uma prova reproduzível sobre o texto e o
baseline atuais:

1. extraiu, na ordem, a coluna “Ação e fala-base” das 13 janelas de conteúdo,
   excluindo somente a linha de reserva `14:30–15:00`;
2. contou **480 palavras** pela expressão
   `\b[\p{L}\p{N}][\p{L}\p{M}\p{N}_-]*\b`;
3. cronometrou a reprodução integral com a voz Microsoft Maria em português e
   `Rate = -2`, deliberadamente lento: **403,631 s**;
4. reexecutou `uv run --frozen poe web-browser-test`, incluindo o setup do
   servidor, com **14/14** cenários aprovados em **9,904 s**;
5. somou as etapas sem sobreposição: `403,631 s + 9,904 s = 413,535 s`, ou
   **06:53,535**.

Contra os 870 segundos de conteúdo, restaram `870 s - 413,535 s = 456,465 s`,
ou **07:36,465**, além da reserva de **00:30** até 15:00. **Cortes aplicados:
nenhum.** A soma sequencial é conservadora porque não sobrepõe fala e regressão
e inclui o setup do Chromium.

O resultado aprova o limite temporal técnico do artefato; não afirma que Renan
falou, não mede perguntas ou interrupções e não transforma o navegador controlado
em login ou smoke AWS live. Um ensaio pessoal com as telas preparadas continua
recomendado, mas não bloqueia a entrega documental. [A24]

Não use `services-up`, `applications-up` ou qualquer workflow AWS como
contingência desta demo offline. Eles ampliam dependências e não são necessários
para as três jornadas comprovadas acima. [A17]

## Checklist pré-demo

### Baseline e conteúdo

- [ ] Confirmar que a execução parte da raiz e de uma revisão aprovada.
- [ ] Confirmar árvore Git limpa antes e depois dos comandos somente leitura.
- [ ] Confirmar Python 3.13, Node.js 22, pnpm 10.15.1 e dependências instaladas
      pelos locks.
- [ ] Manter materiais originais, derivados locais, `.env` e credenciais fora
      do fluxo e das telas compartilhadas.
- [ ] Abrir previamente este roteiro, o diagrama lógico e a saída filtrada do
      golden set; abrir também a UI já no modo offline e não depender de
      navegação improvisada.
- [ ] Fechar ou ocultar terminais, abas e notificações alheios à apresentação.

### Validação executável

- [ ] Executar `uv run --frozen poe check` com sucesso.
- [ ] Executar `uv run --frozen poe smoke` com sucesso.
- [ ] Executar `uv run --frozen poe web-browser-test` e conferir 14/14 cenários
      Chromium, inclusive PKCE, zero API offline, retry, foco e reflow.
- [ ] Executar `uv run --frozen poe aws-demo-frontend-regression` e a regressão
      de entrega de 442 casos, sempre com fakes locais.
- [ ] Executar `uv run --frozen poe golden-e2e` e conferir cinco outcomes, dois
      resultados documentais e três probes.
- [ ] Executar a prova RAG dinâmica direcionada e conferir `2 passed`.
- [ ] Executar `uv run --frozen poe failure-matrix` com sucesso.
- [ ] Executar o check do OpenAPI se o contrato for mostrado.
- [x] Comprovar tecnicamente o artefato em até 14:30: 480 palavras sintetizadas
      em 403,631 s mais Chromium com setup em 9,904 s, total de 06:53,535, sem
      cortes e com 07:36,465 de margem dentro do conteúdo.
- [ ] Fazer um ensaio pessoal com as telas preparadas; recomendado para fluidez,
      sem integrar o aceite temporal documental e sem alegar execução já feita.

## Gates reconciliados e evidência futura

Os gates de conteúdo da SEN-64 e da SEN-75 foram integrados e reconciliados. A
SEN-70 não aguarda execução AWS, custo observado, smoke remoto ou teardown. O
ensaio técnico aprovou o limite de 14:30 com 06:53,535, sem cortes; um ensaio
pessoal é recomendado, não bloqueador. O estado AWS pré-release permanece
**não implantado**, com custo apenas **estimado**.

### Gate concluído — SEN-64: UI, offline e acessibilidade

- [x] Reconciliar a UI com a integração
      `3e83dd8965668b126c4e86d9d2f26d47ed5f443a`.
- [x] Descrever as ações reais da UI sem remover a prova CLI.
- [x] Provar no Chromium cinco outcomes offline e zero requisições às rotas de
      análise e documentos.
- [x] Separar a indisponibilidade documental offline do ciclo de
      aprovação/rejeição provado pelo golden e pela prova RAG dinâmica.
- [x] Vincular último resultado, loading/erro, retry, resposta fora de ordem,
      teclado, foco, alvos de 44 px, reflow e reduced motion às regressões reais.
- [x] Preservar cinco outcomes, zero API offline, último resultado, retry,
      teclado/foco, alvos, reflow e reduced motion nas regressões finais.
- [x] Atualizar [A15] com os arquivos e testes efetivamente integrados.

### Gate concluído — SEN-75: publicação autenticada pré-release

- [x] Reconciliar com `origin/develop` em
      `039c0f83819f4cc673e45a960c409a7e31d0f48b`, sem tratar configuração
      versionada como deploy live.
- [x] Vincular runtime config público e fechado, origem/API/Hosted UI allowlisted
      e ausência de segredo embutido aos arquivos e testes finais.
- [x] Documentar Authorization Code + PKCE S256, callback de uso único, sessão em
      memória, logout e falhas de autenticação conforme a implementação.
- [x] Separar o inventário e os controles de publicação da execução AWS real.
- [x] Vincular a jornada autenticada do Chromium e a regressão do frontend AWS a
      serviços sintéticos controlados, sem apresentá-las como login ou smoke live.
- [x] Criar [A22] e [A23] para autenticação e entrega, preservando [A15] para a
      UI local/offline e [A16] para o estado AWS não implantado e estimado.

### Adendo futuro pós-release — SEN-74, fora da SEN-70

A SEN-74 ocorre somente depois de SEN-70 → SEN-71 → SEN-72 e depende de
autorização explícita. Ela não faz parte do checklist, do critério de aceite nem
do ensaio de até 15 minutos da SEN-70. Se for executada no futuro, um adendo de
evidência separado poderá:

- registrar o SHA exato da release, plan/apply e inventário sanitizado;
- registrar smoke autenticado, sem expor identidade, credencial ou token;
- separar custo observado da estimativa de USD 2,72;
- registrar teardown, state e inventário residual conforme a janela autorizada;
- atualizar apenas os fatos realmente comprovados, sem reescrever a evidência
  pré-release da SEN-70 como se ela tivesse sido live.

### Fechamento e recomendações antes da apresentação

- [x] Manter [A15] vinculado exclusivamente à UI/offline/acessibilidade
      efetivamente entregues pela SEN-64.
- [x] Adicionar [A22] e [A23] para publicação/autenticação e entrega sem misturar
      as afirmações com [A15].
- [x] Confirmar em [A16] que AWS permanece arquitetura validada offline, não
      implantada e com custo estimado; nenhuma prova live é necessária para
      fechar a SEN-70.
- [x] Aprovar em [A24] o limite temporal técnico com 480 palavras, voz Microsoft
      Maria em `Rate = -2`, Chromium 14/14, soma de 06:53,535 e nenhum corte.
- [ ] Antes da apresentação, executar as três jornadas sem edição de código,
      ensaiar pessoalmente fala e trocas de tela e revisar cortes e recuperação.
      Essa recomendação não bloqueia a entrega; não afirmar que Renan já a
      executou.

## Recuperação durante a apresentação

| Sintoma | Ação segura | Retomada e fala permitida |
| --- | --- | --- |
| `golden-e2e` não inicia | Execute `uv run --frozen poe smoke` para separar problema de runtime/configuração de regressão da jornada. | Se o smoke falhar, não declare execução atual aprovada; use os documentos como evidência histórica e diga que a checagem executada naquela sessão falhou. |
| Smoke passa, mas `golden-e2e` falha | Preserve a saída sanitizada e pare a jornada executável. Não edite fixture, não repita até “passar” e não use saída antiga como se fosse atual. | Mostre o teste e o relatório versionados, rotulados como evidência do baseline, e registre a regressão depois da sessão. |
| Painel local não inicia | Use `golden-e2e` como fallback HTTP/CLI e preserve a falha da sessão. | Diga explicitamente: “Este fallback prova contratos e guardrails; a prova visual desta sessão ficou indisponível.” [A15] |
| Origem configurada ou autenticação indisponível | Retorne à composição local/offline e não improvise token, origem ou bypass. Não repita uma operação que recebeu `401`/`403`. | Diga que a evidência pré-release do runtime config, PKCE e JWT vem da regressão controlada; o fallback offline não prova login humano nem smoke AWS. [A22] |
| Provider ou geração degradam | No relatório golden, mostre somente `observed_outcome: degraded`, round-trip e contagens. Para preservação parcial, mostre separadamente a regressão de integração que verifica diagnóstico e vizinhos. | O relatório não expõe diagnóstico/vizinhos; essa preservação é comportamento testado e documentado, enquanto retry e cancelamento continuam ausentes. [A18] |
| Pergunta sobre AWS live na apresentação pré-release | Mostre o diagrama e a evidência offline versionada. | Diga “arquitetura validada offline, não implantada, com custo estimado”. Não alegue URL CloudFront, login, smoke, gasto ou teardown live; a SEN-74 pós-release não é caminho de recuperação da SEN-70. [A16] |
| JSON fica ilegível na projeção | Use a saída filtrada pré-demo; em último caso, use o relatório `product-golden-e2e.md` e os testes já abertos. | Diferencie documentação versionada, execução pré-demo e execução feita naquele momento. |
| O tempo cai para 10–12 minutos | Aplique os pontos de corte na ordem definida acima. | Preserve três jornadas, limites e conclusão. |

## Perguntas prováveis da banca

### “O modelo não tem cerca de 97% de acurácia?”

Esse número não é a acurácia do comportamento final. Os 97,3756% medem uma
candidata binária antes da abstenção, são dominados pela classe problema e ficam
abaixo da baseline que trata tudo como problema. O objetivo entregue é recuperar
históricos semelhantes e governar a prescrição; com cobertura de 39,7408% e
baixo recall operacional, a decisão registrada é **não aprovar** classificação,
automação ou autorização de manutenção. [A11]

### “Onde está o MLOps se não há SageMaker?”

Na rastreabilidade proporcional ao estágio: dados e artefatos têm lineage,
schema, IDs e hashes; o fit k-NN é determinístico; o artefato local não executa
código; a avaliação é temporal e separa abstenção; data/model cards registram
limites; e a composição `artifacts` exige promoção manual por manifesto e
SHA-256. Não há registry, retreino automático ou drift online, e o modelo não é
aprovado para automação. [A19]

### “O `support_score` é a confiança do modelo?”

Não. É uma heurística de votos e distância entre zero e um, não uma
probabilidade calibrada. Nenhuma decisão operacional deve tratá-la como
confiança. [A11]

### “O que acontece quando a leitura está fora da distribuição?”

O modelo se abstém, a API projeta `out_of_distribution` e a composição não chama
recuperação, geração ou provider. O golden set verifica essas contagens. [A08]

### “Como vocês impedem alucinação?”

O sistema reduz a superfície com schema fechado, identidade imutável do
diagnóstico, evidência aprovada vigente, citações restritas ao conjunto
recuperado e revalidação antes/depois do provider. Isso bloqueia violações
estruturais conhecidas, mas não prova que toda frase seja semanticamente
sustentada; revisão humana continua obrigatória. [A09] [A12]

### “A aprovação de um documento muda o quê?”

Somente uma versão `approved`, vigente e íntegra fica elegível. Evidência
rejeitada ou obsoleta encerra em `stale_evidence` antes do provider. A API atual
registra metadados declarados e decisão; não recebe nem valida bytes. [A07]

### “Vocês demonstram documentos reais?”

Não nesta demo. O harness usa identidades e conteúdo inteiramente sintéticos; os
materiais originais e derivados por registro permanecem locais e fora do Git.
[A04] [A10]

### “O modo offline é igual ao sistema live?”

Não existe sistema live comprovado neste baseline. A UI local offline usa cinco
fixtures do contrato e o teste de navegador observa zero chamadas à API; a
gestão documental fica explicitamente indisponível. Separadamente, o golden
prova contratos de backend por composição FastAPI/TestClient in-process, com
memória, fakes e sem chamadas externas configuradas; a flag de rede do relatório
é declarativa. Separadamente, o caminho publicado com runtime config, PKCE e JWT
foi implementado e exercitado no Chromium contra serviços sintéticos
controlados. AWS permanece não implantada; a SEN-74 só pode gerar evidência
depois da release e não integra a SEN-70. [A04] [A15] [A16] [A22]

### “A interface já está pronta?”

Para demonstração local, sim: há painel responsivo de análise e gestão
documental, cinco outcomes offline, resiliência e acessibilidade testadas. No
modo offline, a gestão documental é intencionalmente indisponível e não simulada.
O perfil publicado, runtime config, PKCE, sessão em memória e transporte JWT
também estão implementados e testados com fakes locais. Isso é prontidão
pré-release, não prova de URL disponível, login humano ou smoke AWS. [A15] [A22]
[A23]

### “Como funciona o login sem guardar token no navegador?”

O botão de entrada inicia Authorization Code com PKCE S256. Somente verifier,
state e timestamp ficam temporariamente em `sessionStorage` durante o redirect e
são consumidos antes da troca do código; access e refresh tokens permanecem na
memória da página. O `Bearer` só pode seguir para a origem, rota e método
allowlisted. Expiração, `401` ou `403` limpa a sessão e exige nova ação humana,
sem replay automático. Essa sequência foi testada contra serviços controlados;
nenhum login humano live foi executado. [A22]

### “A solução está pronta para produção?”

Não. Faltam, entre outros itens, modelo aprovado, artefatos privados autorizados,
avaliação semântica, observabilidade operacional e evidência live de
infraestrutura e autenticação. A publicação autenticada está implementada como
capacidade pré-release, mas não foi implantada nem validada por uma jornada
humana real. O uso atual é demonstração auditável com revisão humana. [A01]
[A02] [A11] [A12] [A16] [A22]

### “A AWS está implantada e quanto custou?”

Não. Terraform e gates foram validados offline, sem `apply`, deploy, smoke,
teardown ou billing live. USD 2,72 é uma **estimativa** com contingência de 50%
para as hipóteses de uma janela de oito horas; não há gasto observado. Essa é a
resposta completa e correta para a SEN-70: não há URL CloudFront, login, smoke ou
teardown live a demonstrar. Uma SEN-74 opcional e autorizada ocorre somente
pós-release e pode produzir um adendo futuro, sem bloquear este roteiro. [A16]

### “Quem aprova o deploy no GitHub?”

O contrato atual usa um único operador em dois atos: inicia manualmente o
`workflow_dispatch` no HEAD exato de `main` e depois seleciona
`Approve and deploy` no environment protegido. `prevent_self_review=false`
permite essa segunda aprovação pelo mesmo usuário; portanto, há pausa e
revalidação antes do OIDC, mas não revisão independente nem segregação de
funções. Nenhum desses workflows foi usado como deploy desta demo. [A21]

### “O que permanece quando o resultado é `degraded`?”

O relatório golden permite mostrar o outcome, o round-trip e as contagens por
camada; ele não publica diagnóstico nem vizinhos. A preservação desses campos
quando o modelo os produziu validamente é verificada pela regressão de integração
do provider desabilitado e descrita em
[`docs/validation/analysis-integration.md`](../validation/analysis-integration.md).
A prescrição e as citações não usadas permanecem ausentes nesse caso. [A18]

### “O que ocorre se o provider travar?”

A orquestração espera até o timeout configurado, limitado a 120 segundos, e
devolve degradação. Há um slot por instância, sem fila nem retry; uma chamada
tardia não altera o resultado já devolvido e mantém o slot ocupado até sair.
Isso limita crescimento, mas não inventa cancelamento cooperativo. [A13]

### “Como reproduzir a prova sem AWS?”

Com dependências já instaladas, execute da raiz `uv run --frozen poe golden-e2e`,
`uv run --frozen poe web-browser-test`,
`uv run --frozen poe aws-demo-frontend-regression` e, para regressões P0/P1,
`uv run --frozen poe failure-matrix`. Os limites e os comandos agregados estão
na seção de comandos offline. [A04] [A14] [A17] [A22] [A23]

## Matriz auditável de afirmações

| ID | Afirmação usada | Rótulo | Evidência verificável | Limite obrigatório |
| --- | --- | --- | --- | --- |
| A01 | A plataforma apoia análise rastreável e não autoriza manutenção. | implementado/limite | [`README.md`](../../README.md) | Não substitui avaliação humana. |
| A02 | O backend é um monólito modular FastAPI; `synthetic_demo` e `artifacts` são modos explícitos, mutuamente exclusivos e sem fallback. | implementado | [inventário de arquitetura](../architecture/README.md), [runtime configurável](../validation/analysis-runtime.md), [integração da análise](../validation/analysis-integration.md) | Nenhum artefato privado é descoberto ou autorizado automaticamente. |
| A03 | O contrato HTTP fecha cinco outcomes, 18 features e `top_k` de 1 a 10. | implementado | [README da API](../../apps/api/README.md), [snapshot OpenAPI](../../apps/api/openapi/v1.json) | União de estados não implica qualidade do modelo. |
| A04 | O golden set compõe FastAPI/TestClient in-process no perfil offline, com memória, fakes e sem endpoint ou provider externo configurado. | implementado/testado | [implementação do harness](../../apps/api/src/prescriptive_maintenance/product_golden.py), [relatório SEN-48](../validation/product-golden-e2e.md), [`test_product_golden_e2e.py`](../../apps/api/tests/test_product_golden_e2e.py), [fixture sintética](../../apps/api/tests/golden/product_journeys.v1.json) | As flags `limits` são declarativas, não telemetria de rede; a prova não cobre semântica nem UI. |
| A05 | `normal` chama modelo uma vez e não chama recuperação, geração ou provider. | implementado/testado | Teste `test_five_http_states_are_closed_and_layer_calls_match_the_golden_set` em [`test_product_golden_e2e.py`](../../apps/api/tests/test_product_golden_e2e.py) | Resultado sintético. |
| A06 | `documented_fault` faz round-trip, chama as quatro camadas e cita somente evidência aprovada. | implementado/testado | [relatório SEN-48](../validation/product-golden-e2e.md), [`test_product_golden_e2e.py`](../../apps/api/tests/test_product_golden_e2e.py) | Citação estrutural não prova groundedness. |
| A07 | O ciclo possui sete estados; aprovação torna a versão vigente elegível, enquanto registro HTTP é só metadado. | implementado/testado | [README da API](../../apps/api/README.md), [README web](../../apps/web/README.md), teste `test_document_registration_approval_and_rejection_use_real_boundaries` em [`test_product_golden_e2e.py`](../../apps/api/tests/test_product_golden_e2e.py) | Sem upload, validação de bytes, storage ou OCR; a UI não simula esse ciclo offline. |
| A08 | OOD encerra após o modelo, sem recuperação, geração ou provider. | implementado/testado | [abstenção SEN-51](../validation/knn-abstention.md), [`test_product_golden_e2e.py`](../../apps/api/tests/test_product_golden_e2e.py) | Abstenção reduz risco, não torna o modelo aprovado. |
| A09 | Ausência, evidência rejeitada e citação inventada terminam em recusas tipadas com 0, 0 e 1 chamada ao provider. | implementado/testado | Teste `test_fake_provider_is_blocked_without_current_approved_evidence` em [`test_product_golden_e2e.py`](../../apps/api/tests/test_product_golden_e2e.py) | Probes estruturais e sintéticos. |
| A10 | O pipeline reconciliou historicamente 166.796 linhas e produziu split temporal auditado. | medido historicamente | [data card](../data/banner-data-card.md) | Não é nova medição nem dado de produção. |
| A11 | No holdout de 24.768 linhas, a cobertura foi 39,7408%; o objetivo é similaridade, a candidata binária não supera a baseline constante e `support_score` não é probabilidade. | medido historicamente/decisão | [model card v3](../model-cards/temporal-knn-v3.md), [avaliação temporal v2](../validation/model-evaluation-v2.md) | Holdout já observado, baixo recall operacional e modelo não aprovado. |
| A12 | O RAG valida contrato, citações e vigência, mas não comprova suporte semântico de cada frase. | implementado/limite | [RAG card](../rag/prescriptive-rag-card.md), [orquestração SEN-59](../validation/prescription-orchestration.md) | Provider fake não prova qualidade de linguagem. |
| A13 | Provider síncrono tem timeout de até 120 s, slot unitário, sem fila/retry e sem cancelamento cooperativo. | implementado/testado | [orquestração SEN-59](../validation/prescription-orchestration.md), [`test_prescription_orchestration.py`](../../apps/api/tests/test_prescription_orchestration.py) | Chamada que nunca retorna mantém a instância ocupada. |
| A14 | A matriz P0/P1 seleciona falhas seguras e audita objetos Git publicáveis. | implementado/testado | [matriz SEN-66](../validation/safe-failure-matrix.md), tarefa `failure-matrix` em [`pyproject.toml`](../../pyproject.toml) | Não substitui o check completo. |
| A15 | A UI local demonstra cinco outcomes offline com zero API observado, preserva o último laudo, oferece retry e cobre teclado, foco, alvos, reflow e reduced motion; documentos ficam explicitamente indisponíveis offline. | implementado/testado | [README web](../../apps/web/README.md), [`demo.spec.js`](../../apps/web/tests/browser/demo.spec.js), testes Node em [`apps/web/tests`](../../apps/web/tests) | Retry usa respostas HTTP controladas; esta afirmação não cobre autenticação ou publicação. |
| A16 | Para a SEN-70, AWS é arquitetura e validação offline no estado não implantado; não há gasto observado e USD 2,72 é estimativa com contingência. SEN-74 é execução opcional e autorizada pós-release, capaz apenas de gerar um adendo futuro. | implementado offline/estimado/limite | [evidência SEN-69](../validation/aws-demo-evidence.md), [diagramas](../architecture/diagrams.md) | Evidência live não integra o aceite nem os 14:30; não alegar URL CloudFront disponível, login humano, smoke, gasto ou teardown live. |
| A17 | `check`, `smoke`, `web-browser-test`, `aws-demo-frontend-regression`, `golden-e2e`, `failure-matrix`, `public-repository-audit`, o teste RAG direcionado e o check do OpenAPI são interfaces existentes. | implementado | tarefas em [`pyproject.toml`](../../pyproject.toml), [README web](../../apps/web/README.md), [prova RAG](../validation/dynamic-rag-e2e.md), [guia de scripts](../../scripts/README.md) | Setup prévio pode exigir download; serviços e AWS não são necessários para as provas offline. |
| A18 | Na degradação por provider desabilitado, a regressão de integração preserva diagnóstico e vizinhos válidos e omite prescrição e citações não usadas. | implementado/testado | [integração da análise](../validation/analysis-integration.md), teste `test_disabled_provider_degrades_without_presenting_unused_evidence` em [`test_analysis_integration.py`](../../apps/api/tests/test_analysis_integration.py) | O relatório golden mostra outcome, round-trip e contagens, não diagnóstico/vizinhos; não inferir a preservação somente dele. |
| A19 | O MLOps atual liga lineage/hashes, treino k-NN determinístico, artefato local não executável versionado por schema, avaliação temporal, abstenção, cards, baseline fixa e autorização manual por manifesto. | implementado/limite | [data card](../data/banner-data-card.md), [model card v3](../model-cards/temporal-knn-v3.md), [avaliação v2](../validation/model-evaluation-v2.md), [runtime configurável](../validation/analysis-runtime.md), [README da API](../../apps/api/README.md) | Sem SageMaker, registry, retreino automático ou drift online; não aprovado para automação. |
| A20 | A prova RAG dinâmica cria extração sintética nova, percorre chunking/indexação/ciclo documental e vincula a citação ao chunk aprovado ranqueado. | implementado/testado | [relatório SEN-77](../validation/dynamic-rag-e2e.md), [`test_dynamic_rag_e2e.py`](../../apps/api/tests/test_dynamic_rag_e2e.py) | Não usa resposta pronta de fixture nem prova qualidade semântica; SEN-77 não é gate. |
| A21 | O gate GitHub usa `workflow_dispatch` e `Approve and deploy` pelo mesmo operador, com revalidação antes do OIDC e sem revisão independente. | implementado/limite | [contrato de entrega](../../infra/aws/demo/delivery/README.md), [workflow de deploy](../../.github/workflows/aws-demo-deploy.yml), [`github_environment_gate.py`](../../infra/aws/demo/scripts/github_environment_gate.py) | O controle foi validado offline; não comprova execução AWS nem segregação de funções. |
| A22 | A origem publicada exata ativa runtime config fechado e sem segredos, Authorization Code + PKCE S256, material de redirect de uso único, tokens em memória e `Bearer` restrito à API allowlisted; expiração, `401` e `403` exigem nova ação humana sem replay. | implementado/testado | [README web](../../apps/web/README.md), [`runtime-config.js`](../../apps/web/src/config/runtime-config.js), [`cognito.js`](../../apps/web/src/auth/cognito.js), [`session.js`](../../apps/web/src/auth/session.js), [`authenticated-fetch.js`](../../apps/web/src/api/authenticated-fetch.js), [`demo.spec.js`](../../apps/web/tests/browser/demo.spec.js) | A regressão intercepta origens e usa respostas sintéticas; não comprova login humano, disponibilidade da origem ou aceitação do JWT pela AWS. |
| A23 | Staging, allowlist de assets, runtime config, headers, publicação/invalidação e smoke do frontend, além das políticas e regressões de entrega, são reproduzíveis offline. | implementado/testado offline | [`frontend_delivery_regression.py`](../../infra/aws/demo/scripts/frontend_delivery_regression.py), [`delivery_regression.py`](../../infra/aws/demo/scripts/delivery_regression.py), [workflow offline](../../.github/workflows/aws-demo-validate.yml), [contrato de entrega](../../infra/aws/demo/delivery/README.md) | Fakes e subprocessos locais não provam S3, CloudFront, Cognito, OIDC, API Gateway, custo ou teardown live. |
| A24 | As 13 falas-base têm 480 palavras; Microsoft Maria em português com `Rate = -2` levou 403,631 s e Chromium 14/14 com setup levou 9,904 s. A soma sequencial foi 413,535 s (06:53,535), sem cortes. | medido tecnicamente | [ensaio técnico cronometrado](#ensaio-técnico-cronometrado-do-artefato), [`demo.spec.js`](../../apps/web/tests/browser/demo.spec.js) | Voz sintética e navegador controlado não afirmam fala de Renan, perguntas da banca, login humano ou AWS live; ensaio pessoal é recomendado, não bloqueador. |

## Estado do roteiro

O baseline contém a UI final da SEN-64, o perfil publicado autenticado da SEN-75,
o runtime explícito, a correção semântica do k-NN, a prova RAG dinâmica e o golden
set. Os gates de conteúdo foram reconciliados e o critério temporal da SEN-70
foi aprovado tecnicamente em 06:53,535, sem cortes, contra 14:30 de conteúdo e
00:30 de reserva até 15:00. O ensaio não afirma que Renan falou; uma passagem
pessoal continua recomendada e não bloqueia a entrega. SEN-77 não é gate e
SEN-74 ocorre somente pós-release, sem condicionar o fechamento.
