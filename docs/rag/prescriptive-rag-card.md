# RAG card — composição prescritiva v1

- Responsável: Renan Mocelin
- Data de referência: 2026-08-23
- Contrato: `prescriptive-generation.v1`
- Prompt: `prescriptive-generation-system.v2`
- Status: integração sintética demonstrável; não aprovada para uso operacional

## Finalidade e decisão de uso

A composição liga um diagnóstico imutável a documentos governados para produzir
uma resposta estruturada com citações. Seu uso permitido nesta versão é testar
contratos, recusas e rastreabilidade com dados sintéticos e revisão humana.

Ela não comprova qualidade semântica, não autoriza manutenção e não pode ser
apresentada como RAG operacional. A factory HTTP padrão usa serviços
sintéticos; modelo, índice, mapping, documentos e provider reais só podem entrar
por injeção e autorização exatas.

## Fluxo implementado

```text
ModelPrediction
  -> recuperação governada da classe exata
  -> versões approved, vigentes e íntegras
  -> guardrail e revalidação pre-provider
  -> no máximo uma chamada ao provider
  -> schema, citações e currentness revalidados
  -> resultado público sanitizado ou recusa/degradação
```

Somente `FAULT` com chave documental, mapping válido e evidência elegível pode
alcançar o provider. `NORMAL`, `OUT_OF_DISTRIBUTION`, falta de mapping e ausência
de evidência encerram sem chamada. O diagnóstico do modelo não pode ser
substituído pelo texto recuperado.

## Contratos e limites

| Controle | Valor implementado |
| --- | --- |
| Evidências | até 12 itens |
| Conteúdo por evidência | até 4.000 caracteres |
| Conteúdo total | até 24.000 caracteres |
| Vizinhos / `top_k` | 1 a 10 |
| Output bruto do provider | até 64.000 caracteres antes da validação |
| Timeout configurável | maior que zero e no máximo 120 s |
| Concorrência por instância | um slot, sem fila e sem retry |

Documentos entram em envelope não confiável. O output só é aceito quando usa o
schema fechado, mantém o mesmo diagnóstico, fornece ao menos uma prescrição
para suporte positivo e cita apenas identidades recuperadas. As mesmas versões
são conferidas antes e depois da chamada. Resultado público e logs não contêm
texto documental, prompt montado ou output bruto.

## Providers e dados

`FakeGenerationProvider` é determinístico, offline e sem credenciais. Ele prova
o contrato, não qualidade de linguagem. `BedrockGenerationProvider` é um adapter
lazy com fábrica de cliente injetada; fica desabilitado por padrão, não descobre
credenciais e não é selecionado pela aplicação ou pelo perfil AWS
automaticamente.

Extrações, chunks, vetores e mappings reais permanecem locais e ignorados.
Referências públicas usam somente IDs opacos, versão, chunk, página, seção e
score. O conteúdo fornecido a um provider real pode ser sensível e exige revisão
de finalidade, região, retenção e contrato antes de qualquer habilitação.

## Evidência disponível

- **implementado:** contratos estritos, recuperação governada, guardrails,
  timeout limitado, integração e cinco estados públicos;
- **validado sinteticamente:** golden set com os cinco estados e prova dinâmica
  de chunk, indexação, lifecycle, ranking e citação; também há bloqueio
  pré-provider, citação inventada, evidência rejeitada/obsoleta, timeout,
  ocupação e falhas sanitizadas;
- **medido localmente:** benchmark sintético da API, separado por cenário e
  sem rede ou provider pago;
- **não medido:** groundedness semântico, precisão de recuperação, resistência
  geral a prompt injection, qualidade de prescrição, latência/custo de provider
  real e desempenho pgvector com documentos reais.

## Riscos residuais

- citações válidas estruturalmente não provam que cada frase é sustentada;
- conteúdo hostil pode influenciar um provider apesar do envelope e do prompt;
- a segunda conferência reduz, mas não elimina, a janela TOCTOU;
- um provider síncrono que nunca retorna mantém o único slot ocupado;
- indisponibilidade de mapping ou documento reduz cobertura;
- o modelo avaliado possui falso aceite open-set material e não é aprovado;
- habilitar rede ou provider amplia exposição de conteúdo, custo e superfície
  de supply chain.

Toda saída exige revisão humana. Uma habilitação operacional futura precisa de
avaliação semântica autorizada, critérios de groundedness, autenticação,
autorização, política de dados, observabilidade, limites de custo e resposta a
incidentes.

## Evidências relacionadas

- [integração da análise](../validation/analysis-integration.md);
- [orquestração prescritiva](../validation/prescription-orchestration.md);
- [pipeline documental](../validation/document-pipeline.md);
- [prova dinâmica de chunk e recuperação](../validation/dynamic-rag-e2e.md);
- [golden set](../validation/product-golden-e2e.md);
- [model card](../model-cards/temporal-knn-v2.md).
