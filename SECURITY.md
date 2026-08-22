# Política de segurança

## Estado suportado

O projeto está em fundação e ainda não possui release publicada, ambiente de
produção ou compromisso contratual de suporte. Correções de segurança são
avaliadas para o estado corrente de `develop` e, quando afetarem uma baseline
estável, para `main` pelo fluxo de hotfix.

Branches de tarefa, commits antigos e cópias modificadas por terceiros não são
mantidos como versões suportadas. Esta política não estabelece prazo de
resposta ou correção.

## Reporte responsável e privado

Envie suspeitas de vulnerabilidade diretamente para Renan Mocelin pelo e-mail
[renanryuakame@gmail.com](mailto:renanryuakame@gmail.com), com o assunto
`[SECURITY] Relato privado`.

Inclua, quando possível:

- componente, branch ou commit afetado;
- condições necessárias para reproduzir o problema;
- impacto observado ou provável;
- demonstração mínima que não exponha dados ou segredos;
- sugestão de mitigação, se houver.

Não publique detalhes exploráveis em issues, discussions, pull requests ou
outros canais públicos. O contato avaliará o relato e combinará de forma
privada os próximos passos compatíveis com o estágio do projeto.

## Controles automatizados

O workflow `Security` executa nos pushes e pull requests para `develop` e
`main`, em agenda semanal e sob acionamento manual. Os jobs existentes analisam
Python e JavaScript/TypeScript com CodeQL, revisam dependências alteradas em
pull requests e varrem conteúdo e histórico relevante com Gitleaks.

O Dependabot verifica semanalmente os ecossistemas uv, npm, GitHub Actions e
Docker Compose e direciona seus pull requests a `develop`. Esses controles
reduzem risco conhecido, mas não substituem revisão, reporte privado ou gestão
de segredos.

## Segredos e credenciais

O repositório não deve conter credenciais reais. `.env`, chaves privadas,
tokens, arquivos de conta de serviço, dumps, volumes, logs sensíveis e
configurações locais permanecem fora do Git conforme `.gitignore`.

Os valores presentes em `.env.example` e `compose.yaml` são deliberadamente
fictícios e exclusivos para desenvolvimento local. Eles não representam um
cofre, não oferecem segurança para ambientes compartilhados e nunca devem ser
reutilizados em produção.

Antes de um commit ou pull request:

- revise o diff e os arquivos não rastreados;
- execute os hooks, incluindo a detecção de chave privada;
- confirme que exemplos usam somente valores fictícios;
- evite registrar valores de ambiente, cabeçalhos, URLs com credenciais ou
  conteúdo de arquivos locais nos logs.

## Resposta a vazamentos

Se um segredo real for exposto, considere-o comprometido mesmo que o commit
seja removido depois. Interrompa o uso, revogue ou rotacione a credencial no
provedor responsável, verifique acessos indevidos e substitua-a nos ambientes
legítimos antes de retomar o serviço.

A limpeza do histórico pode reduzir a exposição, mas não substitui a rotação.
Registre o incidente e as medidas tomadas sem republicar o valor comprometido.

## Dados e materiais do desafio

Os oito materiais originais são locais, somente leitura e não são conteúdo
público do repositório. Eles não devem ser copiados para commits, logs,
fixtures, testes, issues ou pull requests. Somente nomes, tamanhos e hashes já
registrados em `data/source-manifest.json` podem ser usados para conferir
integridade.

Fixtures versionadas devem ser inteiramente sintéticas. Dados brutos,
intermediários, processados ou gerados permanecem nos diretórios ignorados e
não devem conter credenciais ou identificadores reais.

## Práticas proibidas em produção

A fundação local não é uma configuração de produção. É proibido, sem projeto e
revisão específicos:

- reutilizar as credenciais fictícias ou os nomes padrão do Compose;
- expor PostgreSQL diretamente à internet ou ampliar o bind local sem controles;
- armazenar segredos em `.env` versionado, código, imagem ou log;
- executar com debug, mensagens sensíveis ou permissões excessivas;
- usar materiais originais, fixtures ou dados locais como dados de produção;
- considerar liveness como readiness ou como verificação de dependências;
- publicar a aplicação sem autenticação, autorização, criptografia em trânsito,
  gestão de segredos, backups e observabilidade definidos para o ambiente;
- tratar a configuração Docker Compose local como infraestrutura de deploy.

Qualquer implantação futura exige modelagem de ameaça, gestão de acesso,
política de dados, dependências auditadas e plano de resposta a incidentes
adequados ao ambiente real.
