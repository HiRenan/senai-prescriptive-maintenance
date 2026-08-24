# Runtime configurável de análise — SEN-79

- Responsável: Renan Mocelin
- Data de referência: 2026-08-23
- Estado: implementado e validado com artefatos 100% sintéticos

## Decisão

A aplicação exige `PRESCRIPTIVE_MAINTENANCE_ANALYSIS_MODE` e aceita somente
dois valores mutuamente exclusivos:

| Modo | Composição | Referências externas |
| --- | --- | --- |
| `synthetic_demo` | factory determinística já existente | proibidas |
| `artifacts` | modelo k-NN, índice versionado, recuperação governada, orquestração e UoW reais | manifesto e SHA-256 obrigatórios |

Não há valor padrão, descoberta por diretório nem fallback. Compose e o perfil
AWS declaram `synthetic_demo` visivelmente. `artifacts` existe para configuração
local aprovada; nenhum derivado privado é publicado no repositório.

## Startup e readiness

Há duas classes de falha deliberadamente distintas:

1. configuração estrutural ausente, extra ou contraditória impede o startup com
   `ApplicationStartupError` sanitizado;
2. manifesto `artifacts` ausente, corrompido ou incompatível mantém o processo
   vivo, mas não configura o serviço de análise. A liveness responde 200, a
   readiness responde 503 e `POST`/`GET` de análise respondem 503.

Essa semântica mantém uma evidência operacional consultável da indisponibilidade
sem colocar a instância no tráfego e sem converter a falha em
`synthetic_demo`. As respostas nunca incluem caminho, conteúdo, label privada,
prompt, segredo ou texto da exceção. `X-Analysis-Mode` publica somente o valor
fechado configurado e preserva os modelos HTTP v1.

## Âncora de autorização

`artifacts` exige o modo e as duas referências abaixo:

```powershell
$env:PRESCRIPTIVE_MAINTENANCE_ANALYSIS_MODE = "artifacts"
$env:PRESCRIPTIVE_MAINTENANCE_ANALYSIS_ARTIFACTS_MANIFEST = `
  "C:/approved-local/analysis-runtime.json"
$env:PRESCRIPTIVE_MAINTENANCE_ANALYSIS_ARTIFACTS_MANIFEST_SHA256 = `
  "<sha256 minúsculo aprovado>"
```

O caminho é local, explícito e ocultado de representações. O SHA-256 externo
autoriza os bytes exatos do manifesto. Referências internas usam somente paths
POSIX relativos ao diretório do manifesto, sem `..`, links ou descoberta. O
manifesto de schema 1 vincula:

| Grupo | Vínculos obrigatórios |
| --- | --- |
| autorização | versão, SHA-256 semântico e `configuration_id` |
| política operacional | SHA-256 do payload versionado da SEN-78 |
| modelo | hash físico do manifesto, dataset, modelo, conteúdo e partição de treino |
| índice | hash físico do manifesto, schema, índice, conteúdo, contagem e modelo-fonte |
| mapping | hash físico, versão e SHA-256 semântico |
| recuperação | versão, score mínimo e SHA-256 semântico |
| projeção | versão, prioridades por fault code público e SHA-256 semântico |
| geração | prompt/arquivo revisado, provider fake explícito e timeout |
| documentos | hash de cada extração derivada, versão, IDs, hash-fonte e chunks exatos |
| indexação | configuração de chunking e identidade/dimensão do embedding |

O loader existente do k-NN verifica seus arrays e o loader do índice verifica
manifesto, arquivos, compatibilidade e vínculo com o modelo. As extrações
estruturadas são reindexadas pelo pipeline existente; todos os chunks precisam
estar íntegros e representados. O ciclo documental é reconstruído com auditoria,
gates de extração/indexação e aprovação ancorada pelo manifesto. O mapping é
validado contra esse ciclo, as classes problemáticas do modelo e a projeção de
prioridades antes de qualquer busca.

O provider permitido por este schema é `fake-generation.v1`. Isso prova a
composição e os guardrails sem rede ou credenciais, mas não prova qualidade
semântica e não habilita Bedrock. Um provider operacional exige decisão e
validação próprias; não é inferido do ambiente AWS.

## Persistência

Com `memory`, o composition root instala no store efêmero somente metadados e
referências dos documentos verificados. Com `postgres`, ele exige que os mesmos
documentos, versões e chunks já existam no banco e falha fechado quando a
dependência ou o vínculo diverge; a conexão inicial usa timeout de um segundo.
A análise persiste dataset, modelo, prompt,
configuração, outcome e referências de evidência; features e texto documental
não são gravados nessa trilha.

## Evidência sintética

`test_analysis_runtime.py` cria em diretório temporário um treino comum de 18
features, salva e recarrega o k-NN, constrói e consulta o índice real, reindexa
uma extração inteiramente sintética, aprova o documento, valida mapping e
políticas, executa `POST /analysis` e consulta o resultado. A prova confirma:

- ranking idêntico entre modelo e índice;
- somente o único chunk aprovado chega à citação pública;
- metadados e a referência exata são persistidos pela UoW;
- mapping sem cobertura produz `undocumented_fault`, sem prescrição, citação ou
  chamada ao provider;
- divergências de dataset, modelo, índice, política operacional, mapping,
  recuperação, projeção, prompt, provider, chunking, embedding, documento e
  autorização deixam o runtime indisponível;
- corrupção de manifesto, arrays, índice, mapping ou extração não aciona o demo.

## Smoke local opcional

Quando uma configuração local `artifacts` já está presente:

```powershell
uv run --frozen poe smoke --with-artifacts
```

O smoke compõe os derivados e imprime somente contagens agregadas de amostras,
registros do índice, documentos, chunks e classes mapeadas. Se a configuração
não estiver presente, registra explicitamente indisponibilidade/skip; se estiver
presente mas for inválida, o smoke falha. Ele não imprime paths, IDs, labels ou
conteúdo e não lê fontes originais.
