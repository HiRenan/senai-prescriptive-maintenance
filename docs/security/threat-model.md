# Threat model

- Responsável: Renan Mocelin
- Data de referência: 2026-08-23
- Baseline: `origin/develop` em `00af0e3`
- Estado: runtime local e perfil AWS apenas declarativo; sem produção

## Escopo e premissas

O modelo cobre o repositório público, processamento local, API FastAPI,
PostgreSQL/pgvector, composição de modelo/RAG, imagens OCI, CI e o perfil
Terraform AWS demo. O adversário pode enviar requisições hostis, adulterar
arquivos acessíveis à sua conta local, inserir instruções em documentos ou
tentar expor segredos e materiais pelo Git.

O runtime local não possui autenticação, autorização ou rate limiting e só é
aceitável em loopback ou rede controlada. O perfil AWS descreve JWT Cognito e
IAM, mas não foi aplicado; esses controles não protegem a execução local atual.

## Ativos e fronteiras de confiança

- materiais autorizados e seus derivados locais;
- credenciais, state, variáveis e configuração externa;
- identidades e integridade de dataset, modelo, índice, mapping e documentos;
- diagnóstico, evidência, prescrição e histórico de auditoria;
- disponibilidade da API, banco e provider;
- orçamento e privilégio da futura conta AWS.

As principais fronteiras são: repositório público ↔ filesystem local; cliente ↔
API; processo ↔ PostgreSQL; documento não confiável ↔ recuperação/RAG;
aplicação ↔ provider; GitHub OIDC ↔ AWS. Hash não concede confiança ao conteúdo:
ele prova somente identidade dos bytes esperados.

## Riscos P0/P1

| Prioridade e ameaça | Controles implementados | Risco residual / ação exigida |
| --- | --- | --- |
| **P0 — publicação de material protegido** | `.gitignore`, manifesto sem conteúdo, derivados em destinos ignorados, contexts OCI por allowlist e auditoria de nome/hash no histórico Git. | Derivação ou paráfrase não é detectada por igualdade. Revisar diff, executar auditoria pública e Gitleaks antes do push. |
| **P0 — segredo em Git, imagem ou log** | Exemplos fictícios, detecção de chave privada, Gitleaks, logs HTTP allowlisted e exclusão de `.env`/Git/dados dos builders. | Scanner não substitui rotação. Revogar imediatamente qualquer segredo exposto e auditar acessos. |
| **P0 — diagnóstico ou prescrição insegura** | Abstenção do modelo, cinco estados fechados, evidência governada, citações obrigatórias, recusa fail-closed e nenhuma composição real padrão. | O modelo medido não é aprovado e schema/citação não provam correção semântica. Revisão humana continua obrigatória. |
| **P0 — prompt injection ou citação fabricada** | Documento encapsulado como não confiável, mapping da classe exata, currentness pre/pós-provider e rejeição de citações fora do conjunto. | Não há prova de resistência semântica universal. Provider real exige avaliação adversarial antes de habilitar. |
| **P0 — adulteração de fonte ou artefato** | Fingerprint pre/post no mesmo descritor read-only, IDs/hashes determinísticos, conjunto fechado de arquivos, `allow_pickle=False` e checks cruzados. | Conta local comprometida pode alterar código e evidência em conjunto. Separar autoridade e preservar evidência externa em uso real. |
| **P1 — exposição da API sem controle de acesso** | Binds locais em `127.0.0.1`, containers não privilegiados e erros sanitizados. | Não há autenticação, autorização, TLS ou rate limiting local. Nunca publicar esse runtime; implementar controles antes de rede compartilhada. |
| **P1 — evidência obsoleta ou corrida TOCTOU** | Lifecycle com revisão CAS, aprovação vigente e revalidação do mesmo snapshot antes/depois do provider. | Mudança após a última conferência ainda é possível. Operação real exige transação, lease ou política equivalente. |
| **P1 — indisponibilidade e exaustão** | Readiness limitada, `top_k` e budgets fechados, uma chamada de provider por instância, timeout e ausência de retry/fila. | Provider travado retém o slot; API não tem quota. Substituir a instância e definir limites operacionais antes de carga real. |
| **P1 — vazamento ao provider externo** | Fake offline padrão; Bedrock lazy, desabilitado e sem descoberta de credenciais. | Habilitar provider transmite conteúdo autorizado. Revisar minimização, região, retenção, acesso e contrato previamente. |
| **P1 — abuso de IAM ou custo AWS** | Plan/deploy/teardown manuais, três roles OIDC, IAM por ação/recurso, Budget e plano auditado. | Nada foi validado live; Budget não interrompe gasto. Bootstrap, reviewers, teto e teardown precisam de prova autorizada. |
| **P1 — supply chain** | Locks congelados, bases/actions por digest ou SHA, CodeQL, Dependabot e revisão de dependências. | Dependência legítima ainda pode ser comprometida. Revisar atualizações e manter provenance/scan antes de release. |

## Falhas seguras observáveis

Banco obrigatório indisponível produz readiness `503`; entrada inválida encerra
antes das portas internas; falha, timeout ou ocupação do provider não fabrica
prescrição; documento rejeitado/obsoleto não alcança geração; OCR e PDF inválidos
produzem códigos sanitizados; falha de persistência não publica cache.

A matriz reproduzível está em
[falhas seguras](../validation/safe-failure-matrix.md). Esses testes usam apenas
dados sintéticos e execução offline.

## Verificação mínima

```powershell
uv run --frozen poe failure-matrix
uv run --frozen poe check
uv run --frozen poe hooks
uv run --frozen poe smoke
uv run --frozen poe golden-e2e
gitleaks git . --redact
```

Gitleaks deve usar saída redigida. A auditoria pública não percorre a árvore de
trabalho e, portanto, não abre arquivos ignorados.

## Gatilhos de revisão

Revisar este documento antes de adicionar autenticação, exposição de rede,
upload, novo dado, provider externo, artefato real autorizado, busca semântica,
worker, nova persistência, UI ou execução AWS. Também revisar após incidente,
mudança de trust boundary ou alteração relevante de retenção.

A política de reporte privado, segredos e resposta a vazamentos está em
[Política de segurança](../../SECURITY.md).
