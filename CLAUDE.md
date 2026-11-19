# heuderdev/helpers

Biblioteca de helpers PHP reutilizáveis (`heuderdev/helpers`) e a aplicação mínima que a consome. Repositório `helpers-joomla-vendor`.

## Estrutura

- `packages/heuderdev/helpers/src/`: os helpers. Classes estáticas, sem namespace, carregadas por classmap (`packages/heuderdev/helpers/composer.json`). Um arquivo por helper; exceções do helper ficam no mesmo arquivo.
- `src/IncludeHelper.php`: mapa de dependências entre helpers (`$dependencies`) e de classes cujo nome difere do arquivo (`$autoloadMap`). Usado onde não há Composer (Joomla).
- Raiz (`composer.json`, `index.php`, `download.php`): aplicação consumidora. Usa o pacote por repositório Composer `path` com `symlink: false`.
- `vendor/`: cópia gerada pelo Composer e **versionada no Git**.
- `exemplo/`, `exports/`, `logs/`: locais, ignorados pelo Git, com dados reais.

## Compatibilidade (exigência do mantenedor)

- Os helpers precisam funcionar em Joomla 3, 4, 5 e 6, Laravel e PHP puro, em **PHP 7 e PHP 8.3+**, com **MySQL e PostgreSQL**.
- A plataforma é detectada em runtime pelo `PlatformHelper` (`JVERSION`, `app()`).
- Os fontes ficam no nível de sintaxe PHP 7.0 e funcionam de 7.0 a 8.5 quando carregados pelo `IncludeHelper`, que é o caso do Joomla.
- Pela instalação via Composer, o piso é **PHP 7.4**. O `composer.json` do pacote declara `php >=7.4.0` e `joomla/database ^2.0 || ^3.0 || ^4.0`, e o Composer escolhe a versão pelo PHP do servidor:

  | PHP | `joomla/database` | `phpspreadsheet` |
  |---|---|---|
  | 7.4 | 2.x | 1.29+ |
  | 8.1 | 3.x | 5.x |
  | 8.3+ | 4.x | 5.x |

- Não aperte nem afrouxe essas faixas sem pedido e sem testar nas versões de PHP envolvidas.
- Regras de sintaxe, cabeçalho e SQL: `.claude/rules/helpers-php.md` (carregada ao mexer nos helpers).

## Fluxo de alteração

1. Edite somente em `packages/heuderdev/helpers/src/`. Nunca edite `vendor/` à mão.
2. Na raiz, rode `composer reinstall heuderdev/helpers` para espelhar em `vendor/`.
3. Rode `php -l` em cada arquivo alterado, com o PHP padrão e com o mais antigo instalado: `C:/laragon/bin/php/php-7.1.3-nts-Win32-VC14-x64/php.exe -n -l <arquivo>`. As versões instaladas são 7.1, 7.2, 7.4, 8.1, 8.2, 8.3, 8.4 e 8.5; não há 7.0.
4. Confira que `diff -rq packages/heuderdev/helpers/src vendor/heuderdev/helpers/src` não imprime nada.

Não há testes automatizados, lint configurado nem CI. Para testar comportamento, use um script temporário fora do repositório ou em `exemplo/`. Avise que o teste é manual.

Para criar um helper ou mudar dependências entre helpers, use a skill `novo-helper`. Para revisar compatibilidade sem editar, use o agent `revisor-compat`.

## Banco de dados

- O acesso passa por `DbConnectionHelper::register($nome, $config)`, `OrmTables::table($tabela, ['connection' => $nome])` e `DbTransactionHelper`.
- Sem `connection`, o padrão é a conexão do Joomla. Fora do Joomla isso termina em "Class JFactory not found".
- Scripts locais obtêm as conexões com `$config = require __DIR__ . '/config.php';` e passam `$config['UPLOAD']`. As chaves são `USERS`, `LEMBRETE`, `UPLOAD` e `ELASTICEMAIL`; `PORTAL2020` está dentro de um índice numérico (`$config[0]['PORTAL2020']`).

## Dados sensíveis

- `config.php` tem credenciais reais. Não leia, não imprima e não copie o arquivo (a leitura está bloqueada em `.claude/settings.json`).
- CSVs, `exports/` e `logs/` têm dados reais. Leia só o necessário para a tarefa e nunca cole o conteúdo em commits, artefatos ou serviços externos.

## Git

- Faça commit e push só quando o usuário pedir. Procedimento: skill `publicar-helpers`.
- Mensagens em português. Título no presente: "Adiciona X", "Usa Y em Z". Corpo com a lista do que mudou. `vendor/` atualizado entra no mesmo commit.
- `arquiteto.md` e `subregra.md` na raiz são instruções do usuário fora do Git. Não edite nem commite.

## Configuração do Claude

- `CLAUDE.md` e `.claude/` são mantidos pela ferramenta em `tools/claude_project/` ([README](tools/claude_project/README.md)).
- Edições manuais são permitidas: a ferramenta detecta por hash e não sobrescreve.
- Para preferências pessoais, use `CLAUDE.local.md` (ignorado pelo Git).
