# Relatório consolidado de fechamento dos dez épicos

- Responsável: Renan Mocelin
- Data da consolidação: 2026-08-24
- Baseline consultada: `origin/develop` em `32944dfabba772a7713c2c7b3d9cc3d7c6ec687c`
- Release estável: `origin/main` em `601c26717de021bc424bf4b3e310078cee3d7186`
- Estado: dez épicos consolidados, auditoria pública concluída e release promovida

Este relatório organiza as decisões e evidências das épicas SEN-1 a SEN-10 sem
substituir os documentos canônicos. A fonte de cada afirmação material é ligada
por caminho relativo; relatórios antigos são tratados como fotografias da
baseline que avaliaram, não como prova automática do estado atual. O
[README principal](../../README.md) resume o produto integrado e o
[índice técnico](../README.md) conduz aos detalhes.

## Como ler os estados de evidência

| Rótulo | Significado neste relatório |
| --- | --- |
| **Implementado** | Capacidade presente na baseline versionada. Não implica aprovação operacional. |
| **Testado** | Comportamento exercitado por teste identificado; o texto informa quando os dados ou providers são sintéticos. |
| **Medido historicamente** | Resultado de execução registrado em documento versionado, interpretado com a proveniência disponível e os limites declarados; não foi medido novamente nesta consolidação. |
| **Estimado** | Cálculo baseado em hipóteses declaradas; não é observação de uso ou cobrança. |
| **Deferido** | Trabalho planejado ou avaliado que não integra a baseline; o motivo específico é declarado em contexto. |
| **Pós-release** | Verificação ou evolução que só pode produzir evidência depois da promoção estável; não pertence ao produto atual. |
| **Pendente nesta rodada** | Dependência ainda não integrada à baseline deste documento. |

“Apoio” significa fornecer evidência para uma pessoa decidir. Nenhuma seção
autoriza manutenção, transforma heurística em probabilidade ou apresenta
capacidade futura como entrega.

## SEN-1 — Fundação do projeto e governança de engenharia

### Problema e objetivo

Antes de dados e modelos, o projeto precisava de uma base pública reproduzível,
com limites claros para conteúdo restrito, mudanças isoladas e validação igual
no desenvolvimento e na integração contínua.

### Decisões técnicas e por que foram escolhidas

- Um monorepo com monólito modular mantém API, web, infraestrutura, dados e
  documentação sob os mesmos contratos e locks, sem criar fronteiras de deploy
  não justificadas.
- O repositório é público para avaliação, mas não possui licença de
  reutilização. Fontes e derivados restritos ficam fora do Git; somente
  identidades sanitizadas e fixtures sintéticas são públicas.
- Python 3.13, Node.js 22, uv, pnpm e Poe foram fixados para reduzir diferenças
  entre máquinas. `develop` recebe tarefas por pull request; `main` recebe
  somente releases pelo fluxo verificável de dois pais.
- As decisões foram registradas como ADRs para preservar contexto, alternativas
  e consequências. O [índice de ADRs](../adr/README.md) reúne as quatro decisões
  vigentes.

### O que foi realmente entregue

**Implementado:** estrutura do monorepo, configuração reproduzível, locks,
comandos canônicos, CI, gates de segurança, política de pull request, GitFlow,
documentação de contribuição e fronteira pública dos materiais. O
[inventário de arquitetura](../architecture/README.md) detalha os componentes e
os limites registrados.

**Testado:** setup, qualidade, hooks, smoke e PostgreSQL/pgvector foram
executados em clone isolado na validação da Foundation. O fluxo de promoção foi
posteriormente corrigido para evitar repetir a divergência de ancestralidade
causada pelo primeiro squash de uma branch longa.

### Evidências e resultados

**Medido historicamente:** no commit `514ca43`, um clone HTTPS anônimo concluiu
setup, 9 testes com 100% de cobertura naquele recorte, 11 hooks, smoke e uma
consulta vetorial local; a árvore rastreada permaneceu limpa. Esses números
descrevem somente a Foundation de 2026-08-22, conforme o
[relatório clean-room](../validation/foundation-clean-room.md), e não a suíte
atual, que cresceu com as demais épicas.

### Riscos e limites

A reprodutibilidade ainda depende das versões de runtime e, para o caminho com
serviços, de Docker Compose. A auditoria histórica não substitui a auditoria
final da entrega. Os materiais restritos permanecem intencionalmente ausentes,
portanto um clone público comprova a fronteira e os dados sintéticos, não o
conteúdo local.

### Explicação curta para a banca

A fundação é o mecanismo de confiança do restante: uma métrica só é útil quando
o código, a entrada, a configuração e o caminho de revisão podem ser
reproduzidos sem expor dados ou depender de mudanças diretas nas branches
estáveis.

## SEN-2 — Dados: qualidade, taxonomia e pipeline reprodutível

### Problema e objetivo

O conjunto temporal precisava ser transformado em partições auditáveis para
modelagem sem perda silenciosa, vazamento de target ou publicação de registros.

### Decisões técnicas e por que foram escolhidas

- A fonte é aberta por uma porta explícita e somente leitura, com tamanho e
  SHA-256 conferidos antes e depois. Isso impede descoberta acidental e detecta
  mudança concorrente.
- O contrato reduz as 26 colunas de origem a 18 features canônicas disponíveis
  no instante do evento; IDs, tempo, destino e target ficam fora de `X`.
- Ocorrências e fronteiras 70/15/15 são decididas cronologicamente sem receber
  `y`. Estatísticas IQR são ajustadas somente no treino; outlier é sinalizado,
  não removido por padrão.
- Um ledger atribui exatamente um destino a cada linha, e a escrita é atômica e
  fail-closed. Essa escolha privilegia rastreabilidade sobre limpeza implícita.

### O que foi realmente entregue

**Implementado:** toolchain de dados, contrato estrito, profiler agregado,
inventário determinístico de 151 rótulos brutos, política de qualidade, build
canônico, partições temporais, seis artefatos locais e checker somente leitura.
O target continua categórico e exato; não foi criada uma equivalência semântica
industrial entre rótulos.

**Testado:** CI usa cenários inteiramente sintéticos para schema, leakage,
purga, determinismo, escrita segura e reconciliação. Os derivados por registro
continuam locais e ignorados.

### Evidências e resultados

**Medido historicamente:** duas execuções independentes reconciliaram 166.796
linhas em 568 ocorrências e produziram os mesmos IDs e hashes. O split registrou
116.882 linhas/145 ocorrências em treino, 25.146/26 em validação e 24.768/397 em
teste; os 11 gates ficaram verdadeiros nas duas execuções. Todas as linhas
receberam `corrected` por inconsistência entre representações redundantes de
unidade, e 65.998 também receberam sinal IQR. Os denominadores e hashes estão no
[relatório do pipeline](../validation/data-pipeline.md); o
[data card](../data/banner-data-card.md) e a
[política de qualidade](../data/banner-quality-policy.md) explicam o uso
permitido.

### Riscos e limites

A correção unitária em 100% das linhas depende da decisão de confiar nas colunas
canônicas e exige revisão de domínio antes de conclusões industriais. Há um
único fluxo temporal e nenhuma chave separada de ativo. A distribuição e o
inventário de classes mudam materialmente ao longo do tempo; hashes provam
identidade, não correção física. Não há ingestão contínua, imputação,
balanceamento ou automação de retreino.

### Explicação curta para a banca

O ponto central não é apenas “limpar dados”: é provar que o modelo vê somente
informação disponível no passado, que nenhuma ocorrência cruza partições e que
cada registro tem destino e motivo auditáveis.

## SEN-3 — Motor de similaridade e diagnóstico assistido

### Problema e objetivo

O sistema precisava recuperar históricos comparáveis e expressar quando a
similaridade não sustenta uma candidata, sem converter distância em certeza ou
em autorização automática.

### Decisões técnicas e por que foram escolhidas

- O k-NN v3 padroniza 18 features com estatísticas do treino e faz busca exata
  dos cinco vizinhos por distância euclidiana. Ranking e referências opacas
  tornam a comparação auditável.
- O voto forma apenas uma **condição candidata**. `support_score` é uma
  heurística de votos e distância, não probabilidade ou confiança calibrada.
- Distância anômala ou voto inconclusivo produz abstenção. Modelo, índice,
  dataset e política operacional são versionados e precisam concordar; não há
  rebind silencioso.
- A interpretação primária passou de 151 rótulos exatos para a distinção
  operacional versus problema, preservando a avaliação exata como diagnóstico
  secundário. Isso responde ao uso prescritivo sem renomear erro de classe como
  acerto.

### O que foi realmente entregue

**Implementado:** motor k-NN v3 determinístico, build e serialização capazes de
produzir artefatos locais íntegros, índice `exact-flat`, ranking top-k, política
fechada de estados operacionais, abstenção tipada e composição autorizada pelo
modo `artifacts`. O uso aprovado é recuperação de similaridade com revisão
humana. O model card v3 rejeita explicitamente a leitura como classificador
aprovado, probabilidade ou automação.

**Testado:** paridade entre modelo e índice, integridade de artefatos,
incompatibilidade v2/v3, ranking determinístico, motivos de abstenção e jornada
HTTP com artefatos sintéticos foram exercitados. O
[runtime de análise](../validation/analysis-runtime.md) mostra a composição
fail-closed.

### Evidências e resultados

**Medido historicamente:** na execução documentada, as instâncias v3 do modelo e
do índice foram reconstruídas e verificadas localmente. A leitura operacional
pós-hoc no holdout temporal já observado foi 24.118/24.768, ou 97,3756%, para a
candidata binária antes da abstenção. Esse número inclui as 14.925 linhas que
depois foram abstidas, é dominado pelas 24.446 linhas de problema e fica abaixo
da baseline “sempre problema”, de 98,6999%. Portanto, 97,3756% não é acurácia
final do sistema.

A cobertura foi 9.843/24.768, ou 39,7408%. Entre as aceitas, a acurácia foi
96,9928%, também abaixo dos 97,6938% da baseline constante no mesmo recorte. A
acurácia balanceada e o recall operacional ficaram acima do trivial — 59,4426%
e 20,4969% antes da abstenção; 60,6095% e 22,4670% entre aceitas —, mas o ganho
recupera pouco mais de um quinto dos estados operacionais e não fecha um gate
de uso autônomo. Fórmulas e denominadores estão na
[avaliação corrigida](../validation/model-evaluation-v2.md).

A mudança temporal é material: 24.543 das 24.768 linhas do teste pertencem a
rótulos exatos ausentes do treino. Na avaliação histórica, 9.676 dessas linhas
foram aceitas, falso aceite open-set pós-hoc de 39,4247%. O holdout já havia
tido agregados observados, logo a medição é confirmatória e não uma estimativa
independente de generalização. A fonte é a
[avaliação temporal histórica](../validation/model-evaluation.md).

**Deferido:** a melhoria planejada na SEN-73 para comportamento open-set,
métricas de ranking e rastreabilidade permanece deferida. Nenhum artefato nem
recalibração dessa iniciativa integra a baseline. A entrega continua no k-NN v3
documentado no [model card atual](../model-cards/temporal-knn-v3.md).

### Riscos e limites

Uma classe semanticamente nova pode ocupar região geométrica conhecida. A baixa
cobertura, o baixo recall operacional, o holdout já observado e a ausência de
probabilidade calibrada impedem aprovação operacional. Uma nova conclusão exige
objetivo e custos pré-registrados e uma janela temporal ainda não observada.

### Explicação curta para a banca

O motor responde “quais históricos são próximos?” e pode se abster; ele não
responde sozinho “qual manutenção executar?”. A acurácia bruta alta é enganosa
por desbalanceamento e por incluir casos depois abstidos, por isso cobertura,
recalls e baseline são apresentados juntos.

## SEN-4 — Pipeline documental e base de conhecimento

### Problema e objetivo

Documentos técnicos só podem sustentar uma orientação quando sua extração,
versão, qualidade e aprovação são rastreáveis. Processar um arquivo não deve
conceder autoridade automática ao seu conteúdo.

### Decisões técnicas e por que foram escolhidas

- Texto nativo é preferido ao OCR para reduzir custo e incerteza; OCR local é
  usado apenas quando necessário e mantém qualidade por página.
- Chunks nunca atravessam página e recebem IDs por conteúdo, localização e
  configuração. Isso permite reproduzir a linhagem da citação.
- Extração, indexação, ciclo de vida e recuperação têm responsabilidades
  separadas. Somente versão `approved`, vigente e íntegra é elegível.
- O filtro por classe e integridade ocorre antes do ranking, e o snapshot é
  conferido novamente depois. O embedding hash local prova o contrato, não
  similaridade semântica.

### O que foi realmente entregue

**Implementado:** inventário e extração local protegida, chunking determinístico,
porta de indexação, ciclo fechado de sete estados, histórico append-only,
aprovação com CAS, mapeamento externo versionado e recuperação de evidência
content-free. O [relatório da SEN-4](../validation/document-pipeline.md) delimita
o recorte original; integrações HTTP e web posteriores são tratadas nas SEN-6 e
SEN-7.

**Testado:** chunking, embedding fake, repositórios, lifecycle, concorrência e
recuperação foram testados publicamente com dados sintéticos. A
[prova dinâmica](../validation/dynamic-rag-e2e.md) percorre JSON de extração,
indexação, aprovação, supersessão, ranking e citação sem resposta pronta.

### Evidências e resultados

**Medido historicamente:** a evidência sanitizada dos seis documentos registra
62 páginas: 45 extraídas nativamente e 17 por OCR, com uma página suspeita e
zero falhas. Cinco extrações terminaram concluídas e uma exigiu atenção. Duas
execuções preservaram os mesmos agregados e fingerprints. Isso comprova
inventário e extração; não comprova chunks reais, vetores, carga pgvector,
aprovação vigente ou RAG real sobre os seis documentos.

### Riscos e limites

Confiança de OCR não prova fidelidade semântica, e a página sinalizada requer
revisão humana. O embedding público é não semântico; não há contagem aprovada de
chunks reais, mapping industrial publicado nem prova de carga real desses
documentos em pgvector. A releitura reduz TOCTOU, mas não cria uma transação ou
lease distribuído.

### Explicação curta para a banca

A cadeia separa “foi lido”, “virou unidade recuperável”, “foi aprovado” e “foi
usado”. Essa separação impede que um documento recém-processado se torne norma
sem revisão e permite voltar da citação à versão e à página exatas.

## SEN-5 — RAG prescritivo, geração e guardrails

### Problema e objetivo

A camada de geração precisava tornar evidências autorizadas legíveis sem
rediagnosticar o evento, inventar procedimento ou chamar provider quando não há
fonte adequada.

### Decisões técnicas e por que foram escolhidas

- O diagnóstico é entrada imutável. Só uma disposição `FAULT` com chave
  documental, mapping válido e evidência aprovada pode alcançar o provider.
- Evidências entram em envelope não confiável; schema, citações e vigência são
  validados antes e depois da chamada. Ausência, conflito ou obsolescência
  produz recusa ou degradação, nunca preenchimento inventado.
- Uma chamada síncrona usa no máximo um slot por instância, sem fila ou retry,
  e timeout de até 120 s. Isso limita duplicação, embora não cancele um provider
  que nunca retorna.
- O provider fake é determinístico e offline. A fronteira Bedrock é lazy,
  injetável e desabilitada; ela não descobre credenciais nem prova qualidade de
  linguagem.

### O que foi realmente entregue

**Implementado:** contratos estruturados, prompt versionado, recuperação
governada, guardrails pré/pós-provider, citações por identidades recuperadas,
orquestração com estados `generated`, `skipped`, `refused` e `degraded`, e
projeção para os cinco outcomes HTTP. O
[RAG card](../rag/prescriptive-rag-card.md) é a fonte de uso permitido; a
[orquestração](../validation/prescription-orchestration.md) detalha timeout,
metadados e preservação do diagnóstico.

**Testado:** o golden set prova chamada somente no caso documentado e recusa
sem evidência; a prova dinâmica liga a citação ao chunk ranqueado; regressões
confirmam que instruções documentais e colisões de sentinela permanecem dados no
envelope não confiável. Separadamente, os guardrails recusam citação inventada,
versão obsoleta, timeout, ocupação e saída inválida. Tudo usa dados e providers
sintéticos.

### Evidências e resultados

**Medido historicamente:** a prova dinâmica registrou 2 testes direcionados
aprovados; na mesma revisão, o gate agregado registrou 1.156 testes aprovados e
43 skips condicionais. O [golden E2E](../validation/product-golden-e2e.md)
percorreu os cinco outcomes e o ciclo documental com contagem exata de chamadas
por camada. Essas contagens provam controle de fluxo, não qualidade semântica.

### Riscos e limites

Uma citação estruturalmente válida não prova que cada frase é sustentada.
Conteúdo hostil ainda pode influenciar um provider, e a segunda conferência não
elimina toda janela concorrente. Não foram medidos groundedness, qualidade da
prescrição, embedding semântico, provider real habilitado e validado live, custo
ou latência de rede. Toda saída continua sujeita a revisão humana.

### Explicação curta para a banca

O modelo sugere a condição e os casos próximos; o RAG só redige quando há fonte
aprovada. Sem documento vigente, a ausência de prescrição é o comportamento
correto, não uma falha a esconder.

## SEN-6 — API, persistência e orquestração backend

### Problema e objetivo

As capacidades internas precisavam de uma fronteira HTTP estável que preservasse
os estados de segurança, correlacionasse falhas e persistisse somente metadados
necessários à auditoria.

### Decisões técnicas e por que foram escolhidas

- FastAPI e um snapshot OpenAPI v1 fechado tornam validação e geração de tipos
  parte do contrato, sem copiar modelos para a web.
- O backend permanece um monólito modular com portas injetadas. A composição
  exige `synthetic_demo` ou `artifacts`; não há descoberta nem fallback
  silencioso.
- Liveness mede processo e readiness mede aptidão. Configuração estrutural
  inválida bloqueia startup; artefato indisponível mantém liveness e devolve
  readiness/análise 503 sanitizado.
- UoW e migrações explícitas oferecem adapters em memória e PostgreSQL. A trilha
  persiste `analysis_id`, `outcome`, IDs de dataset, modelo, prompt e
  configuração, `created_at` e referências ordenadas de evidência. Não persiste
  features, texto documental, diagnóstico nem prescrição.

### O que foi realmente entregue

**Implementado:** `POST /analysis`, consulta por ID, contrato de 18 features e
`top_k` de 1 a 10, cinco outcomes, health checks, correlation ID, erros
sanitizados e seis operações do ciclo documental. O registro documental recebe
metadados, não bytes. O [README da API](../../apps/api/README.md) descreve o
contrato e a persistência.

A [integração da análise](../validation/analysis-integration.md) liga modelo,
índice, recuperação, geração, projeção e UoW por autorização imutável. O modo
`artifacts` atual carrega a composição apenas quando todos os hashes e bindings
concordam.

**Testado:** os cinco outcomes, corrupção de bindings, timeout, ausência de
evidência, rollback, cache após commit, erros OpenAPI e round-trip PostgreSQL
opcional possuem testes. A prova do modo `artifacts` cria modelo, índice e
documento inteiramente sintéticos em diretório temporário.

### Evidências e resultados

O resultado verificável é contratual: 18 features ordenadas, cinco outcomes,
sete estados documentais e erros fechados. O
[relatório do runtime](../validation/analysis-runtime.md) registra ranking
idêntico, citação do único chunk aprovado e persistência da referência exata na
jornada sintética. Ausência de derivados no smoke opcional é `skip` explícito,
não aprovação inventada.

### Riscos e limites

Não há autenticação, autorização, rate limiting ou operação de produção. A API
documental não valida tamanho/hash contra bytes, não faz upload nem inicia OCR.
O `GET` completo da análise é process-local; o banco conserva metadados, não a
resposta pública. Provider real, fila distribuída e retry automático continuam
ausentes.

### Explicação curta para a banca

A API é a fronteira que impede combinações impossíveis: uma dependência pode
degradar a resposta sem apagar diagnóstico e vizinhos, e uma configuração
incoerente falha antes de parecer uma análise válida.

## SEN-7 — Dashboard e experiência da demonstração

### Problema e objetivo

A demonstração precisava expor diagnóstico, evidências, prescrição e recusas de
modo compreensível, além de operar o ciclo documental sem ocultar seus limites.

### Decisões técnicas e por que foram escolhidas

- A entrega integrada usa React 19, TypeScript estrito e Vite. Tipos derivados
  do OpenAPI, build reproduzível e testes do bundle mantêm a interface alinhada
  ao contrato sem duplicar modelos de request e response.
- Contratos de análise e documentos são gerados do OpenAPI. Um `200` só vira
  laudo depois da validação estrita da variante; prescrição incompatível fica
  indisponível, não parcialmente renderizada.
- O proxy same-origin possui allowlist das operações publicadas, limites e
  timeouts; não é proxy genérico. O modo offline aceita somente os cinco pares
  sintéticos exatos e não infere outcome de uma entrada alterada.
- O perfil AWS é ativado somente na origem publicada exata. O login usa Cognito
  Hosted UI com Authorization Code e PKCE S256; tokens ficam somente em memória
  e o Bearer é limitado à origem, aos paths e aos métodos aprovados.
- Estado anterior, foco, teclado, reduced motion e reflow foram tratados como
  comportamento funcional, não acabamento opcional.

### O que foi realmente entregue

**Implementado:** a SEN-47 entregou o fluxo de análise; a SEN-63 integrou a
gestão documental mínima com os sete estados e transições aplicáveis; a SEN-64
integrou resiliência, acessibilidade e verificações em Chromium; a SEN-75
integrou a capacidade de publicar o frontend autenticado com Code + PKCE. O
estado atual está no [README do painel](../../apps/web/README.md).

O painel importa 18 features por formulário ou JSON, mostra os cinco outcomes,
diagnóstico, suporte heurístico, vizinhos, citações e disponibilidade da
prescrição. A área documental registra somente `filename`, `media_type`,
`size_bytes` e `sha256`, depois oferece aprovação, rejeição e reprocessamento
conforme o estado.

O assistente extrativo permanece implementado e testado na API, mas foi retirado
da navegação final. Seu corpus público fixo comprova recuperação, citação e
abstenção; não representa consulta aos documentos privados do desafio.

**Testado:** contrato, decoder, importação, proxy, apresentação e falhas têm
testes Node; o browser-test cobre modo offline sem chamadas, cinco outcomes,
teclado, foco, retry, ordem de respostas, reduced motion, alvos, reflow e o fluxo
publicado sintético de autorização, callback, troca de token e chamada
autenticada única.

### Evidências e resultados

**Medido historicamente:** a primeira entrega do dashboard registrou 92 testes
web aprovados, stack local com três contêineres saudáveis e reflow sem rolagem
horizontal a 390 px. Esse snapshot antecede SEN-63/SEN-64 e está no
[relatório do dashboard](../validation/analysis-dashboard.md); o README atual é
a fonte do escopo integrado posterior.

**Testado no PR:** o
[PR #58](https://github.com/HiRenan/senai-prescriptive-maintenance/pull/58)
registrou 1.272 testes Python aprovados, 43 skips previstos e cobertura de
81,08%, além de 150 testes web e 14/14 cenários Chromium aprovados. O PR foi
integrado em `develop` pelo commit
`039c0f83819f4cc673e45a960c409a7e31d0f48b`.

Esses resultados exercitam o frontend e o fluxo Code + PKCE com serviços
sintéticos. Nenhuma publicação, URL, conta, login humano ou jornada autenticada
foi validada em AWS live.

### Riscos e limites

O modo offline não executa decisões documentais e só reproduz fixtures exatas.
O cadastro não envia PDF nem prova conteúdo. Não há conversa multi-turno, histórico
completo de análises ou autenticação nos perfis local/offline. A autenticação do
perfil AWS está implementada e testada offline, mas não validada live. A
comparação visual de features é descritiva e não prova causa ou gravidade.

### Explicação curta para a banca

A interface apresenta as camadas separadamente: sinal recebido, candidata e
vizinhos, evidência documental e eventual prescrição. Quando o contrato não
autoriza prescrição, a tela mostra a retenção e o próximo passo em vez de
preencher uma resposta convincente.

## SEN-8 — Qualidade, avaliação e observabilidade

### Problema e objetivo

Testes de software, avaliação de similaridade, qualidade documental e segurança
de geração respondem perguntas diferentes. A estratégia precisava localizar
falhas e registrar limites sem depender de credenciais ou serviços pagos.

### Decisões técnicas e por que foram escolhidas

- A suíte padrão usa fixtures sintéticas e locks congelados; integrações
  externas são opcionais e identificadas.
- Golden set, prova dinâmica RAG e matriz de falha exercitam jornadas e
  recusas, enquanto avaliações de modelo mantêm seus próprios denominadores.
- O benchmark mede fronteiras dentro do `POST /analysis`, separa aquecimento,
  latência, erro e `tracemalloc`, e liga o resultado ao commit e à árvore. Isso
  evita comparar execuções sem proveniência.
- Logs usam correlation ID e campos allowlisted, sem payload, prompt, conteúdo
  documental ou texto de exceção.

### O que foi realmente entregue

**Implementado:** testes unitários, integração, contratos, migrações, golden
E2E, matriz de falha segura, auditoria pública, benchmark sintético e
observabilidade sanitizada. A [matriz de falhas](../validation/safe-failure-matrix.md),
o [golden set](../validation/product-golden-e2e.md) e o
[benchmark](../validation/analysis-benchmark.md) delimitam seus escopos.

**Testado:** banco indisponível, rollback, OCR falho, documento inválido,
instrução hostil, citação inventada, versão obsoleta, timeout/ocupação do
provider e entrada HTTP inválida possuem resultados seguros. O golden set cobre
os cinco outcomes e o ciclo documental; a prova dinâmica confirma linhagem e
citação.

### Evidências e resultados

**Medido historicamente:** na SEN-66, a seleção rápida registrou 30 casos e a
auditoria sanitizada aprovados; o gate agregado registrou 1.132 testes aprovados,
43 skips opcionais e 80,56% de cobertura. O Gitleaks de histórico e diff passou,
mas a varredura bruta do diretório não foi declarada aprovada porque encontrou
33 ocorrências preexistentes em caminhos ignorados; zero estava no diff ou em
arquivos rastreados.

O benchmark está **implementado e testado**, mas este repositório não adota
números de latência sintética como desempenho de artefatos reais. Ele não mede
concorrência, throughput, RSS, GPU, rede ou provider pago.

### Riscos e limites

Fakes comprovam contrato e controle de fluxo, não qualidade semântica ou
desempenho industrial. Cobertura de testes não substitui nova janela temporal
nem revisão humana. A auditoria pública final foi integrada antes da release;
ela comprova a fronteira pública, não a qualidade industrial do resultado.

### Explicação curta para a banca

As métricas são separadas para evitar uma conclusão indevida: software correto
não torna o modelo preciso, vizinho próximo não torna a prescrição correta e
texto fluente não torna a evidência segura.

## SEN-9 — Deploy AWS, segurança e arquitetura industrial

### Problema e objetivo

O projeto precisava provar que sua arquitetura poderia ser implantada de forma
privada, removível e controlada por custo, sem fingir que código Terraform
validado equivale a ambiente executado.

### Decisões técnicas e por que foram escolhidas

- ECS Fargate executa a imagem OCI existente; Lambda exigiria adaptação de
  runtime não implementada. API Gateway usa VPC Link e Cloud Map para alcançar
  a task privada.
- A VPC single-AZ não possui Internet Gateway, NAT ou IP público. Endpoints
  privados atendem ECR, logs, SQS e S3; a escolha reduz exposição, com custo e
  disponibilidade assumidos para uma janela curta.
- CloudFront/OAC, bucket privado, Cognito/JWT, IAM por ação/recurso, Budget e
  alarmes estão declarados. Os fluxos capazes de alterar AWS são manuais,
  protegidos por environment, SHA de `main` e roles OIDC distintas.
- A fundação usa `api_desired_count=0`, ECR e frontend vazios. O perfil seleciona
  `memory` e `synthetic_demo`; não inventa banco, worker ou Bedrock operacional.

### O que foi realmente entregue

**Implementado:** perfil Terraform efêmero, plano estático auditável com 75
recursos gerenciados planejados, outputs públicos em allowlist fechada, políticas
IAM versionadas, regressões adversariais e workflows de validação, plan, deploy
e remoção. A SEN-75 acrescentou publicação web por allowlist, runtime config
público, CloudFront/OAC e Cognito Code + PKCE ao caminho protegido. O
[README do perfil](../../infra/aws/demo/README.md) e o
[contrato de entrega](../../infra/aws/demo/delivery/README.md) descrevem o
estado atual; a [evidência SEN-69](../validation/aws-demo-evidence.md) registra
a fotografia offline.

**Testado:** `fmt`, inicialização sem backend, `validate`, plano sintético,
auditoria de rede/IAM/outputs e regressões de segurança foram executados sem
credenciais. A SEN-69 registrou historicamente 73 criações no plano, 14 outputs
e 388 casos adversariais. Esse snapshot foi supersedido para a contagem atual: a
allowlist integrada contém 75 criações, portanto 73 não descreve esta baseline.
Na SEN-75, `delivery_regression.py` aprovou 442 casos adversariais. Achados
iniciais do CodeQL no teste de navegador foram corrigidos antes da integração, e
os checks finais do PR #58 ficaram aprovados.

**Estimado:** o relatório de 2026-08-23 calcula USD 2,72 com contingência de 50%
para uma janela hipotética de oito horas. É cálculo por preços e hipóteses,
não cobrança, gasto observado ou garantia de teto.

Uma execução AWS live alcançou criação parcial de recursos da fundação e parou
por permissões EC2 ausentes no contrato. A correção foi integrada e validada
offline, mas não houve nova tentativa: deploy, publicação, smoke remoto, URL e
remoção final não foram comprovados. Os workflows versionados são capacidade
operacional protegida, não evidência de operação concluída.

### Riscos e limites

Single-AZ aceita indisponibilidade zonal; Budget alerta com atraso e não é hard
cap. Não há banco AWS, task em execução, worker, usuário Cognito, provider real
habilitado e validado live ou frontend publicado na AWS. O fluxo de publicação e
autenticação está integrado e validado offline; bootstrap, domínio, certificado,
state e autorizações continuam externos ao módulo.

### Explicação curta para a banca

A evidência atual prova que o desenho gera um plano privado e auditado e que os
gates recusam ampliações conhecidas. Ela não prova que a AWS aceitou o plano ou
que uma jornada remota funcionou; essa distinção evita confundir IaC com
operação.

## SEN-10 — Documentação, apresentação e entrega

### Problema e objetivo

A entrega precisa ser executável e estudável por quem não acompanhou o
desenvolvimento, com uma apresentação curta que relacione problema, decisão,
evidência e limite sem depender de improviso ou nuvem disponível.

### Decisões técnicas e por que foram escolhidas

- O README é a entrada; índice, ADRs, cards e relatórios aprofundam temas sem
  duplicação extensa. Links relativos mantêm a navegação válida em clone.
- Afirmações públicas usam estados de evidência explícitos. Isso impede que um
  plano, fake ou estimativa seja apresentado como execução real.
- Quickstart, smoke, golden set e contingência offline dão um caminho
  reproduzível sem credenciais ou materiais restritos.
- Este relatório organiza as dez épicas como mapa de estudo e aponta para a
  prova, em vez de substituir testes, cards ou runbooks.

### O que foi realmente entregue

**Implementado:** README principal, índice técnico, diagramas, quatro ADRs,
data/model/RAG cards, threat model, referência da API, guias local/AWS e
relatórios de validação. O [índice de documentação](../README.md), o
[guia de scripts](../../scripts/README.md), os
[diagramas](../architecture/diagrams.md) e o
[threat model](../security/threat-model.md) formam a navegação canônica.

O [roteiro final](demo-script-base.md) organiza três jornadas reproduzíveis:
condição normal, prescrição governada e revisão humana com falha segura. Ele
também registra contingência offline, pontos de corte, perguntas da banca e uma
matriz auditável de afirmações.

**Testado:** formatação, whitespace e auditoria pública usam gates existentes nas
tarefas que produziram os documentos. Na SEN-81, os 33 links relativos deste
relatório foram checados pontualmente; isso não constitui um gate automatizado
de links.

### Evidências, pendências e atualização final

**Concluído nesta rodada:** a SEN-70 está `Done` e foi integrada pelo
[PR #59](https://github.com/HiRenan/senai-prescriptive-maintenance/pull/59) no
commit `5273165a28613c987c1a4411bcacddf67cc894f5` de `develop`.

**Medido historicamente no ensaio técnico da SEN-70:** as 13 falas-base somam
480 palavras. A voz sintética Microsoft Maria em português, com `Rate = -2`,
levou 403,631 s; o Chromium com setup aprovou 14/14 cenários em 9,904 s. A soma
sequencial foi 413,535 s, ou 06:53,535, sem cortes. Essa medição avalia o
artefato: não foi uma fala de Renan e não mede perguntas ou interrupções da
banca.

**Concluído após a SEN-70:** a auditoria pública final foi integrada pelo
commit `2d762e0` e a promoção estável foi concluída. A release final está em
`main` no commit `601c26717de021bc424bf4b3e310078cee3d7186`, após os checks de
CI, segurança e política de promoção.

### Riscos e limites

Documentação pode ficar defasada quando uma mudança integra depois da fotografia
usada. O relatório não substitui a execução das evidências citadas, e a ausência
de deploy AWS concluído deve continuar explícita na apresentação.

### Explicação curta para a banca

Use cada seção como roteiro “problema → escolha → prova → limite”. Quando uma
pergunta exigir detalhe, abra a fonte relativa; quando a evidência ainda for
pendente, responda como pendência, não como promessa já cumprida.
