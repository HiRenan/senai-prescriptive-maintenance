# Matriz de falha segura e reprodutibilidade pública

- Data: 2026-08-23
- Escopo: SEN-66
- Branch: `test/sen-66-validate-failure-matrix`
- Base validada: `origin/develop` em
  `c3dc6f63667b199525f3e405baabe98de0a78811`

## Objetivo e limites

Esta prova reúne as regressões P0/P1 já existentes e acrescenta somente a
lacuna de auditoria dos objetos Git públicos. Todos os cenários usam dados
sintéticos e execução offline; não há leitura de arquivos locais ignorados,
AWS, credenciais, provider pago, LLM real ou interface web.

## Matriz verificável

| Risco | Resultado seguro | Regressões selecionadas |
| --- | --- | --- |
| Banco obrigatório indisponível ou persistência falha | readiness `503` e falha de análise sanitizadas, sem publicação de cache | `test_required_dependency_failure_changes_only_readiness_and_is_sanitized`, `test_persistence_failure_is_classified_and_never_publishes_cache` |
| Timeout, ocupação ou exceção do provider | `degraded`, uma tentativa no máximo e nenhuma prescrição fabricada | `test_timeout_busy_late_completion_and_slot_release_are_bounded`, `test_provider_exception_degrades_without_leaking_or_retrying` |
| OCR falho ou resposta OCR inválida | erro tipado/sanitizado e página sem conteúdo fabricado | `test_sanitizes_engine_failures`, `test_malformed_ocr_text_is_a_sanitized_page_failure` |
| Documento sintético inválido | rejeição `document.pdf_unreadable` sem publicar seu conteúdo | `test_unreadable_pdf_is_rejected_without_publishing_its_content` |
| Prompt injection documental | instruções permanecem dados dentro do envelope não confiável | `test_document_instructions_and_sentinel_collisions_remain_only_data` |
| Citação inventada ou versão documental rejeitada/obsoleta | recusa tipada; versão rejeitada não alcança o provider | `test_fake_provider_is_blocked_without_current_approved_evidence`, `test_pre_provider_currentness_failures_are_total_and_skip_provider`, `test_snapshot_changed_during_provider_call_is_refused_post_provider` |
| Entrada HTTP inválida | `422` antes de modelo, recuperação ou geração | `test_invalid_top_k_returns_sanitized_422_before_internal_ports`, `test_invalid_feature_values_return_422_before_internal_ports` |
| Nome ou conteúdo protegido em Git | auditoria falha sem imprimir nome, hash, caminho ou bytes | `test_public_repository_audit.py` |

O marcador Pytest `failure_matrix` agrega essas regressões sem copiar sua
lógica. A interface canônica executa a seleção e, em seguida, a auditoria Git:

```powershell
uv run --frozen poe failure-matrix
```

O `poe check` continua executando a suíte completa com cobertura; a matriz é
uma seleção rápida e explícita, não uma substituição da validação agregada.

## Auditoria pública sanitizada

`scripts/public_repository_audit.py` carrega somente as identidades do
manifesto público, mantém nomes e fingerprints fora de representações e produz
apenas contagens agregadas. A auditoria:

- recusa repositório shallow;
- recusa índice com estágio diferente de zero;
- percorre o índice atual e os objetos alcançáveis por `HEAD`, referências
  remotas de `origin` e tags;
- rejeita um basename protegido mesmo com conteúdo diferente;
- calcula SHA-256 dos blobs Git para rejeitar conteúdo protegido renomeado,
  inclusive quando ele só permanece no histórico;
- não percorre a árvore de trabalho e, portanto, não abre arquivos ignorados;
- retorna somente código estável em falha, sem o valor que acionou o gate.

A CI executa `poe failure-matrix` em checkout com histórico completo no job
Ubuntu. O Gitleaks permanece responsável pela classe separada de segredos no
workflow `Security`, com histórico completo e saída pública desabilitada.

## Protocolo clean-room

O clone deve nascer de HTTPS em diretório temporário vazio. Nenhum arquivo
ignorado, ambiente virtual, cache, `.env` ou dado da worktree de implementação
é copiado. A sequência mínima é:

```powershell
uv lock --check
uv run --frozen poe setup
uv run --frozen poe failure-matrix
uv run --frozen poe check
uv run --frozen poe hooks
uv run --frozen poe smoke
uv run --frozen poe golden-e2e
```

O estado rastreado deve ser comparado antes e depois dos comandos somente
leitura. O smoke padrão valida a configuração offline e o Compose, mas não
inicia serviços; nenhuma execução AWS integra este protocolo.

## Evidência executada na worktree

Todos os comandos abaixo foram executados em Windows, com Python 3.13.5,
Gitleaks 8.30.1, Node.js 22 e pnpm 10.15.1:

| Comando | Resultado observado |
| --- | --- |
| `uv run --frozen poe failure-matrix` | 30 casos e auditoria sanitizada aprovados, com oito identidades protegidas e histórico completo |
| `uv run --frozen poe check` | Ruff format/check e Pyright aprovados; 1.132 testes aprovados, 43 skips de integrações opcionais e cobertura total de 80,56% |
| `uv run --frozen poe hooks` | todos os 11 hooks aprovados |
| `uv run --frozen poe smoke` | runtimes, pacote, configuração explícita, Compose, liveness e readiness offline aprovados |
| `uv run --frozen poe golden-e2e` | cinco estados, ciclo documental e três probes de segurança aprovados com fake local |
| `uv lock --check` e `corepack pnpm install --frozen-lockfile` | locks aceitos sem atualização |
| `gitleaks git . --redact` | histórico aprovado sem achado |
| `gitleaks git . --pre-commit --redact` | diff aprovado sem achado |

A varredura bruta `gitleaks dir . --redact` não foi declarada aprovada: ela
retornou 33 achados, todos classificados em caminhos ignorados, com zero no
diff e zero em arquivos rastreados. O relatório temporário foi zerado depois da
classificação. O resultado do clone HTTPS público do head publicado é registrado
no pull request e no handoff, pois essa prova só pode ocorrer depois do push.

## Limitações explícitas

- A auditoria de materiais detecta igualdade exata por nome ou SHA-256; ela não
  tenta classificar conteúdo derivado ou semanticamente semelhante.
- O scanner de segredos e a auditoria de materiais são controles diferentes;
  aprovação de um não implica aprovação do outro.
- A auditoria final pós-frontend permanece no escopo da SEN-71.
