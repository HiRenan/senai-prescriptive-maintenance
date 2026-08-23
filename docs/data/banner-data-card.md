# Data card — pipeline tabular canônico

- Responsável: Renan Mocelin
- Data de referência: 2026-08-23
- Dataset: `a0c1a7c5141b9b3a8856ad9af458fe09baa7fa04f6b96316ecb52a6d6b426327`
- Status: demonstração local; não aprovado para decisão industrial

## Finalidade e decisão de uso

O dataset canônico transforma uma fonte tabular autorizada em partições
temporais reproduzíveis para avaliação local. Ele serve a testes, análise
exploratória controlada e à baseline k-NN com revisão humana.

Não deve ser usado para autorizar manutenção, estimar desempenho de produção,
treinar decisões autônomas ou inferir cobertura de classes futuras. O
[model card](../model-cards/temporal-knn-v2.md) registra a decisão de não
aprovar o modelo avaliado para automação.

## Proveniência e composição

A fonte permanece local, somente leitura e fora do Git. O pipeline exige
caminhos explícitos, valida tamanho e SHA-256 antes e depois do consumo e liga o
resultado ao manifesto, contrato, política, inventário e `uv.lock`. A CI usa
somente fixtures inteiramente sintéticas; elas não são amostras da fonte.

Resultados abaixo são **medições históricas** das duas execuções locais
independentes aprovadas na SEN-41, não nova medição desta consolidação:

| Recorte | Linhas | Ocorrências |
| --- | ---: | ---: |
| Total reconciliado | 166.796 | 568 |
| Treino | 116.882 | 145 |
| Validação | 25.146 | 26 |
| Teste | 24.768 | 397 |
| Rejeitado / purga | 0 / 0 | — |

A entrada possui 26 colunas contratadas. As partições de modelagem contêm 18
features `float64` ordenadas e o target `y`, materializado somente depois das
decisões de qualidade, agrupamento e split. IDs, timestamps, metadados,
disposições e target não participam das features.

## Transformações e qualidade

- ocorrências usam somente ordem temporal, gap e duração, sem target;
- o split aproxima 70/15/15 sem cortar ocorrências e aplica purga nas
  fronteiras quando necessária;
- estatísticas IQR são ajustadas somente no treino;
- não há imputação, balanceamento ou scaling no pipeline;
- cada linha recebe uma disposição e um destino únicos, reconciliados pelo
  checker offline;
- os seis artefatos do build são determinísticos no runtime e lock avaliados.

Na execução medida, todas as 166.796 linhas receberam a disposição
`corrected` por inconsistência entre representações redundantes de unidade; as
colunas canônicas em °C e mm/s foram preservadas e as contrapartes redundantes
ficaram fora das features. Em 65.998 linhas também houve match IQR, mantido no
ledger sem remoção. A abrangência de 100% da correção exige revisão de domínio
antes de qualquer interpretação industrial.

## Privacidade, acesso e retenção

| Classe | Tratamento |
| --- | --- |
| Fonte e derivados por registro | Locais, ignorados, não redistribuíveis e nunca usados pela CI. |
| Modelo, índice e relatórios por registro | Locais e ignorados; sem pickle e sem publicação. |
| Manifesto e agregados aprovados | Públicos, sanitizados e insuficientes para reconstruir linhas. |
| Fixtures | Públicas e inteiramente sintéticas. |

Não há base pública para declarar a fonte livre de informação industrial
sensível ou de todo dado pessoal. Por isso ela e seus derivados são tratados
como restritos por padrão. O projeto não define prazo automático de retenção:
remoção e backup dos arquivos locais pertencem ao responsável pelo ambiente.
Não há licença de reutilização.

## Limitações e riscos

- um único fluxo temporal é conhecido; não há chave de ativo separada;
- a distribuição muda materialmente ao longo do tempo e introduz classes
  ausentes do treino;
- o target é categórico e exato, sem hierarquia ou equivalência semântica;
- `purge = 0` é resultado desta fonte, não desativação do controle;
- hashes e determinismo provam identidade, não correção de domínio;
- qualquer mudança de fonte, contrato, política, inventário ou lock exige novo
  build, nova identidade e nova avaliação.

## Evidência e reprodução

A metodologia, os hashes, os onze gates e as limitações completas estão no
[relatório do pipeline](../validation/data-pipeline.md). A política pública está
em [qualidade e outliers](banner-quality-policy.md), e a fronteira de
publicação está em [preparação dos materiais](../../data/README.md).

Os comandos `data-build` e `data-check` exigem a fonte autorizada e um destino
local já ignorado. A reprodução pública usa somente os testes sintéticos e
`uv run --frozen poe check`.
