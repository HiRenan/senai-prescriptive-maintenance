# Orquestração prescritiva — SEN-59

## Decisão implementada

A SEN-59 adiciona um caso de uso interno e puro entre o resultado do modelo e os
guardrails RAG já existentes. `PrescriptionOrchestrationService` recebe um
`ModelPrediction`; ele não executa o modelo, não conhece HTTP e não persiste o
resultado. A SEN-46 passou a compor essa fronteira pela API sem mover execução
do modelo ou persistência para dentro dela.

O resultado do modelo é copiado e revalidado na entrada. Disposição, forma da
abstenção, diagnóstico público, suporte finito, identidade do modelo, vizinhos
opacos ordenados e chave canônica de recuperação precisam ser coerentes. O
diagnóstico público permanece intacto no resultado. A chave de recuperação
identifica a classe documental no contrato de geração e não reescreve o código
público produzido pelo modelo.

Somente a combinação abaixo pode alcançar `RagGuardrailService`:

1. disposição exata `FAULT`;
2. chave documental canônica;
3. recuperação governada válida no estado `EVIDENCE`;
4. relógio monotônico válido antes do gate.

`NORMAL`, `OUT_OF_DISTRIBUTION`, falha sem chave, ausência de evidência e classe
não mapeada encerram sem provider. Falha técnica de recuperação encerra como
`degraded`. A composição não repete filtros de lifecycle, score, integridade,
schema, citações ou currentness: esses invariantes continuam pertencendo às
fronteiras SEN-56, SEN-57 e SEN-58.

No construtor, a orquestração obtém um snapshot defensivo do binding efetivo da
recuperação. A policy vem do `GovernedKnowledgeRetrievalService` e a identidade
do mapeamento vem da `FaultKnowledgeMapping` realmente carregada pelo serviço
aprovado. Cada resultado recuperado é comparado novamente antes do provider;
divergência de policy ou mapeamento degrada sem chamada.

## Estados e preservação de contexto

O contrato fecha quatro estados: `generated`, `skipped`, `refused` e
`degraded`. Cada saída não gerada contém um código, uma mensagem e uma próxima
ação fixos. A matriz do próprio resultado rejeita combinações impossíveis, como
geração para disposição normal, metadados sem guardrail, motivo de timeout sem
tentativa ou estado de provider tratado como recusa comum.

Para toda predição válida, inclusive indisponibilidade, timeout e recusa
pós-provider, permanecem acessíveis:

- disposição do modelo;
- diagnóstico público, exceto na abstenção OOD;
- suporte heurístico, sem tratá-lo como probabilidade;
- `model_id`;
- vizinhos content-free.

Snapshot documental com conteúdo, prompt e output bruto nunca entram no
resultado. Uma trace content-free guarda apenas policy, mapeamento e referências
opacas das evidências recuperadas para a auditoria da integração.
As ações e justificativas aceitas continuam nos contratos estruturados da
geração, com suporte documental, citações e warnings. “Seguro” significa aqui
que os gates estruturais foram satisfeitos; não significa garantia semântica ou
autorização operacional para executar manutenção.

## Timeout e concorrência

O provider atual é síncrono e sua porta não oferece cancelamento cooperativo. A
menor política defensável usa um `BoundedSemaphore(1)` por instância:

1. o slot é adquirido de forma não bloqueante antes de criar a thread;
2. uma thread daemon executa exatamente uma chamada, sem retry;
3. o caller espera somente o timeout explícito, maior que zero e no máximo 120
   segundos;
4. em timeout, o caller recebe `provider_timeout`, mas o slot só é liberado pelo
   `finally` da execução tardia;
5. enquanto ela permanece ativa, novas solicitações recebem `provider_busy` sem
   criar thread, fila ou segunda chamada;
6. resposta ou exceção tardia é descartada e não altera o resultado devolvido.

Assim, cada instância possui no máximo uma chamada subjacente em voo e não há
crescimento ilimitado de threads órfãs. O custo residual é explícito: se o
provider nunca retornar, a instância ficará ocupada e deverá ser substituída
depois que a dependência for tratada. O timeout cobre a fronteira do provider;
recuperação e currentness precisam aplicar seus próprios limites nos adapters.

Não existe cache, single-flight global, retry ou efeito persistente nesta
tarefa. Repetir o caso de uso é uma nova decisão explícita do caller. Como não há
escrita de domínio, não há efeito persistente a deduplicar.

## Metadados auditáveis

Uma tentativa que recebeu `ProviderResponse` válida registra apenas:

- `prompt_id=prescriptive-generation-system.v2`, vindo do recurso realmente
  carregado;
- `provider_id` estável e validado, fornecido pela configuração explícita;
- latência em milissegundos, derivada de relógio monotônico injetado;
- `ProviderUsage` copiado e revalidado.

A latência abrange o gate inicial de currentness, a chamada ao provider e a
revalidação final; ela mede a fase guardada completa, não o tempo puro da rede ou
do modelo. O timeout permanece restrito à chamada síncrona do provider.

Os contadores permanecem disponíveis quando o provider respondeu com envelope
válido, mas o output declarou evidência insuficiente ou falhou no gate
pós-provider. Erro, timeout, envelope inválido e uso malformado não inventam
contagens. Valores de relógio booleanos, inteiros, não finitos ou regressivos,
assim como delta ou conversão para milissegundos que transborde, encerram como
`timing_unavailable` e descartam a geração.

## Segurança e limites

Documentos continuam confinados ao envelope não confiável da SEN-58. A
orquestração não registra logs e mantém diagnóstico, guardrail e narrativas fora
do `repr`; metadados aceitam somente identificadores, latência e inteiros de
uso. Exceções comuns de recuperação, currentness, relógio e provider são
classificadas sem transportar mensagem, caminho, token ou objeto original.
`KeyboardInterrupt` e `SystemExit` do caller não são engolidos; somente a thread
daemon contém `BaseException` do provider para sinalizar falha segura e garantir
a liberação do slot em `finally`.

Os testes são inteiramente sintéticos e offline. Eles cobrem estados sem chamada,
IDs e subclasses hostis, mutação de contratos, output sem citação, erro e
desabilitação, relógio não monotônico ou transbordado, uso malformado, repetição,
corrida concorrente, timeout seguido de `busy`, descarte de conclusão tardia,
liberação do slot, binding real e divergência antes do provider. O
`FakeGenerationProvider` existente permanece o fake oficial;
nenhum SDK, credencial, rede ou chamada Bedrock live é usado.

A segunda revalidação de snapshots reduz TOCTOU, mas não cria transação ou lease.
Uma mudança depois dela ainda depende de coordenação operacional futura. Os
gates também não comprovam que toda frase aceita é semanticamente sustentada
pelo documento; uma avaliação humana e regras operacionais continuam
necessárias antes de qualquer ação real.
