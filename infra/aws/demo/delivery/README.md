# Entrega protegida do perfil AWS demo

Este diretório registra o contrato de segurança e a operação automatizada da
SEN-68. O código prepara validação, plano, deploy e teardown, mas **nenhuma dessas
operações foi executada na AWS nesta tarefa**. Roles, provedor OIDC, backend S3,
domínio, certificado, usuário de smoke e valores reais continuam sendo bootstrap
externo autorizado; o contrato não cria nem altera esses controles. Os três
environments e o reviewer também são externos, embora sua configuração efetiva
tenha sido validada somente para leitura em 23/08/2026.

O contrato público [`delivery-contract.v1.json`](delivery-contract.v1.json) usa
somente placeholders e fixa repositório, ref, subjects OIDC, referências completas
das actions, backend e
fronteiras IAM. `delivery_policy.py` vincula cada permission policy à combinação
exata de `Sid`, `Action` e `Resource`; mudar uma ação ou ampliar um recurso exige
alteração deliberada do contrato, do fingerprint e das regressões.
`permission_policy_publication` fixa também nome, versão, modo inline ou
customer-managed e o particionamento exato de cada documento publicado.

## Fluxos

| Workflow | Evento e proteção | Efeito permitido |
| --- | --- | --- |
| `aws-demo-validate.yml` | `pull_request` para `develop`, sem environment, segredo ou OIDC | formata/valida Terraform, gera plano inteiramente sintético e executa políticas offline |
| `aws-demo-plan.yml` | manual no HEAD atual de `main`, environment `aws-demo-plan`, confirmação exata e SHA igual ao `GITHUB_SHA`; exige fundação concluída | lê infraestrutura e state completo, cria/remove apenas o lock nativo do S3 e produz um plano privado; não executa `apply` |
| `aws-demo-deploy.yml` | manual no HEAD atual de `main`, environment `aws-demo-deploy`, escolha exata entre `FOUNDATION-AWS-DEMO` e `DEPLOY-AWS-DEMO`, com SHA igual ao `GITHUB_SHA` | em dispatches separados, cria/valida a fundação vazia ou publica/reutiliza a API por digest e executa o smoke autenticado |
| `aws-demo-teardown.yml` | manual no HEAD atual de `main`, environment `aws-demo-teardown`, confirmação exata e SHA igual ao `GITHUB_SHA` | aplica somente um plano de exclusões, comprova state vazio e procura recursos residuais no escopo |

Os workflows manuais compartilham `concurrency: aws-demo-state` e não cancelam
uma execução em andamento. O job de preflight possui somente `actions: read` e
`contents: read`: além de rejeitar repositório, evento, ref, confirmação ou SHA
fora da allowlist, consulta a API REST do GitHub e exige que o environment exato
exista, tenha somente `HiRenan` como reviewer, `prevent_self_review = false`,
`can_admins_bypass = false` e aceite somente a deployment branch literal
`main`. Esse fallback de operador único mantém dois atos manuais — dispatch e
`Approve and deploy` — sem alegar revisão independente. Environment ausente,
resposta truncada, schema novo, reviewer diferente, bypass administrativo ou
branch policy mais ampla reprovam antes de qualquer solicitação OIDC. O job
protegido possui somente `contents: read` e `id-token: write`; credenciais AWS
temporárias só nascem depois da aprovação do environment. Pull requests e forks
nunca alcançam esse job.

O `source_sha` precisa ser exatamente o `GITHUB_SHA` do dispatch em `main` e o
HEAD efetivamente obtido pelo checkout. Aceitar apenas ancestralidade permitiria
selecionar uma revisão histórica e executar um controlador anterior aos fixes de
segurança; a igualdade fecha esse downgrade. A baseline SEN-46 fixa
`d45bcabfb6de89c6bac2ec2aa6180bce353be7c1` no contrato e no controlador. Ela
continua sendo um gate adicional do deploy, não substitui a identidade do
controlador atual e não pode ser autoatestada por secret, variable ou input.

Como a aprovação do environment pode ficar pendente enquanto `main` avança, cada
job protegido faz um segundo fetch público de `origin/main`, com configurações
Git global e de sistema desabilitadas, depois da aprovação e do checkout imutável,
mas antes de solicitar OIDC. HEAD local, `source_sha` e `origin/main` precisam
continuar idênticos. Essa segunda prova fecha o drift da janela de aprovação; ela
não consegue bloquear novos commits em `main` depois da verificação. A execução
continua presa ao SHA verificado e uma nova operação deve usar o HEAD então atual.

Os inputs entram em `env` e são comparados como dados. Nenhuma expressão GitHub
é interpolada dentro de shell, nenhum tfvars real é criado e todo checkout
desabilita persistência de credenciais. Actions de terceiros são fixadas por SHA
completo. Planos binários/JSON, listagens de state, outputs, logs de comandos e
metadata de build não são enviados como artifact: planos, listagens e metadata
existem somente sob diretório temporário; stdout/stderr de operações sem retorno
são descartados e consultas possuem limite estrito antes do uso. Nada disso é
impresso.

Como `terraform state list` reprova quando o backend ainda não possui snapshot,
o controlador usa primeiro `terraform state pull`: saída vazia é a única forma
aceita de state ausente. Um snapshot existente precisa ser JSON canônico antes
da listagem de endereços. O conteúdo bruto, que pode conter valores sensíveis,
fica limitado em memória e nunca é persistido ou impresso.
Somente foundation e teardown tratam essa ausência como estado operacional
válido; deploy e plan fecham o gate. Em especial, plan não possui um branch
`fresh` e não serve para inicializar o backend.

Variáveis operacionais e segredos existem somente nos dois steps finais e
mutuamente exclusivos. O step de fundação não recebe baseline SEN-46 nem token
de smoke; o step de runtime recebe ambos. Checkout, setups, Buildx e a própria
assunção OIDC não recebem o token nem o e-mail do Budget.

Buildx existe somente no modo runtime. O workflow cria um `DOCKER_CONFIG`
dedicado em `runner.temp`, identificado por `run_id` e `run_attempt`, entrega o
mesmo caminho ao `setup-buildx-action` e ao controlador sanitizado e seleciona
explicitamente o nome devolvido por `steps.buildx.outputs.name`. O executor
rejeita nome, caminho, symlink ou variável Docker/Buildx ambiental fora desse
contrato; ele não reabre `~/.docker`. O modo de fundação pula os dois steps e não
recebe nome ou configuração de builder.

## Identidade OIDC e privilégio mínimo

As três roles não compartilham subject nem environment:

```text
repo:HiRenan@107653306/senai-prescriptive-maintenance@1342357031:environment:aws-demo-plan
repo:HiRenan@107653306/senai-prescriptive-maintenance@1342357031:environment:aws-demo-deploy
repo:HiRenan@107653306/senai-prescriptive-maintenance@1342357031:environment:aws-demo-teardown
```

Cada trust aceita somente o provider
`token.actions.githubusercontent.com`, `aud = sts.amazonaws.com` e seu `sub`
imutável exato. O executor exige as três partes da credencial temporária STS,
incluindo `AWS_SESSION_TOKEN`, e rejeita região default divergente; access key
duradoura sem token de sessão não satisfaz o contrato. A role de plan lê o
perfil e o state, mas precisa de
`GetObject`/`PutObject`/`DeleteObject` no objeto `.tflock`: portanto ela é sem
mutação de infraestrutura, **não** uma policy IAM estritamente read-only. A role
de plan possui apenas `GetObject` no objeto de state, deliberadamente sem
`PutObject`; por isso ela só pode rodar depois de `FOUNDATION-AWS-DEMO`. Um plan
anterior pode falhar já no backend init e deve permanecer fail-closed, sem ampliar
a role. A role de deploy concentra provisionamento, build/push ECR e atualização
ECS, sem as exclusões do teardown. A role de teardown concentra destruição e
inventário e
não publica imagens nem cria recursos.

O provider AWS fixado em `6.61.0` também faz leituras auxiliares durante refresh
e remoção. Por isso, as três roles contêm explicitamente
`iam:ListAttachedRolePolicies`, `ec2:DescribeVpcAttribute` e
`ec2:DescribeSecurityGroupRules`; teardown acrescenta
`iam:ListInstanceProfilesForRole`. As leituras IAM ficam somente nos três ARNs
de role canônicos. As chamadas EC2 `Describe*`, que não oferecem escopo por ARN,
ficam isoladas das mutações no statement global de leitura. Essa matriz é
protegida por regressões de remoção de cada ação e deve ser reavaliada quando a
versão do provider mudar.

Plan usa sessão STS de 3.600 segundos para um job limitado a 45 minutos. Deploy
e teardown usam 7.200 segundos para jobs limitados a 90 minutos; suas roles
externas precisam ter `MaxSessionDuration` de pelo menos 7.200 segundos, enquanto
a role de plan precisa aceitar pelo menos 3.600. O workflow captura o
`aws-expiration` emitido pela action OIDC, o controlador recusa uma duração maior
que 7.300 segundos e exige margem de cinco minutos mais a janela inteira da
próxima mutação. Assim, nenhum `apply` começa quando a sessão poderia terminar
antes do timeout restante do job. O cliente Cognito emite access e ID tokens por
duas horas; o controlador só aceita access token com `iat`/`exp` canônicos,
duração máxima de duas horas e pelo menos 6.000 segundos restantes antes do
build. Uma expiração insuficiente reprova antes da mutação.

Referências primárias: [AWS IAM: duração da sessão assumida](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_manage-assume.html),
[AWS IAM: atualização de `MaxSessionDuration`](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_update-role-settings.html),
[action OIDC: `role-duration-seconds` e `aws-expiration`](https://github.com/aws-actions/configure-aws-credentials),
[Cognito: validade de tokens do app client](https://docs.aws.amazon.com/cognito/latest/developerguide/amazon-cognito-user-pools-using-tokens-with-identity-providers.html)
e [provider AWS `v6.61.0`](https://github.com/hashicorp/terraform-provider-aws/tree/v6.61.0).

Em uma conta limpa, a criação inicial de ECS e API Gateway pode exigir as
service-linked roles `AWSServiceRoleForECS` e `AWSServiceRoleForAPIGateway`. A
role de deploy permite somente `iam:CreateServiceLinkedRole` nos dois prefixos de
ARN canônicos, cada um condicionado por `iam:AWSServiceName` exatamente igual a
`ecs.amazonaws.com` ou `ops.apigateway.amazonaws.com`. Essas roles são recursos
da conta, ficam fora do state e do teardown da demo e não têm custo direto; o
teardown não tenta removê-las. A policy não inclui service-linked role de
Budgets, pois o perfil não usa BillingView entre contas.

State, IAM, ECR e S3 usam statements separados. O objeto de state permite apenas
`GetObject`/`PutObject`; `DeleteObject` fica restrito ao `.tflock`. As três roles
da aplicação, o repositório ECR e os buckets/objetos da demo usam ARNs derivados
dos nomes fixos. No teardown, as APIs S3 que removem lifecycle, tags, encryption,
ownership controls e public access block exigem suas permissões `Put*`
correspondentes; aliases intuitivos como `DeleteBucketLifecycle` não são ações
IAM válidas. A [matriz oficial de operações e permissões S3](https://docs.aws.amazon.com/AmazonS3/latest/userguide/using-with-s3-policy-actions.html)
fundamenta esses nomes. ECR separa leitura no ARN exato, criação condicionada por
`aws:RequestTag/Profile` e upload/configuração condicionados por
`aws:ResourceTag/Profile`; somente `ecr:GetAuthorizationToken` permanece global.

As quatro correções de compatibilidade com o IAM permanecem explícitas e
regredidas. `ecs:DeregisterTaskDefinition` fica sozinho em `TaskDeregister` e
`DestroyTaskDefinitions`, sem condição e com `Resource: "*"`, porque a
[matriz oficial do ECS](https://docs.aws.amazon.com/service-authorization/latest/reference/list_amazonelasticcontainerservice.html)
não oferece resource-level authorization nem condition key para essa ação.
`servicediscovery:TagResource` também fica sozinho em `CloudMapTag` com
`Resource: "*"`: a
[matriz oficial do Cloud Map](https://docs.aws.amazon.com/service-authorization/latest/reference/list_servicediscovery.html)
deixa `Resource types` vazio para a ação e admite apenas
`aws:RequestTag/${TagKey}` e `aws:TagKeys`. O statement exige exatamente
`aws:RequestTag/Profile = aws-demo` e `aws:TagKeys = ["Profile"]`. Esses são os
únicos dois tipos de ação acrescentados com wildcard global; nenhum deles
compartilha statement com outra permissão.
`ecs:RegisterTaskDefinition` compara `ecs:task-cpu` e `ecs:task-memory` com
`NumericEquals`, mantendo a tag do request em `StringEquals`. Por fim, a ação IAM
do inventário é `tag:GetResources`; `resourcegroupstaggingapi` continua sendo
apenas o namespace do comando AWS CLI.

### Publicação e margem das permission policies

O contrato declara como cada policy deve ser publicada. Plan e teardown usam um
documento inline cada; deploy usa duas customer-managed policies versionadas e
anexadas à mesma role. Essa divisão não altera ações, recursos ou condições: cada
Sid aparece exatamente uma vez, e o auditor falha se houver omissão, duplicidade,
mudança de modo ou nome sem versão.

Os tamanhos abaixo usam JSON canônico já renderizado com valores sintéticos
seguros; espaços em branco não entram na contagem. O gate exige pelo menos 1.024
caracteres livres em inline policies e 900 em cada customer-managed policy.

| role/documento | modo | tamanho | limite | margem |
| --- | --- | ---: | ---: | ---: |
| plan / `senai-pm-demo-plan-v1` | inline | 2.853 | 10.240 | 7.387 |
| deploy / `senai-pm-demo-deploy-core-v1` | customer-managed | 4.987 | 6.144 | 1.157 |
| deploy / `senai-pm-demo-deploy-runtime-v1` | customer-managed | 5.050 | 6.144 | 1.094 |
| teardown / `senai-pm-demo-teardown-v1` | inline | 6.727 | 10.240 | 3.513 |

Sem particionamento, o deploy renderizado ocuparia 9.999 dos 10.240 caracteres
inline e deixaria só 241 de margem. As duas policies anexadas consomem 2 das 10
associações customer-managed padrão da role, preservando 8 para uma evolução
deliberada. Uma policy futura, inclusive de frontend, deve ganhar nome versionado
e só pode ser anexada depois de passar pelos mesmos limites; não se remove
condição nem se amplia wildcard para acomodá-la.

O administrador gera os quatro documentos sanitizados e o manifesto de hashes
em um diretório novo antes do bootstrap:

```powershell
uv run --frozen python infra/aws/demo/scripts/delivery_policy.py `
  --render-directory C:/secure-local/aws-demo-policies-v1
```

O diretório é local e não deve ser versionado. Cada documento gerado deve passar
por `aws accessanalyzer validate-policy --policy-type IDENTITY_POLICY` antes de
qualquer publicação; ausência de credenciais mantém essa prova live pendente.

### Wildcards residuais, ação por ação

Uma **conta AWS exclusiva da demo, sem workloads de produção ou compartilhados,
é requisito de execução**, não recomendação. Account, região, nomes, paths, ARNs
e tags limitam todas as mutações em que a matriz oficial oferece esses controles;
o gate de state/plano acrescenta a identidade de valor. Ainda assim, algumas
leituras do provider e criações globais exigem ou conservam `Resource: "*"`.
Nenhuma ação de delete, update, policy, pass-role, upload de camada ou escrita de
state aparece nos statements globais abaixo.

- Plan, `ReadDemoResources`: `apigateway:GET`, `budgets:ViewBudget`,
  `cloudfront:GetCachePolicy`, `cloudfront:GetDistribution`,
  `cloudfront:GetOriginAccessControl`, `cloudfront:GetResponseHeadersPolicy`,
  `cloudfront:ListTagsForResource`, `cloudwatch:DescribeAlarms`,
  `cloudwatch:ListTagsForResource`, `cognito-idp:DescribeUserPool`,
  `cognito-idp:DescribeUserPoolClient`, `cognito-idp:ListTagsForResource`,
  `ec2:DescribeAvailabilityZones`, `ec2:DescribeRouteTables`,
  `ec2:DescribeSecurityGroupRules`, `ec2:DescribeSecurityGroups`,
  `ec2:DescribeSubnets`, `ec2:DescribeVpcAttribute`,
  `ec2:DescribeVpcEndpoints`, `ec2:DescribeVpcs`, `ecs:DescribeClusters`,
  `ecs:DescribeServices`, `ecs:DescribeTaskDefinition`,
  `ecs:ListTagsForResource`, `logs:DescribeLogGroups`,
  `logs:ListTagsForResource`, `servicediscovery:GetNamespace`,
  `servicediscovery:GetService`, `servicediscovery:ListTagsForResource`,
  `sqs:GetQueueAttributes`, `sqs:GetQueueUrl`, `sqs:ListQueueTags` e
  `sts:GetCallerIdentity`. Motivo: refresh do provider antes do plano, inclusive
  APIs `Describe` sem resource-level authorization e consultas cujo ID é obtido
  do state; são exclusivamente leitura e não contêm mutação de infraestrutura
  (o lock nativo do backend permanece no statement S3 exato e separado).
- Deploy, `EcrAuth`: `ecr:GetAuthorizationToken`. Motivo: a matriz ECR
  não oferece resource-level authorization para a credencial temporária do
  registry; nenhum segredo retornado é impresso.
- Deploy, `GlobalReads`: `apigateway:GET`, `budgets:ViewBudget`,
  `cloudfront:GetCachePolicy`, `cloudfront:GetDistribution`,
  `cloudfront:GetOriginAccessControl`, `cloudfront:GetResponseHeadersPolicy`,
  `cloudfront:ListTagsForResource`, `cloudwatch:DescribeAlarms`,
  `cloudwatch:ListTagsForResource`, `cognito-idp:DescribeUserPool`,
  `cognito-idp:DescribeUserPoolClient`, `cognito-idp:ListTagsForResource`,
  `ec2:DescribeAvailabilityZones`, `ec2:DescribeRouteTables`,
  `ec2:DescribeSecurityGroupRules`, `ec2:DescribeSecurityGroups`,
  `ec2:DescribeSubnets`, `ec2:DescribeVpcAttribute`,
  `ec2:DescribeVpcEndpoints`, `ec2:DescribeVpcs`, `ecs:DescribeClusters`,
  `ecs:DescribeServices`, `ecs:DescribeTaskDefinition`,
  `ecs:ListTagsForResource`, `logs:DescribeLogGroups`,
  `logs:ListTagsForResource`, `servicediscovery:GetNamespace`,
  `servicediscovery:GetOperation`, `servicediscovery:GetService`,
  `servicediscovery:ListTagsForResource`, `sqs:GetQueueAttributes`,
  `sqs:GetQueueUrl`, `sqs:ListQueueTags` e `sts:GetCallerIdentity`. Motivo:
  refresh/reconciliação do provider; o statement é somente leitura e está
  separado de todas as mutações.
- Deploy, `GlobalNew`: `cloudfront:CreateDistribution`,
  `cognito-idp:CreateUserPool` e
  `servicediscovery:CreatePrivateDnsNamespace`. Motivo: a autorização oficial
  não oferece ARN de recurso pré-existente para essas criações; todas exigem
  `aws:RequestTag/Profile = aws-demo`.
- Deploy, `TaskDeregister`: `ecs:DeregisterTaskDefinition`. Motivo: a matriz ECS
  não oferece resource-level authorization nem condition key para a ação; ela
  permanece sozinha, sem outras ações e sob o gate de state/plano exato.
- Deploy, `CloudMapTag`: `servicediscovery:TagResource`. Motivo: a matriz Cloud
  Map não oferece resource-level authorization para essa ação, portanto exige
  `Resource: "*"`; o statement isolado permite somente a request tag
  `Profile = aws-demo` e somente a tag key `Profile`.
- Deploy, `CfPolicyNew`: `cloudfront:CreateCachePolicy`,
  `cloudfront:CreateOriginAccessControl` e
  `cloudfront:CreateResponseHeadersPolicy`. Motivo: essas criações não oferecem
  resource type nem condition key de tag na matriz CloudFront. Os nomes são
  comprovados no state/plano, e o teardown limita deletes aos três tipos de ARN
  na conta.
- Deploy, `ApiCreate`: o `POST` que cria a REST API foi isolado no recurso
  raiz exato `arn:aws:apigateway:${AWS_REGION}::/apis`, sem wildcard, e exige
  `aws:RequestTag/Profile = aws-demo`. Os demais `POST`/`PATCH` ficam em
  `ApiWrites` sob `/apis/*`, pois atuam somente depois que o API ID existe.
- Teardown, `InventoryDemoResourcesGlobal`: `apigateway:GET`,
  `budgets:ViewBudget`, `cloudfront:GetCachePolicy`,
  `cloudfront:GetDistribution`, `cloudfront:GetDistributionConfig`,
  `cloudfront:GetOriginAccessControl`, `cloudfront:GetResponseHeadersPolicy`,
  `cloudfront:ListCachePolicies`, `cloudfront:ListDistributions`,
  `cloudfront:ListOriginAccessControls`, `cloudfront:ListResponseHeadersPolicies`,
  `cloudfront:ListTagsForResource`, `cloudwatch:DescribeAlarms`,
  `cloudwatch:ListTagsForResource`, `cognito-idp:DescribeUserPool`,
  `cognito-idp:DescribeUserPoolClient`, `cognito-idp:ListTagsForResource`,
  `cognito-idp:ListUserPools`, `ec2:DescribeRouteTables`,
  `ec2:DescribeSecurityGroupRules`, `ec2:DescribeSecurityGroups`,
  `ec2:DescribeSubnets`, `ec2:DescribeVpcAttribute`,
  `ec2:DescribeVpcEndpoints`, `ec2:DescribeVpcs`,
  `ecr:DescribeRepositories`, `ecs:DescribeClusters`, `ecs:DescribeServices`,
  `ecs:DescribeTaskDefinition`, `ecs:ListClusters`,
  `ecs:ListTagsForResource`, `iam:ListRoles`, `logs:DescribeLogGroups`,
  `logs:ListTagsForResource`,
  `s3:ListAllMyBuckets`, `servicediscovery:GetNamespace`,
  `servicediscovery:GetOperation`, `servicediscovery:GetService`,
  `servicediscovery:ListNamespaces`, `servicediscovery:ListServices`,
  `servicediscovery:ListTagsForResource`, `sqs:GetQueueAttributes`,
  `sqs:GetQueueUrl`, `sqs:ListQueueTags`, `sqs:ListQueues`,
  `sts:GetCallerIdentity` e `tag:GetResources`. Motivo: depois da exclusão,
  ARNs/IDs já podem não existir; as listagens somente leitura precisam enumerar
  a conta/região para
  provar ausência dentro do escopo. O parser aceita apenas nomes, ARNs e tags
  canônicos e falha em qualquer erro, truncamento ou schema novo.
- Teardown, `DestroyTaskDefinitions`: `ecs:DeregisterTaskDefinition`. Mesma
  limitação da matriz ECS; a ação fica isolada e depende da identidade exata do
  state/plano aprovada antes do destroy.

#### Wildcards confinados a ARN ou path

Os casos abaixo não usam `Resource: "*"`, mas ainda contêm um componente
wildcard porque o identificador é atribuído pelo serviço. Cada ação residual é
enumerada para não esconder privilégio dentro de um ARN aparentemente restrito.

- Deploy, `ApiSlr` (ARN):
  `iam:CreateServiceLinkedRole`. O sufixo de role é gerado pela AWS; account e
  prefixo canônico ficam no ARN e `iam:AWSServiceName` exige exatamente
  `ops.apigateway.amazonaws.com`.
- Deploy, `EcsSlr` (ARN):
  `iam:CreateServiceLinkedRole`. Mesma razão, com account/prefixo canônico e
  `iam:AWSServiceName = ecs.amazonaws.com`.
- Deploy, `ApiWrites` (ARN): `apigateway:PATCH` e
  `apigateway:POST`. O API ID e os IDs dos subrecursos são gerados; o path fica
  limitado a `/apis/*` na região aprovada e toda chamada exige
  `aws:ResourceTag/Profile = aws-demo`.
- Deploy, `TaggedRW` (ARN): `cloudfront:UpdateDistribution`,
  `cloudwatch:TagResource`, `ec2:AssociateRouteTable`,
  `ec2:AuthorizeSecurityGroupEgress`, `ec2:AuthorizeSecurityGroupIngress`,
  `ec2:ModifySubnetAttribute`, `ec2:ModifyVpcAttribute`,
  `ecs:UpdateClusterSettings`,
  `ecs:UpdateService`, `logs:PutRetentionPolicy`, `logs:TagResource`,
  `sqs:SetQueueAttributes` e `sqs:TagQueue`.
  IDs gerados usam wildcard, mas account/região e prefixos de log/fila/alarm
  permanecem no ARN e toda ação exige `aws:ResourceTag/Profile = aws-demo`.
- Deploy, `CfTagCreate` (ARN): `cloudfront:TagResource`. O distribution ID
  ainda é desconhecido; account fica no ARN e
  `aws:RequestTag/Profile = aws-demo` é obrigatório.
- Deploy, `CognitoRW` (ARN):
  `cognito-idp:CreateUserPoolClient`, `cognito-idp:UpdateUserPool` e
  `cognito-idp:UpdateUserPoolClient`. O user pool ID é gerado e essas ações não
  expõem uma tag condition utilizável na matriz oficial; account/região, conta
  isolada e identidade do state/plano fecham a fronteira.
- Deploy, `CognitoTag` (ARN): `cognito-idp:TagResource`. O user pool ID é
  gerado; account/região ficam no ARN e o request deve conter
  `Profile = aws-demo`.
- Deploy, `AlarmCreate` (ARN): `cloudwatch:PutMetricAlarm`. Somente o
  sufixo do alarm name usa wildcard após `${NAME_PREFIX}-demo-`; account/região
  e `aws:RequestTag/Profile = aws-demo` permanecem obrigatórios.
- Deploy, `CloudMapNew` (ARN): `servicediscovery:CreateService`. IDs de
  namespace/service são gerados; account/região e
  `aws:RequestTag/Profile = aws-demo` limitam a criação.
- Deploy, `Ec2Create` (ARN): `ec2:CreateRouteTable`,
  `ec2:CreateSecurityGroup`, `ec2:CreateSubnet`, `ec2:CreateTags`,
  `ec2:CreateVpc` e `ec2:CreateVpcEndpoint`. IDs EC2 ainda não existem;
  account/região ficam no ARN e todas as chamadas exigem
  `aws:RequestTag/Profile = aws-demo`.
- Deploy, `TaskNew` (ARN): `ecs:RegisterTaskDefinition`. Somente a
  revisão gerada usa wildcard; family, account e região são exatos, e o request
  exige `Profile = aws-demo`, CPU numérica `256` e memória numérica `512`.
- Teardown, `DestroyDemoBucketObjects` (ARN): `s3:DeleteObject` e
  `s3:DeleteObjectVersion`. O wildcard cobre qualquer key/version somente nos
  três buckets de nome exato; esvaziá-los é pré-requisito técnico de delete.
- Teardown, `DestroyDemoApiGateway` (ARN): `apigateway:DELETE` e
  `apigateway:PATCH`. API/subresource IDs são gerados e o path fica restrito a
  `/apis/*` na região; `aws:ResourceTag/Profile = aws-demo` e o gate de
  state/plano precisam provar a identidade antes do destroy.
- Teardown, `DestroyUntaggableCloudFrontPolicies` (ARN):
  `cloudfront:DeleteCachePolicy`, `cloudfront:DeleteOriginAccessControl` e
  `cloudfront:DeleteResponseHeadersPolicy`. Os três IDs são gerados e esses
  tipos não aceitam tags; account e tipo ficam no ARN e nomes/IDs precisam vir
  do state/plano aprovado.
- Teardown, `DestroyDemoCognitoClient` (ARN):
  `cognito-idp:DeleteUserPoolClient`. O pool ID é gerado; account/região e
  identidade pré-destroy do state/plano limitam o alvo.
- Teardown, `DestroyTaggedDemoResources` (ARN):
  `cloudfront:DeleteDistribution`, `cloudfront:UntagResource`,
  `cloudfront:UpdateDistribution`, `cloudwatch:DeleteAlarms`,
  `cognito-idp:DeleteUserPool`, `ec2:DeleteRouteTable`,
  `ec2:DeleteSecurityGroup`, `ec2:DeleteSubnet`, `ec2:DeleteTags`,
  `ec2:DeleteVpc`, `ec2:DeleteVpcEndpoints`, `ec2:DisassociateRouteTable`,
  `ec2:RevokeSecurityGroupEgress`, `ec2:RevokeSecurityGroupIngress`,
  `ecs:DeleteCluster`, `ecs:DeleteService`, `ecs:UpdateService`,
  `logs:DeleteLogGroup`,
  `servicediscovery:DeleteNamespace`, `servicediscovery:DeleteService` e
  `sqs:DeleteQueue`. IDs gerados usam wildcard, mas account/região,
  names/prefixos disponíveis e
  `aws:ResourceTag/Profile = aws-demo` são obrigatórios; o gate ainda compara
  todos os valores `before` com a identidade canônica.

As matrizes oficiais que justificam resource types e condition keys são:
[ECR](https://docs.aws.amazon.com/service-authorization/latest/reference/list_ecr.html),
[CloudFront](https://docs.aws.amazon.com/service-authorization/latest/reference/list_cloudfront.html),
[EC2](https://docs.aws.amazon.com/service-authorization/latest/reference/list_ec2.html),
[ECS](https://docs.aws.amazon.com/service-authorization/latest/reference/list_amazonelasticcontainerservice.html),
[IAM](https://docs.aws.amazon.com/service-authorization/latest/reference/list_identityandaccessmanagementiam.html),
[Cognito User Pools](https://docs.aws.amazon.com/service-authorization/latest/reference/list_amazoncognitouserpools.html),
[CloudWatch Logs](https://docs.aws.amazon.com/service-authorization/latest/reference/list_amazoncloudwatchlogs.html),
[Cloud Map](https://docs.aws.amazon.com/service-authorization/latest/reference/list_servicediscovery.html),
[SQS](https://docs.aws.amazon.com/service-authorization/latest/reference/list_amazonsqs.html),
[API Gateway](https://docs.aws.amazon.com/service-authorization/latest/reference/list_amazonapigatewaymanagement.html)
e [Resource Groups Tagging API](https://docs.aws.amazon.com/service-authorization/latest/reference/list_resourcegroupstaggingapi.html).

As policies versionadas são uma especificação para revisão, não prova de que a
AWS já aceitou todas as ações necessárias. O primeiro exercício real deve ser
acompanhado por um administrador, falhar fechado diante de permissão ausente e
originar uma revisão explícita; não se deve acrescentar wildcard para “fazer
passar”.

Referências para o bootstrap automático e limitado das service-linked roles:

- [ECS: service-linked role para clusters](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/using-service-linked-roles-for-clusters.html);
- [API Gateway: service-linked role](https://docs.aws.amazon.com/apigateway/latest/developerguide/using-service-linked-roles.html).

## Bootstrap externo obrigatório

Antes da primeira fundação, um administrador deve preparar fora deste
repositório:

1. bucket S3 de state privado, versionado e criptografado com SSE-S3, configurado
   para a chave fixa `demo/sen-67/terraform.tfstate` e lock nativo `.tflock`; o
   objeto de state não deve ser criado manualmente, pois a primeira fundação
   reconhece a ausência canônica e o primeiro apply o grava; uma chave KMS
   gerenciada pelo cliente exigiria permissões adicionais que este contrato
   deliberadamente não concede;
2. provider OIDC do GitHub na conta AWS e três roles independentes a partir do
   contrato versionado; configure `MaxSessionDuration = 3600` na role de plan e
   `MaxSessionDuration = 7200` nas roles de deploy e teardown;
3. environments `aws-demo-plan`, `aws-demo-deploy` e `aws-demo-teardown`, cada
   um com exatamente o usuário `HiRenan` (`id = 107653306`) como reviewer,
   `prevent_self_review = false`, `can_admins_bypass = false` e uma única custom
   deployment branch literal `main`;
4. domínio DNS e certificado ACM válido de `us-east-1`, já exigidos pela SEN-67;
5. variáveis comuns abaixo e o secret de e-mail do Budget em cada environment
   aplicável.

| Tipo | Nome | Uso |
| --- | --- | --- |
| variável | `AWS_DEMO_ACCOUNT_ID` | conta de destino com 12 dígitos |
| variável | `AWS_DEMO_REGION` | região da demo |
| variável | `AWS_DEMO_AVAILABILITY_ZONE` | AZ única aprovada |
| variável | `AWS_DEMO_STATE_BUCKET` | bucket externo de state |
| variável | `AWS_DEMO_FRONTEND_DOMAIN_NAME` | domínio HTTPS real |
| variável | `AWS_DEMO_FRONTEND_CERTIFICATE_ARN` | certificado ACM aprovado |
| variável por environment | `AWS_DEMO_PLAN_ROLE_ARN`, `AWS_DEMO_DEPLOY_ROLE_ARN` ou `AWS_DEMO_TEARDOWN_ROLE_ARN` | role exclusiva da operação |
| secret | `AWS_DEMO_BUDGET_ALERT_EMAIL` | destinatário real do Budget |
| secret de runtime | `AWS_DEMO_SMOKE_BEARER_TOKEN` | configurado depois da fundação; JWT efêmero emitido imediatamente antes do runtime |

Reviewers, branch policy, secrets e variables são controles externos do GitHub;
YAML não consegue criá-los. O preflight consegue comprovar via API somente a
forma efetiva já configurada e falha antes de OIDC quando ela não é exata. Na
data desta entrega, uma consulta somente leitura comprovou os três environments
com o operador único, bypass administrativo desabilitado e branch `main` exatos.
Essa evidência não cria nem congela a configuração: o preflight precisa repeti-la
em cada execução. A fundação não exige usuário Cognito ou token. O runtime usa a
baseline SEN-46 fixa do contrato e permanece bloqueado enquanto a autenticação
do smoke não estiver configurada. A SEN-68 não cria senha ou usuário durante a
implementação e não acrescenta API, autenticação da aplicação ou UI.

O token automático `GITHUB_TOKEN` do preflight recebe apenas `actions: read` e
`contents: read`. Segundo a [API REST oficial de environments](https://docs.github.com/en/rest/deployments/environments?apiVersion=2022-11-28)
e a [API de deployment branch policies](https://docs.github.com/en/rest/deployments/branch-policies?apiVersion=2022-11-28),
essas leituras são suficientes para observar protection rules e a allowlist de
branches; qualquer `403`, `404`, paginação, environment duplicado ou schema fora
do contrato é recusa, nunca autorização implícita.

## Evidência do subject OIDC

Em 23/08/2026, uma consulta somente leitura ao endpoint
`GET /repos/HiRenan/senai-prescriptive-maintenance/actions/oidc/customization/sub`,
com `X-GitHub-Api-Version: 2026-03-10`, respondeu HTTP 200, selecionou essa mesma
versão e informou:

```json
{
  "use_default": true,
  "use_immutable_subject": false,
  "sub_claim_prefix": "repo:HiRenan@107653306/senai-prescriptive-maintenance@1342357031"
}
```

O repositório foi criado depois da data em que a documentação do GitHub passou a
descrever IDs imutáveis no `sub` como padrão para repositórios novos. Nesse
contexto, `use_default: true` mantém o padrão efetivo do GitHub e
`use_immutable_subject: false` registra apenas que não houve opt-in explícito no
nível do repositório; o campo não invalida o prefixo imutável devolvido pela
própria API. O prefixo observado contém os IDs numéricos do owner e do
repositório e é a identidade exata fixada neste contrato.

Essa leitura é evidência da configuração atual, não substitui a prova do claim
emitido em runtime. Antes do bootstrap, o administrador deve consultar novamente
o mesmo endpoint, exigir `use_default: true` e comparar o
`sub_claim_prefix` byte a byte com o contrato. Qualquer divergência interrompe o
bootstrap e exige nova revisão. Com a evidência vigente, não há motivo para
alterar a configuração OIDC nem manter trust nominal ou dual; as três roles devem
aceitar somente os subjects imutáveis exatos, e o primeiro plan protegido deve
confirmar a assunção real.

Referências primárias:

- [GitHub: OpenID Connect reference](https://docs.github.com/en/actions/reference/security/oidc);
- [GitHub: configuring OIDC in Amazon Web Services](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws);
- [GitHub REST API: OIDC subject customization](https://docs.github.com/en/rest/actions/oidc);
- [AWS IAM: GitHub OIDC provider](https://docs.aws.amazon.com/IAM/latest/UserGuide/id_roles_providers_create_oidc.html);
- [Terraform S3 backend locking](https://developer.hashicorp.com/terraform/language/backend/s3#state-locking).

## Runbook sanitizado do usuário e JWT efêmeros

O app client habilita exatamente `ALLOW_USER_SRP_AUTH`,
`ALLOW_REFRESH_TOKEN_AUTH` e, como único fluxo adicional para este runbook,
`ALLOW_ADMIN_USER_PASSWORD_AUTH`. `ALLOW_USER_PASSWORD_AUTH` e custom auth
permanecem desabilitados. O operador autorizado usa uma identidade humana já
existente e separada das três roles de entrega, com somente
`cognito-idp:AdminCreateUser`, `cognito-idp:AdminSetUserPassword`,
`cognito-idp:AdminInitiateAuth` e `cognito-idp:AdminDeleteUser` no ARN exato do
user pool. Essas permissões administrativas não pertencem ao workflow.

Depois de `FOUNDATION-AWS-DEMO`, obtenha os outputs não secretos
`cognito_user_pool_id` e `cognito_client_id` pelo canal administrativo aprovado.
Em um shell Bash efêmero, com AWS CLI e GitHub CLI já autenticados pela identidade
do operador, execute o bloco abaixo. O bloco desliga tracing e histórico, gera
duas senhas somente em memória, entrega todos os campos sensíveis à AWS CLI por
JSON em stdin e entrega o access token ao GitHub CLI também por stdin. Não copie
o bloco para um arquivo, não use `--debug` e não ative `set -x`.

```bash
set +x
set -euo pipefail
set +o history
export HISTFILE=/dev/null
export AWS_PAGER=""

read -r -p "Cognito user pool ID: " AWS_DEMO_USER_POOL_ID
read -r -p "Cognito public client ID: " AWS_DEMO_CLIENT_ID
read -r -p "Synthetic smoke email: " AWS_DEMO_SMOKE_EMAIL
[[ "$AWS_DEMO_USER_POOL_ID" =~ ^[a-z]{2}-[a-z]+-[0-9]_[A-Za-z0-9]+$ ]]
[[ "$AWS_DEMO_CLIENT_ID" =~ ^[a-z0-9]+$ ]]
[[ "$AWS_DEMO_SMOKE_EMAIL" =~ ^[^[:space:]@]+@[^[:space:]@]+$ ]]

export AWS_DEMO_USER_POOL_ID AWS_DEMO_CLIENT_ID AWS_DEMO_SMOKE_EMAIL
export AWS_DEMO_TEMP_PASSWORD="Aa1!$(openssl rand -hex 24)"
export AWS_DEMO_SMOKE_PASSWORD="Zz2@$(openssl rand -hex 24)"

python - <<'PY' | aws cognito-idp admin-create-user \
  --cli-input-json file:///dev/stdin --no-cli-pager >/dev/null
import json
import os

print(json.dumps({
    "UserPoolId": os.environ["AWS_DEMO_USER_POOL_ID"],
    "Username": os.environ["AWS_DEMO_SMOKE_EMAIL"],
    "TemporaryPassword": os.environ["AWS_DEMO_TEMP_PASSWORD"],
    "MessageAction": "SUPPRESS",
    "UserAttributes": [
        {"Name": "email", "Value": os.environ["AWS_DEMO_SMOKE_EMAIL"]},
        {"Name": "email_verified", "Value": "true"},
    ],
}))
PY

python - <<'PY' | aws cognito-idp admin-set-user-password \
  --cli-input-json file:///dev/stdin --no-cli-pager >/dev/null
import json
import os

print(json.dumps({
    "UserPoolId": os.environ["AWS_DEMO_USER_POOL_ID"],
    "Username": os.environ["AWS_DEMO_SMOKE_EMAIL"],
    "Password": os.environ["AWS_DEMO_SMOKE_PASSWORD"],
    "Permanent": True,
}))
PY

cognito_auth_payload() {
  python - <<'PY'
import json
import os

print(json.dumps({
    "AuthFlow": "ADMIN_USER_PASSWORD_AUTH",
    "ClientId": os.environ["AWS_DEMO_CLIENT_ID"],
    "UserPoolId": os.environ["AWS_DEMO_USER_POOL_ID"],
    "AuthParameters": {
        "USERNAME": os.environ["AWS_DEMO_SMOKE_EMAIL"],
        "PASSWORD": os.environ["AWS_DEMO_SMOKE_PASSWORD"],
    },
}))
PY
}
AWS_DEMO_SMOKE_BEARER_TOKEN="$(
  cognito_auth_payload |
    aws cognito-idp admin-initiate-auth \
      --cli-input-json file:///dev/stdin \
      --query AuthenticationResult.AccessToken \
      --output text --no-cli-pager
)"
unset -f cognito_auth_payload
export AWS_DEMO_SMOKE_BEARER_TOKEN
[[ "$AWS_DEMO_SMOKE_BEARER_TOKEN" =~ ^eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$ ]]
printf '%s' "$AWS_DEMO_SMOKE_BEARER_TOKEN" |
  gh secret set AWS_DEMO_SMOKE_BEARER_TOKEN --env aws-demo-deploy
```

Em seguida, sem fechar o shell, solicite o dispatch `DEPLOY-AWS-DEMO` para o HEAD
exato de `main`. O token deve ter acabado de ser emitido: o controlador exige
6.000 segundos restantes para cobrir o job de 90 minutos e sua margem. Depois
que o workflow terminar, com sucesso **ou falha**, remova primeiro o secret do
environment e depois o usuário efêmero; o delete também usa stdin e não revela a
identidade nos argumentos:

```bash
set +x
set +e
gh secret delete AWS_DEMO_SMOKE_BEARER_TOKEN --env aws-demo-deploy
secret_cleanup_status=$?
python - <<'PY' | aws cognito-idp admin-delete-user \
  --cli-input-json file:///dev/stdin --no-cli-pager >/dev/null
import json
import os

print(json.dumps({
    "UserPoolId": os.environ["AWS_DEMO_USER_POOL_ID"],
    "Username": os.environ["AWS_DEMO_SMOKE_EMAIL"],
}))
PY
user_cleanup_status=$?
unset AWS_DEMO_SMOKE_BEARER_TOKEN AWS_DEMO_SMOKE_PASSWORD
unset AWS_DEMO_TEMP_PASSWORD AWS_DEMO_SMOKE_EMAIL
unset AWS_DEMO_CLIENT_ID AWS_DEMO_USER_POOL_ID
if ((secret_cleanup_status != 0 || user_cleanup_status != 0)); then
  printf '%s\n' 'A limpeza efêmera falhou; acione o administrador.' >&2
  exit 1
fi
```

Não use opções como password/auth parameters na linha de comando, não grave o
JSON em disco, não imprima a variável do token e não reutilize o usuário. Os
comandos seguem as referências oficiais de
[AdminCreateUser](https://docs.aws.amazon.com/cognito-user-identity-pools/latest/APIReference/API_AdminCreateUser.html),
[AdminSetUserPassword](https://docs.aws.amazon.com/cognito-user-identity-pools/latest/APIReference/API_AdminSetUserPassword.html),
[AdminInitiateAuth](https://docs.aws.amazon.com/cognito-user-identity-pools/latest/APIReference/API_AdminInitiateAuth.html),
[AdminDeleteUser](https://docs.aws.amazon.com/cognito-user-identity-pools/latest/APIReference/API_AdminDeleteUser.html)
[AWS CLI `--cli-input-json`](https://docs.aws.amazon.com/cli/latest/userguide/cli-usage-parameters-file.html)
e [GitHub CLI `gh secret set`](https://cli.github.com/manual/gh_secret_set).

## Primeira implantação em dois dispatches e smoke

A ordem operacional é obrigatória porque o user pool Cognito não existe antes da
fundação e, portanto, um JWT com issuer e audience válidos não pode ser emitido
antecipadamente:

1. concluir o bootstrap externo de state, OIDC, roles, environments, domínio,
   certificado, variáveis comuns e e-mail do Budget;
2. executar o workflow protegido com `FOUNDATION-AWS-DEMO` e o HEAD exato de
   `main`; esse dispatch não recebe baseline SEN-46 nem token de smoke, cria todos
   os recursos com ECR vazio e ECS em escala zero e termina verde somente após um
   segundo plano integralmente `no-op` comprovar state completo, digest
   placeholder e `desired_count = 0`;
3. a partir daqui, `PLAN-AWS-DEMO` pode inspecionar o state completo sem apply;
   ele não é uma etapa de bootstrap e não deve ser executado antes da fundação;
4. no user pool recém-criado, o operador segue o runbook sanitizado, cria o
   usuário efêmero fora do Terraform, emite um access token de duas horas e
   configura `AWS_DEMO_SMOKE_BEARER_TOKEN`; a baseline SEN-46 já é a constante
   imutável `d45bcabfb6de89c6bac2ec2aa6180bce353be7c1`;
5. executar um novo dispatch protegido com `DEPLOY-AWS-DEMO` e o HEAD atual de
   `main`. State vazio, parcial ou fora da allowlist é recusado antes do build.

`FOUNDATION-AWS-DEMO` pode ser repetido somente sobre uma fundação completa ainda
em placeholder e sem drift; nesse caso, o plano `no-op` encerra verde. O modo
recusa state parcial e também um runtime já implantado. `DEPLOY-AWS-DEMO` aceita
a fundação completa em placeholder e, para recuperação idempotente ou rollout de
novo SHA, um runtime completo já existente; seu gate continua permitindo somente
a troca da task definition e a atualização do serviço ECS.

No dispatch de runtime, o runner:

1. valida antes de qualquer mutação AWS a baseline SEN-46 e a estrutura do JWT;
2. valida e seleciona explicitamente o builder criado no `DOCKER_CONFIG`
   isolado do run;
3. consulta no ECR a tag canônica e imutável `sha-<source_sha>`;
4. se a tag não existir de forma canônica, autentica com senha temporária,
   constrói `apps/api/Dockerfile` para `linux/amd64` e faz push;
5. lê a metadata com parser estrito e exige que o ECR devolva o mesmo digest
   `sha256:...`; se a tag já existir com exatamente um digest válido, reutiliza o
   digest sem login, build ou push;
6. rejeita resposta ambígua, falha diferente de `ImageNotFound` ou divergência de
   digest, sem confiar apenas na tag;
7. cria um plano que só pode substituir a task definition e atualizar o
   serviço ECS, ou ser integralmente idempotente. O gate valida `before`,
   `after` e `after_unknown`, exige exatamente
   `repository@sha256:<digest verificado>` e mantém CPU, memória, environment,
   roles, command/entrypoint, health check e ports; a expressão do service deve
   apontar para a nova revisão da task;
8. aplica o plano salvo e relê o state privado, comprovando novamente imagem por
   digest e service ligado ao ARN exato da task revision;
9. chama anonimamente `GET /health/ready` e um `POST /analysis` sintético e exige
   `401` ou `403` nos dois, sem ler ou imprimir seus corpos;
10. somente depois executa readiness autenticada e os cenários sintéticos
    normal, falha documentada e recusa sem documentação.

A tag deriva do HEAD exato e protegido de `main`, e o repositório ECR rejeita
sobrescrita. Assim, repetir o mesmo deploy depois de uma falha tardia de runtime
ou smoke reaproveita o digest já publicado; não tenta sobrescrever a tag
imutável. O reuso continua fail-closed e não aceita catálogo parcial ou schema
inesperado.

O smoke aceita apenas endpoint HTTPS regional do API Gateway, JWT com estrutura
canônica e respostas JSON fechadas. O preflight anônimo rejeita `2xx`, redirect
ou qualquer status fora de `401`/`403` sem interpretar o corpo. O smoke não segue
redirect, limita tamanho e tempo e não imprime endpoint, token, request ou
resposta. Overrides de raiz TLS e `SSLKEYLOGFILE` fecham o gate antes da conexão.
Ele prova integração
operacional dos contratos quando SEN-46 estiver disponível; não substitui testes
de domínio nem comprova segurança semântica universal.

## Teardown e prova negativa

O teardown só aplica um plano em que todo recurso presente pertence à allowlist
gerenciada e possui exclusão exata. Antes de gerar o destroy, o controlador puxa
o snapshot bruto para memória, valida schema fechado e reconstrói cada address a
partir de type/name/index. Para cada tipo, exige conta/região em todo ARN, formato
canônico de ID, nome/família/bucket/prefixo esperado, tags explícitas e `tags_all`
da demo, além dos vínculos de rede com a VPC aprovada. Relações entre API e seus
subrecursos, user pool e app client, repositório e lifecycle ECR, cluster, task e
service ECS também precisam coincidir; a task mantém digest, CPU, memória,
roles e contrato de container canônicos. O mesmo gate valida os valores `before`
do destroy e recusa qualquer `after_unknown`; address e type
corretos com nome/tag de produção continuam sendo foreign resource e fecham a
operação. O snapshot nunca é persistido ou impresso.

Um state completo ou um subconjunto não vazio deixado por apply interrompido
pode ser removido; recurso fora da allowlist fecha o gate. State já vazio pula o
apply e segue para a prova negativa. Ao final, `terraform state list` precisa
estar vazio e duas provas se complementam:

- busca por três tags fixas com Resource Groups Tagging API;
- scans explícitos e limitados para S3, ECR, ECS, Logs, SQS, IAM, Cognito, API
  Gateway, Cloud Map, Budgets, CloudFront, VPCs, subnets, route tables, security
  groups, endpoints e alarmes.

Uma consulta que falha, muda schema, excede o limite ou encontra qualquer item
do escopo reprova a operação. O inventário não afirma ausência de recursos fora
da conta/região/nomes/tags aprovados e continua sujeito à consistência eventual
dos serviços AWS.

## Validação sem AWS

Os gates locais não usam credencial, endpoint ou subprocesso AWS:

```powershell
uv run --frozen python infra/aws/demo/scripts/delivery_policy.py
uv run --frozen python infra/aws/demo/scripts/delivery_policy.py `
  --render-directory <diretório-local-novo>
uv run --frozen python infra/aws/demo/scripts/delivery_regression.py
uv run --frozen poe check
uv run --frozen poe hooks
uv run --frozen poe smoke
```

`delivery_regression.py` usa somente objetos sintéticos e um subprocesso Python
local para atacar trust/policies, workflows, plano/state, parser, limites de
stdout/stderr, ambientes herdados, smoke e inventário. A validação real de OIDC,
IAM, backend, build/push, apply, smoke remoto e teardown permanece pendência
externa explícita.

## Limites e riscos residuais

- O bootstrap e a prova real dependem de autorização e conta AWS; nenhum check
  offline substitui a avaliação das policies pelo serviço.
- A policy lógica de deploy ocupa 9.999 dos 10.240 caracteres inline e por isso
  é publicada em dois documentos customer-managed de 4.987 e 5.050 caracteres,
  com margens de 1.157 e 1.094. O bootstrap precisa usar exatamente essa divisão,
  medir de novo e validar cada documento no Access Analyzer; qualquer excesso ou
  ação faltante bloqueia o exercício.
- Os três environments/reviewer/restrições a `main` permanecem controles
  externos, embora a configuração efetiva tenha sido comprovada somente para
  leitura em 23/08/2026. A API REST e a segunda aprovação do mesmo operador
  precisam passar em cada exercício; os dois atos manuais não constituem revisão
  independente, e subjects de environment não carregam a ref no próprio claim.
- Um deploy interrompido depois da fundação pode deixar recursos cobrados. O
  operador deve conservar o state e acionar o teardown protegido; nunca apagar o
  backend para “limpar”.
- Se `main` avançar entre deploy e teardown, a operação seguinte exige o novo
  HEAD e reavalia integralmente plano/state. Qualquer divergência fecha o gate e
  requer revisão; não se permite voltar ao controlador histórico para contornar
  a falha.
- CloudFront, IAM e outros serviços possuem propagação eventual. Falhas de smoke
  ou inventário fecham a execução e exigem investigação, não bypass.
- A demo é single-AZ, usa backend de aplicação em memória e Budget não é trava
  transacional de gasto. Ela não representa produção ou alta disponibilidade.
- A baseline SEN-67 exige estruturalmente domínio próprio e certificado ACM em
  `us-east-1`: `aliases`, `viewer_certificate`, outputs e CORS usam esse domínio.
  Um follow-up pode avaliar o certificado HTTPS padrão do CloudFront e usar o
  domínio calculado da distribuição como origem CORS exata, sem wildcard; isso
  requer nova validação de plano e não pertence à SEN-68.
- O JWT de smoke é um segredo operacional de duas horas; emissão, instalação e
  remoção imediata do secret e do usuário permanecem responsabilidade do
  operador autorizado. O gate temporal reduz, mas não elimina, o risco de uma
  interrupção antes da limpeza.
