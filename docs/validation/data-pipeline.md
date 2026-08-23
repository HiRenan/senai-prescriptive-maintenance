# Encerramento da EPIC 2 — pipeline canônico de dados

## Objetivo e escopo consolidado

Este relatório registra o estado implementado ao encerrar a EPIC 2 com a
SEN-41. A consolidação reutiliza as capacidades entregues por SEN-21 a SEN-27 e
SEN-30 sem ampliar o escopo para UI, treinamento de modelo, serving, ingestão
contínua ou documentos originais.

| Entrega | Capacidade consolidada na SEN-41 |
| --- | --- |
| SEN-21 | Toolchain local e fronteiras de dados ignorados pelo Git. |
| SEN-22 | Porta explícita, auditada e somente leitura para `banner.csv`. |
| SEN-23 | Catálogo e contrato estrito das 26 colunas de origem. |
| SEN-24 | Factory e cenários inteiramente sintéticos para testes e CI. |
| SEN-25 | Perfil determinístico e somente agregado. |
| SEN-26 | Baseline auditada, reconciliada e sanitizada. |
| SEN-27 | Normalização textual e inventário categórico de rótulos. |
| SEN-30 | Política versionada de qualidade, precedências e decisões. |
| SEN-41 | Projeção canônica, ledger, ocorrências, split temporal, purga, estatísticas de treino, artefatos determinísticos e checker offline. |

SEN-41 acrescenta uma configuração e um schema versionados, uma API Python, um
CLI e as tarefas Poe `data-build` e `data-check`. O build real permanece local:
nenhum original ou derivado privado é rastreado, e testes e CI usam somente
fixtures sintéticas.

## Decisões implementadas e justificativas

### Fonte e rastreabilidade

- `build_banner_dataset()` recebe todos os caminhos explicitamente e abre
  `banner.csv` apenas por `consume_banner_source_audited()`, com descritor não
  gravável e fingerprints antes e depois do consumo. Não há descoberta de
  arquivos ou caminho padrão.
- O inventário categórico, a baseline e o manifesto são validados antes da
  leitura da fonte. O `uv.lock`, a política de qualidade, o contrato, o schema,
  a configuração e o inventário ficam vinculados ao manifesto do dataset por
  hashes ou versões.
- Cada uma das 166.796 linhas recebe um `record_id`, uma disposição de qualidade
  e exatamente um destino entre `train`, `validation`, `test`, `purge` e
  `rejected`. Essa reconciliação evita perdas silenciosas.
- A escrita é atômica e fail-closed. Um destino existente só é aceito quando os
  seis arquivos são byte a byte idênticos; conteúdo diferente é recusado. Antes
  da escrita, destinos dentro de qualquer worktree Git precisam estar realmente
  ignorados. Erros do Git, escapes e componentes que sejam symlinks ou junctions
  também são recusados; temporários externos a worktrees continuam permitidos.

### Contrato das 18 features

O contrato de inferência preserva ordem, tipo `float64`, unidade canônica,
nulabilidade falsa, domínio físico e disponibilidade no instante do evento.
As 18 features são:

1. `z_rms_velocity_mm_s`;
2. `temperature_c`;
3. `x_rms_velocity_mm_s`;
4. `z_peak_acceleration_g`;
5. `x_peak_acceleration_g`;
6. `z_peak_vel_comp_freq_hz`;
7. `x_peak_vel_comp_freq_hz`;
8. `z_rms_acceleration_g`;
9. `x_rms_acceleration_g`;
10. `z_kurtosis`;
11. `x_kurtosis`;
12. `z_crest_factor`;
13. `x_crest_factor`;
14. `z_peak_velocity_mm_s`;
15. `x_peak_velocity_mm_s`;
16. `z_high_freq_rms_accel_g`;
17. `x_high_freq_rms_accel_g`;
18. `rpm`.

As cinco representações redundantes em °F ou `in/s` ficam fora das features.
Quando uma relação unitária não satisfaz a tolerância versionada, a contraparte
canônica em °C ou `mm/s` é mantida e o ledger registra a correção determinística,
sem editar a fonte e sem recalcular a feature pela coluna redundante.

Metadados, IDs, timestamps, disposições, referências documentais, partições e
target pertencem à denylist das features. Os três Parquets de modelagem têm
exatamente 19 colunas: as 18 features, na ordem congelada, seguidas por `y`.

### Separação temporal e leakage

O fluxo que decide ocorrências e partições não recebe o target:

1. valida o contrato e aplica apenas decisões de qualidade não estatísticas;
2. ordena registros elegíveis por timestamp e posição estável de origem;
3. itera agrupamento e fronteiras atômicas até estabilizar o limiar de gap,
   ajustando-o em cada iteração somente com os registros das ocorrências que
   pertencem ao treino final corrente, pelo maior valor entre cinco vezes a
   mediana e o percentil 95;
4. agrupa ocorrências apenas por ordem temporal, gap estritamente maior que o
   limiar e duração estritamente menor que 24 horas: um registro em exatamente
   86.400 segundos desde o início abre uma nova ocorrência;
5. escolhe fronteiras cronológicas 70/15/15 por linhas, sem cortar ocorrências;
6. purga ocorrências completas na cauda da partição anterior quando a fronteira
   não respeita o mesmo limiar de gap;
7. ajusta as cercas IQR somente com registros efetivamente destinados a treino
   e aplica apenas flags, sem remoção ou imputação;
8. somente depois dessas decisões resolve o rótulo exato no inventário e o
   materializa como `y`.

Não há scaling nem imputação. O target só é materializado depois da partição,
como `y`, e não influencia elegibilidade, duplicidade, ajuste, ordem,
agrupamento, limite de ocorrência, split, purga ou estatística. Um teste
sintético altera labels dentro de uma sequência curta e comprova que os mesmos
registros permanecem na mesma ocorrência e no mesmo destino; nenhuma ocorrência
atravessa partições. Na execução real, uma ocorrência contém mais de um target,
sem ter sido dividida por essa mudança.

## Artefatos e critérios de qualidade

Cada destino de build contém exatamente:

- `canonical.parquet`: linha canônica pós-partição, metadados de auditoria, 18
  features e `y`;
- `dispositions.parquet`: ledger completo de disposição, motivos, matches,
  transformações e destino;
- `train.parquet`, `validation.parquet` e `test.parquet`: somente 18 features e
  `y`;
- `manifest.json`: identidades, hashes, fit, contagens, reconciliações e gates.

`data-check` é somente leitura e exige o conjunto exato de arquivos, schemas e
tipos, schema profundo e sem campos extras para todo o manifesto, serialização
canônica, hashes físicos e lógicos, componentes vigentes, recomputação do fit
temporal e IQR de treino, referências cruzadas, cobertura única e integral dos
destinos versionados, ID do dataset e os onze gates abaixo:

- ledger com destino único e cobertura integral;
- cobertura canônica de todos os elegíveis;
- alinhamento entre ledger e partições;
- ocorrências disjuntas entre destinos;
- projeção exata dos Parquets de modelagem;
- ordem temporal estrita entre partições;
- gap de purga preservado nas fronteiras;
- isolamento das 18 features;
- três partições não vazias;
- estatísticas ajustadas somente em treino;
- target independente de fit, grupo e split.

## Validação sintética e agregada

- Testes direcionados do pipeline e CLI: 27 passaram e 1 teste condicional de
  symlink foi ignorado no Windows. Eles cobrem projeção das 18 features,
  determinismo, checker read-only, mudança de label dentro de uma ocorrência,
  cenário atômico 10/10/40 sem IDs de validação ou teste no fit, fronteira exata
  de 24 horas, isolamento de `y`, purga sintética, destino `shadow`, faltas,
  duplicações e divergência entre disposição e destino, adulterações
  coerentemente resseladas do manifesto, segurança do destino, saída sanitizada
  e códigos de saída estáveis.
- `uv run --frozen poe check`: Ruff format-check e lint passaram, Pyright estrito
  registrou zero erros e Pytest concluiu 604 testes aprovados, 3 skips esperados
  e cobertura total de 84,96%, acima do gate de 80%.
- Nenhum teste ou comando de CI abre a fonte privada; todos os cenários públicos
  são sintéticos.

## Duas execuções reais independentes

Os destinos `data/processed/sen-41-rebase-a` e
`data/processed/sen-41-rebase-b` foram confirmados por `git check-ignore`; o
próprio build repetiu essa prova antes da escrita. Em cada destino, `data-build`
e depois `data-check` passaram. Os dois manifestos são idênticos e os seis
arquivos têm o mesmo tamanho e SHA-256.

- Dataset ID:
  `a0c1a7c5141b9b3a8856ad9af458fe09baa7fa04f6b96316ecb52a6d6b426327`.
- Configuração do pipeline:
  `a6fedfcc23320e72954a783c0499d9b19ba785f6b9a462f67451bbe531dd72d7`.
- Schema dos artefatos:
  `9c8fc35a1b63b2f5aa4c90b85a2b164a309768a486deb81b833196fb1fe3bbcd`.
- Política de qualidade:
  `51190f2c6662c2ffb236d887f4bb43f1f7cb03f98dfc387441cc5410dcd3838b`.
- Inventário categórico:
  `aabf85c066bcf12fb8b27c4bb6ab7fce601dfc1512b64f762c42bab24b98aa78`.
- `uv.lock`:
  `8739eb081aa6c1785b2b231ed9cc43f959b62de81995bf02a263a9a7a2be9b32`.
- Cercas IQR de treino:
  `5b25b6adeafbb0d065ae29a1fd788fc055383825cfe80f440c4dd400ca5b7bdb`.
- Pertencimento do fit temporal, restrito a 116.882 registros das 145
  ocorrências finais de treino:
  `9b957316fe916cd67ef93d1d21ff17b698b3fe19d42b4cebee3d3d46beb90200`.

| Derivado | Linhas | Colunas | SHA-256 físico | SHA-256 lógico |
| --- | ---: | ---: | --- | --- |
| `canonical.parquet` | 166.796 | 26 | `f0a39f177c8edd15b71616161a687d888521e10526397694ba6e2bef60413b96` | `b4c98c2549a673ba17db2bef83e62861231e391e67b6b4358d3033dc64f0a7ed` |
| `dispositions.parquet` | 166.796 | 7 | `bfb33e61a4d059792549c1598531bc3b4bb4bd498e1ff0d3d74ea1bdce3c430c` | `f05549141ef1ebe6872be98b6f42de06c20d8150401f25d01a8681a2d583feea` |
| `train.parquet` | 116.882 | 19 | `5cd162f27afff80191374ee008349a4cc29ec3f89ce6bdf760d99e277d3662f6` | `a8428f851da34a38b811bb41fa066f5a4ae985a305dd595df0e9cbdc3af7abd1` |
| `validation.parquet` | 25.146 | 19 | `9dc026744712d8ab005a15a1c4c5f20e00de9af16c3a51c437809f327c558693` | `66de1b607cf2cbca59e0e8541761070ea377e2ffc9e10400b6f2ff6e65a89a0d` |
| `test.parquet` | 24.768 | 19 | `7f5bfec103f85e8a481cbbe7d2468c208fd2759ec29aea48f84e023830e1e993` | `0476d131b1e3d889da4d32039d3c60aa8f45c62e55fe12acea60c03a293b0414` |
| `manifest.json` | — | — | `b9b68de563430410c5f2dcdd51fb5c1a2a065accd07571eb76c5a97a5565023c` | incorporado ao dataset ID |

Na validação do hardening, em relação à primeira execução da SEN-41, contagens,
fronteiras, ledger e os três Parquets de modelagem permaneceram idênticos.
Mudaram, como esperado, o ID da configuração, o dataset ID, o manifesto e
`canonical.parquet`: o canônico contém IDs de ocorrência vinculados à
configuração versionada, enquanto o manifesto passou a registrar escopo,
contagens e hash de pertencimento do fit final de treino. Não houve mudança de
linhas entre partições.

Após o rebase sobre a SEN-43, o novo `uv.lock` alterou apenas o dataset ID e os
bytes do manifesto. Configuração, schema, política, inventário, pertencimento
do fit, contagens e hashes físicos e lógicos dos cinco Parquets permaneceram
idênticos. Os derivados vinculados ao lock anterior foram corretamente
recusados como desatualizados pelo checker antes das novas execuções A/B.

### Contagens e fronteiras sanitizadas

- Fonte, canonical e ledger: 166.796 linhas em cada reconciliação.
- Ocorrências: 568 no total.
- Treino: 116.882 linhas em 145 ocorrências.
- Validação: 25.146 linhas em 26 ocorrências.
- Teste: 24.768 linhas em 397 ocorrências.
- Rejeitadas: 0; purge: 0.
- Disposição final: 166.796 `corrected`; as demais disposições têm contagem 0.
- Registros com match unitário determinístico: 166.796.
- Registros que também acumulam ao menos um match IQR: 65.998.
- Registros com match de duplicidade: 0.
- Registros cujo target foi mapeado pós-partição: 166.796.
- Limiar de gap ajustado somente nas 145 ocorrências finais de treino:
  10,000575 segundos; nenhum ID de validação ou teste participa do fit.
- Maior gap dentro de uma ocorrência: 6,000447 segundos.
- Maior duração observada de uma ocorrência: 3.998,247042 segundos, abaixo
  do limite de 24 horas.
- Gaps treino–validação e validação–teste: 24,440238 e 119,827572 segundos,
  ambos acima do limiar.
- Todos os onze gates do manifesto têm valor `true` nas duas execuções e nos
  dois checkers.

A fonte autorizada tinha 32.321.076 bytes antes e depois das execuções. Tamanho
e mtime permaneceram idênticos; em cada build, os fingerprints SHA-256 pre/post
do adaptador também coincidiram. Nenhum conteúdo, linha ou rótulo da fonte foi
registrado nesta validação.

## Limitações e riscos conhecidos

- `corrected = 166796` decorre de todas as linhas apresentarem ao menos uma
  inconsistência entre representações redundantes. A solução confia nas colunas
  canônicas já expressas em °C e `mm/s`, exclui as contrapartes redundantes e
  preserva os matches no ledger. É uma decisão determinística e auditável, mas
  a abrangência de 100% deve ser revisada com especialista de domínio antes de
  usar o dataset para conclusões industriais.
- `purge = 0` não desativa a proteção. Os dois gaps naturais nas fronteiras
  excederam o limiar de 10,000575 segundos, então nenhuma ocorrência precisou
  ser removida. O caminho que efetivamente purga ocorrências é comprovado por
  fixture sintética; uma nova identidade de fonte pode produzir outra contagem.
- O split aproxima 70/15/15 por número de linhas e nunca corta ocorrências.
  Como os tamanhos das ocorrências variam, as quantidades de ocorrências por
  partição não seguem essas proporções; isso explica 397 ocorrências no teste
  apesar de sua menor contagem de linhas.
- O contrato disponível define um único fluxo temporal e não fornece uma chave
  de ativo separada. Se uma futura fonte autorizada trouxer uma identidade de
  ativo validada, a semântica de agrupamento deverá ser versionada e reavaliada.
- O ajuste temporal depende de um ponto fixo entre agrupamento e fronteira final
  de treino. A fonte validada convergiu para 10,000575 segundos e reproduziu as
  fronteiras anteriores; uma fonte futura que oscile ou exceda o limite de
  iterações será recusada sem publicar derivados.
- As 65.998 linhas com match IQR não são removidas. A precedência `corrected`
  domina `flagged`, mas todos os motivos continuam no ledger; consumidores não
  devem interpretar a disposição final isoladamente como ausência de outliers.
- O mapeamento de target é categórico e exato, sem equivalência semântica. Um
  rótulo fora do inventário bloqueia o build.
- Não há imputação, scaling, engenharia adicional, balanceamento de classes,
  treinamento, avaliação de modelo, persistência, serving, UI ou automação de
  ingestão nesta entrega.
- Parquet físico é determinístico no runtime e lock validados. Mudanças de
  versão, configuração, schema, política, inventário, lock ou fonte alteram os
  IDs/hashes e exigem novo build e nova validação.

## Próximos passos

1. Obter revisão de domínio para a decisão de confiar nas colunas canônicas e
   para a interpretação das inconsistências unitárias presentes em 100% das
   linhas.
2. Fazer tarefas de modelagem consumirem somente os Parquets de partição,
   tratando as 18 primeiras colunas como `X` e `y` exclusivamente como target.
3. Registrar métricas de modelo separadamente por treino, validação e teste sem
   reusar teste em ajuste ou seleção de hiperparâmetros.
4. Reexecutar baseline, inventário, build A/B e checker sempre que houver nova
   identidade autorizada da fonte ou mudança versionada de contrato/política.
5. Manter todos os Parquets reais em destinos locais ignorados; somente
   agregados sanitizados podem integrar documentação pública.

## Pontos para defesa na banca

- **Privacidade por construção:** a fonte é lida por uma única porta auditada;
  derivados reais ficam ignorados e CI usa apenas dados sintéticos.
- **Reprodutibilidade verificável:** duas execuções independentes produziram o
  mesmo dataset ID e os mesmos hashes físicos e lógicos dos seis arquivos.
- **Sem perda silenciosa:** as 166.796 linhas reconciliam entre fonte, canonical
  e ledger, cada uma com disposição e destino únicos.
- **Leakage evitado estruturalmente:** ordenação, elegibilidade por duplicidade,
  fit, agrupamento e split usam representações sem target; `y` só é resolvido
  depois dessas decisões. O hash integral permanece restrito à identidade de
  auditoria e não participa dos limites temporais.
- **Tempo preservado:** ocorrências inteiras, ordem cronológica estrita e gaps
  de fronteira maiores que o limiar impedem janelas correlacionadas entre
  partições.
- **Qualidade explicável:** decisões, matches concorrentes e transformações são
  preservados no ledger, inclusive quando a precedência produz uma única
  disposição final.
- **Falha fechada:** contrato, inventário, hashes, schemas, componentes,
  reconciliações e gates bloqueiam a publicação local quando divergem.
- **Escopo controlado:** a entrega prepara dados canônicos; não afirma ter
  treinado, avaliado ou servido um modelo e não implementa interface.
