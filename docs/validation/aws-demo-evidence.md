# Validação offline do perfil AWS demo — SEN-69

- Data de referência: 2026-08-23
- Região de custo: `us-east-1`
- Base avaliada:
  `3bf3a78126047d9eceff7692b942ff0406c50651` em `origin/develop`
- Terraform: `1.15.9` para `windows_amd64`
- Resultado do snapshot SEN-69: arquitetura e gates aprovados offline; nenhuma
  evidência live havia sido produzida

Este relatório confronta o Terraform da SEN-67, a automação protegida da SEN-68,
o diagrama e a documentação. Ele separa configuração versionada, prova executada
localmente e capacidade ainda dependente de autorização.

> **Nota de supersessão (SEN-75, 2026-08-24):** este documento preserva números e
> conclusões do snapshot SEN-69. O código posterior passou a incluir a UI,
> publicação allowlisted, Hosted UI/PKCE e smoke da URL publicada. Isso foi
> validado apenas offline; apply, publicação, login humano e teardown live
> continuam como evidência pendente da SEN-74.

> **Nota operacional (SEN-82, 2026-08-24):** o run `32725423445` confirmou o
> preflight protegido do workflow de deploy e a assunção OIDC da role de deploy
> no modo foundation. A action fixada por SHA entregou `aws-expiration` como
> JSON-string, e o controlador recusou o valor antes do Terraform. State e Budget
> permaneceram ausentes; não houve recurso gerenciado pelo perfil, plan remoto,
> `apply` ou deploy. Os logs foram removidos depois de exporem account ID e role
> ARN nos inputs da action, sem que esses valores fossem preservados neste
> relatório.

No snapshot original da SEN-69, nenhuma variável usual de credencial AWS estava
definida e nenhum GitHub environment havia sido alterado. Desde então, o
bootstrap externo de state, OIDC/IAM, environments e certificado foi preparado
sem criar o state do perfil. Não foram executados `apply`, deploy, smoke remoto,
teardown ou consulta de billing. Os materiais originais não foram acessados.

## Estado das evidências

| Estado | Evidência | Conclusão permitida |
| --- | --- | --- |
| **Validado offline** | `fmt`, `init -backend=false`, `validate`, plano sintético isolado, auditoria do plano e regressões de segurança e entrega | O código versionado produz um plano fechado, privado e removível sob placeholders; os casos adversariais cobertos são recusados. |
| **Validado live parcialmente** | preflight de deploy e OIDC do run `32725423445` | O environment e a trust da role de deploy permitiram credencial temporária no modo foundation; a execução parou antes do Terraform e não comprova state, Budget, plan remoto ou deploy. |
| **Planejado e versionado** | Workflows de plan, fundação/runtime e teardown; contrato OIDC/IAM; smoke e inventário pós-destroy | Os gates existem, mas não comprovam configuração efetiva no GitHub nem aceitação pela AWS. |
| **Preparado externamente** | Conta exclusiva, bucket S3 sem state, OIDC, três roles, três environments com reviewer, domínio, certificado e autorização financeira | O bootstrap permite nova tentativa controlada depois do hotfix, mas não comprova nenhum recurso Terraform ou runtime. |
| **Pendente da SEN-74** | Foundation, alvo DNS do CloudFront, identidade efêmera de smoke, runtime, medição e teardown | Esses resultados só podem ser declarados depois das execuções live correspondentes. |

O único workflow automático em pull request é
`aws-demo-validate.yml`. Ele não solicita OIDC, environment ou segredo e só
executa validações locais. Os workflows capazes de consultar ou alterar a AWS
usam `workflow_dispatch`, exigem o HEAD atual de `main`, SHA completo,
confirmação literal, environment separado e role temporária própria. As
operações mutáveis compartilham `concurrency: aws-demo-state`.

A SEN-82 move account ID, bucket de state, certificado ARN e a role ARN exclusiva
de cada operação para segredos (`secrets`) do respectivo environment. Região, AZ
e domínio permanecem variáveis (`vars`); e-mail do Budget e token de smoke
continuam secrets. Essa distinção aciona a máscara de valores do GitHub para
identificadores que não podem aparecer no log, sem tratá-los como credenciais AWS.
Nenhum secret é
referenciado por checkout, preflight ou preparação anterior à action OIDC; a
action recebe somente account ID e sua role, e os steps operacionais recebem
somente o subconjunto necessário. A alteração versionada não administra os
controles externos. Em paralelo, os secrets equivalentes já foram preparados nos
três environments; as vars legadas permanecem temporariamente para rollback e
serão removidas somente depois que o hotfix for validado em `main`.

## Topologia confrontada

O plano offline contém 73 recursos gerenciados, todos com ação `create`, e 14
outputs em allowlist. A configuração mantém uma VPC single-AZ sem Internet
Gateway, NAT ou IP público. O API Gateway chega ao ECS Fargate privado por VPC
Link e Cloud Map; a task acessa ECR, Logs e SQS por quatro endpoints Interface e
S3 por um endpoint Gateway. CloudFront usa OAC para o bucket privado do
frontend; a API exige JWT Cognito e CORS para uma origem HTTPS exata.

O diagrama de
[`infra/aws/demo/README.md`](../../infra/aws/demo/README.md) foi alinhado a essa
topologia. A revisão também corrigiu o inventário arquitetural, que ainda
afirmava existir somente três workflows e nenhum deploy após a SEN-68.

### Inventário sanitizado do plano

| Grupo lógico | Tipos e quantidades | Total |
| --- | --- | ---: |
| Governança | Budget (1) | 1 |
| Rede privada | VPC (1), subnet (1), route table/association (2), security groups (3), regras (7), endpoints (5) | 19 |
| Storage e entrega web | buckets e controles S3 (19), ECR e lifecycle (2), distribuição e policies CloudFront/OAC (4) | 25 |
| Compute e descoberta | cluster, service e task definition ECS (3), namespace e serviço Cloud Map (2), log group da API (1) | 6 |
| Identidade | roles e policies IAM (6), user pool e client Cognito (2) | 8 |
| API | HTTP API, authorizer, integration, route, stage e VPC Link (6), log group (1) | 7 |
| Fila | SQS e DLQ (2), redrive allow policy (1) | 3 |
| Monitoramento | alarmes CloudWatch (4) | 4 |
| **Total** |  | **73** |

O inventário publica somente categorias e contagens. Nomes físicos, ARNs,
account ID, e-mail, nonce, token, state, valores de output e caminhos
temporários não aparecem.

O plano usa `api_desired_count = 0`: cluster, service e task definition são
declarados, mas nenhuma task Fargate é executada e a imagem placeholder não é
baixada. O custo de Fargate estimado abaixo pertence à futura janela de runtime
com contagem `1`, não à validação offline ou à fundação vazia.

## Segurança, custo automático e remoção

| Controle | Resultado offline |
| --- | --- |
| IAM da aplicação | A role de execução limita-se a pull do ECR e logs; a role da API limita-se a SQS, objetos dos buckets exatos e Bedrock opt-in; a role de worker é apenas um contrato sem compute. |
| OIDC da entrega | Três subjects imutáveis e exclusivos para plan, deploy e teardown; trust exige o provider GitHub e `aud = sts.amazonaws.com`. A role de deploy foi assumida no modo foundation do run `32725423445`; plan remoto e teardown não foram exercitados live. |
| Buckets e frontend | Três buckets com Public Access Block, Object Ownership, SSE-S3, versionamento e lifecycle; frontend lido somente pelo CloudFront/OAC. |
| Cognito e CORS | Usuário apenas administrativo, client público sem secret, rota `$default` com JWT e uma origem CORS HTTPS exata. Nenhum usuário foi criado. |
| Rede e criptografia | Task sem IP público; regras por security group; ECR AES-256, SQS SSE gerenciada e S3 SSE-S3. O perfil efêmero não cria chaves KMS. |
| Budget e alarmes | Budget mensal precede recursos cobrados e alerta a 80% real e 100% previsto; quatro alarmes não têm ações. Alertas não interrompem gasto. |
| Teardown | Workflow manual aceita somente exclusões do state aprovado, exige state vazio e combina tags com scans limitados. Não foi executado live. |

Nenhum recurso pago é criado automaticamente: o workflow de pull request é
inteiramente offline e fundação, runtime e teardown exigem dispatch e aprovação
explícitos. `delivery_policy.py` aprovou subjects, permissions, actions fixadas
por SHA e operações exclusivamente manuais. `delivery_regression.py` aprovou
388 casos adversariais em OIDC/IAM, workflows, plano/state, smoke, inventário e
ambiente.

## Estimativa de custo, não medição

A estimativa foi recalculada em 2026-08-23 para `us-east-1`, em USD, com preços
públicos sob demanda e sem crédito, desconto, Savings Plan ou promessa de Free
Tier. A janela assume oito horas, uma task Linux/x86 de `0,25 vCPU` e `0,5 GB`,
quatro endpoints Interface, até cinco usuários, 5.000 chamadas de baixo volume,
até 1 GB em storage/CDN, imagem ECR de 0,5 GB e 0,1 GB de logs. Itens mensais
pequenos foram cobrados pelo mês completo ou arredondados para cima.

| Serviço | Unidade e premissa | USD |
| --- | --- | ---: |
| ECS Fargate | `(0,25 vCPU × USD 0,0404784/vCPU-h + 0,5 GB × USD 0,004446/GB-h) × 8 h` | 0,10 |
| AWS PrivateLink | `4 endpoints × 8 endpoint-h × USD 0,01` mais até 5 GB processados | 0,37 |
| Cloud Map e Route 53 privado | um recurso e uma private hosted zone, sem pró-rata favorável | 0,60 |
| CloudWatch | quatro alarmes standard pelo mês mais 0,1 GB de logs | 0,45 |
| ECR | `0,5 GB-mês × USD 0,10/GB-mês` | 0,05 |
| API Gateway HTTP API | 5.000 requests no primeiro tier, arredondados | 0,01 |
| SQS | até 5.000 requests standard, arredondados | 0,01 |
| S3 | até 1 GB-mês, requests e versões residuais | 0,03 |
| CloudFront | até 1 GB de saída, requests HTTPS e margem | 0,16 |
| Cognito Lite | até 5 MAU, sem depender da gratuidade da conta | 0,03 |
| AWS Budgets | monitoramento sem Budget Actions | 0,00 |
| **Subtotal estimado** |  | **1,81** |
| **Com contingência de 50%** | variação, propagação e arredondamento | **2,72** |

Com câmbio exclusivamente de planejamento de `R$ 6,00/USD`, a contingência é
`R$ 16,32`, abaixo do teto operacional de `R$ 20` para a janela. O Budget
padrão de USD 15 equivale a R$ 90 nesse câmbio, e o Terraform limita o valor
mensal a USD 16, ainda abaixo de R$ 100. Budget é alerta com atraso, não hard
cap.

Fontes oficiais consultadas:

- [AWS Fargate pricing](https://aws.amazon.com/fargate/pricing/);
- [AWS PrivateLink pricing](https://aws.amazon.com/privatelink/pricing/);
- [AWS Cloud Map pricing](https://aws.amazon.com/cloud-map/pricing/) e
  [Route 53 pricing](https://aws.amazon.com/route53/pricing/);
- [Amazon CloudWatch pricing](https://aws.amazon.com/cloudwatch/pricing/);
- [Amazon ECR pricing](https://aws.amazon.com/ecr/pricing/);
- [Amazon API Gateway pricing](https://aws.amazon.com/api-gateway/pricing/);
- [Amazon SQS pricing](https://aws.amazon.com/sqs/pricing/);
- [Amazon S3 pricing](https://aws.amazon.com/s3/pricing/);
- [Amazon CloudFront pricing](https://aws.amazon.com/cloudfront/pricing/);
- [Amazon Cognito pricing](https://aws.amazon.com/cognito/pricing/);
- [AWS Budgets pricing](https://aws.amazon.com/aws-cost-management/aws-budgets/pricing/).

Nenhum recurso existia para consulta de billing, portanto os valores são
**estimativas** e a medição permanece **indisponível**. Domínio, hosted zone
pública, certificado e bucket externo de state são pré-requisitos fora do
módulo e devem ser confirmados pelo responsável. Bedrock está desabilitado e sua
ativação invalida a estimativa até nova aprovação.

## Substituições locais e capacidades não implantadas

| Capacidade | Estado comprovado |
| --- | --- |
| Persistência | A task usa `aws` + `memory`; PostgreSQL/pgvector existe apenas localmente e não há RDS ou Aurora no plano. |
| Frontend | Bucket e distribuição estão declarados, mas `apps/web` não possui UI e nenhum conteúdo foi publicado. |
| Imagem | ECR está declarado vazio; runtime exige tag imutável e digest verificado. Nenhuma imagem foi publicada. |
| Worker | SQS, DLQ, schema e role existem; não há serviço, task ou executável de worker. |
| Geração | Bedrock é opt-in e falso no plano; nenhuma chamada foi feita. |
| Autenticação | Cognito e JWT estão declarados; usuário, senha e token de smoke são externos e não foram criados. |
| Operação | DNS, certificado, state, OIDC e environments não são recursos deste módulo; não houve deploy, smoke, teardown ou inventário live. |

Também não existem NAT, Internet Gateway, ALB, banco gerenciado, Textract, WAF,
alta disponibilidade, multi-região ou ambiente de produção.

## Validações executadas

| Comando ou prova | Resultado |
| --- | --- |
| SHA-256 do ZIP oficial e `terraform version` | Terraform 1.15.9 verificado |
| `terraform fmt -check -recursive` | aprovado |
| `terraform init -backend=false -input=false` | provider AWS 6.61.0 instalado pelo lock |
| `terraform validate` | configuração válida |
| `static_plan.py` | plano sintético gerado fora do repositório |
| `plan_audit.py` | allowlists, rede, IAM, outputs, Budget, alarmes, tags e teardown aprovados |
| `security_regression.py` | baseline aceita; 23 mutações, output duplicado e três constantes não finitas rejeitados |
| `delivery_policy.py` | três subjects, permissions, actions por SHA e operações manuais aprovados |
| `delivery_regression.py` | 388 casos adversariais aprovados |
| `uv run --frozen poe check` | Ruff e Pyright aprovados; 1.119 testes aprovados, 43 ignorados e cobertura de 80,32% |
| `uv run --frozen poe hooks` | 11 hooks aprovados, incluindo arquivos estruturados, chaves privadas, finais de linha e Ruff |
| `uv run --frozen poe smoke` | runtimes, importação, `.env.example`, Compose e health checks locais aprovados |
| `uv lock --check` e `corepack pnpm install --frozen-lockfile --offline` | locks Python e Node reproduzíveis |
| `gitleaks git --staged --no-banner --redact .` | nenhuma exposição encontrada nas alterações |
| proteção dos originais e integridade textual | nomes protegidos ausentes do índice; alterações em UTF-8, LF e com newline final; `git diff --check` aprovado |
| TFLint e Checkov | indisponíveis no ambiente; não executados |

O plano JSON permaneceu temporário. `.gitignore` bloqueia `.terraform/`,
`*.tfplan`, `*.tfplan.json`, `*.tfvars` reais e `*.tfstate*`.

## Reprodução offline

```powershell
terraform -chdir=infra/aws/demo fmt -check -recursive
terraform -chdir=infra/aws/demo init -backend=false -input=false
terraform -chdir=infra/aws/demo validate
uv run --frozen python infra/aws/demo/scripts/static_plan.py `
  --terraform <terraform-1.15.9-verificado> `
  --plan-json <arquivo-temporario>.tfplan.json
uv run --frozen python infra/aws/demo/scripts/plan_audit.py `
  <arquivo-temporario>.tfplan.json
uv run --frozen python infra/aws/demo/scripts/security_regression.py `
  <arquivo-temporario>.tfplan.json
uv run --frozen python infra/aws/demo/scripts/delivery_policy.py
uv run --frozen python infra/aws/demo/scripts/delivery_regression.py
```

## Limites e decisão

A validação offline encerra os critérios reproduzíveis da SEN-69, mas não
autoriza implantação. A evidência parcial do run `32725423445` também não
comprova implantação. A nova tentativa está autorizada somente depois do merge
do hotfix e de uma revalidação da ausência de state, Budget e inventário, além da
configuração exata de secrets e vars dos environments. A evidência final precisa
registrar plan remoto, inventário sanitizado pós-apply, smoke autenticado,
teardown pelo mesmo state e inventário negativo; uma falha não pode ser
convertida em alegação de sucesso.
