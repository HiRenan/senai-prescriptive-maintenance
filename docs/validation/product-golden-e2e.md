# Golden set e jornadas ponta a ponta — SEN-48

## Objetivo entregue

O golden set `product-golden-synthetic.v1` é uma fixture JSON pequena,
versionada, determinística e inteiramente sintética. A execução usa a factory
FastAPI real em perfil `offline` com persistência em memória; não depende de
AWS, credenciais, rede, provider pago ou materiais originais.

O comando canônico é:

```powershell
uv run --frozen poe golden-e2e
```

Ele escreve em stdout um relatório JSON estável. Logs operacionais HTTP mantêm
somente a allowlist existente e não entram no relatório.

## Jornadas cobertas

As cinco requisições passam pelo `POST /analysis` e são consultadas novamente
por `GET /analysis/{analysis_id}`. A fixture fixa as 18 features, `top_k`, o
resultado esperado e o delta esperado de chamadas por camada.

| Estado | Modelo | Recuperação | Geração/provider |
| --- | ---: | ---: | ---: |
| `normal` | 1 | 0 | 0 |
| `documented_fault` | 1 | 1 | 1 |
| `undocumented_fault` | 1 | 1 | 0 |
| `out_of_distribution` | 1 | 0 | 0 |
| `degraded` | 1 | 1 | 1, com falha sintética controlada |

O ciclo documental usa `POST /documents` para registrar duas identidades. Os
gates de extração e indexação são concluídos por
`DocumentGovernanceService`, a fronteira de aplicação existente, e as decisões
retornam à API por `POST /documents/{id}/approve` e
`POST /documents/{id}/reject`. A consulta HTTP final precisa reproduzir os
estados `approved` e `rejected`.

## Evidência, provider e citações

A evidência aceita referencia exatamente o documento aprovado, sua versão
persistível e um chunk sintético. `RagGuardrailService` e
`FakeGenerationProvider` exercitam quatro condições verificáveis:

- evidência aprovada e vigente produz uma chamada aceita;
- ausência de evidência encerra com `no_evidence` e zero chamadas;
- evidência ligada ao documento rejeitado encerra com `stale_evidence` e zero
  chamadas;
- output que inventa um chunk encerra com `invalid_provider_output` após uma
  chamada, sem publicar a identidade inventada.

A jornada `documented_fault` falha se a citação HTTP não for exatamente a
evidência aprovada. Todas as outras citações também precisam ser vazias ou
pertencer ao mesmo conjunto aprovado.

## Métricas e rastreabilidade

O relatório separa `model`, `retrieval` e `generation`. Cada camada informa
tentativas, sucessos e erros; geração também informa tentativas e falhas do
provider fake. Essas contagens são métricas funcionais determinísticas, não
latência nem throughput.

Os bindings registram versões do contrato HTTP, features, modelo sintético,
adapter e policy de recuperação, contrato/prompt de geração, provider e
mapeamento, incluindo os hashes relevantes. A configuração registra perfil,
backend, `top_k` e modo offline. O relatório não contém features, RPM, nomes,
hashes de conteúdo documental, texto de evidência, prompt ou output bruto.

## Integração e limites

`poe test` executa a suíte funcional como parte de `poe check`. O CI também
chama `poe golden-e2e` explicitamente em Ubuntu e Windows para validar a
interface pública do harness em clone limpo.

O golden set demonstra comportamento e guardrails estruturais com fakes. Ele
não aprova o modelo para automação, não mede desempenho, não comprova correção
semântica de uma prescrição e não substitui avaliação humana ou testes de carga.
