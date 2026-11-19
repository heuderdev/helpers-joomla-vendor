---
paths:
  - "packages/heuderdev/helpers/src/**/*.php"
---

# Regras para os helpers

## Sintaxe: precisa rodar em PHP 7 e 8.3+

Escreva código novo no nível PHP 7.0. Ele roda de 7.0 a 8.x.

**Não use** (versão mínima entre parênteses):
- tipos anuláveis `?tipo` (7.1)
- retorno `: void` (7.1)
- `catch (A | B $e)` (7.1)
- `private const` / `public const`, ou seja, constante com visibilidade (7.1)
- destructuring `[$a, $b] = ...` (7.1); use `list($a, $b) = ...`
- `array_key_first`, `array_key_last`, `is_countable` e `JSON_THROW_ON_ERROR` (7.3)
- `??=` (7.4)
- propriedades tipadas (7.4)
- `fn () =>` (7.4)
- `match` (8.0)
- `?->` (8.0)
- union types e `mixed` (8.0)
- named arguments (8.0)
- `catch (X)` sem variável (8.0)
- atributos `#[...]` (8.0)
- vírgula final na lista de parâmetros (8.0)
- `str_contains`, `str_starts_with`, `str_ends_with` (8.0)
- `enum`, `readonly`, `never` (8.1)

**Pode usar**: `[]`, closures, `Throwable`, `??`, type hints escalares e retornos não anuláveis.

Na prática, os arquivos quase não usam tipos. Siga o estilo do arquivo que estiver editando.

**Situação atual:**
- Os 33 helpers passam no `php -l` de PHP 7.1 a 8.5.
- O `python -m tools.claude_project scan` aponta "PHP mínimo pela sintaxe 7.0" para todos.
- Mantenha assim. Se o `scan` listar algum arquivo acima de 7.0, trate como regressão.

**Para aceitar `null` sem `?tipo`:**
- Não escreva `string $x = null`. É anulável implícito, que o PHP 8.4 marca como deprecated.
- Deixe o parâmetro sem tipo e valide no início do método.
- Modelos: `EmailSendHelper::optionalString` e `UserHelper::assertOptionalCallable`. Os dois mantêm o `TypeError` para valor inválido.

## Comportamentos que mudaram no PHP 8.x

- Passar `null` a funções internas que não aceitam `null` está deprecated desde 8.1. Exemplos: `trim`, `strlen`, `str_replace`, `htmlspecialchars`. Converta antes com `(string)`.
- Propriedades dinâmicas estão deprecated desde 8.2. Declare toda propriedade.
- A interpolação `"${var}"` está deprecated desde 8.2. Use `"{$var}"`.
- `utf8_encode` e `utf8_decode` estão deprecated desde 8.2. Use `mb_convert_encoding`.
- Parâmetro opcional antes de obrigatório gera deprecation desde 8.0.
- Comparação de número com string mudou no 8.0. Prefira `===` e casts explícitos.

## Cabeçalho do arquivo

- **Helper novo**: copie a guarda de `DbConnectionHelper.php`. Ela bloqueia só o acesso direto pela URL e funciona em Joomla, Laravel e PHP puro.
- **`defined('_JEXEC') or die`**: só em helper exclusivo de Joomla. Hoje são 14 com esse cabeçalho: ApiResponse, Audit, Cache, Crypto, Format, Http, Input, Lock, Mail, Permission, Queue, RateLimit, Settings e Webhook (todos com o sufixo `Helper`).
- **Dependência opcional de um desses 14**: `class_exists('X')` dispara o autoload e mata o script fora do Joomla. Use `class_exists($c, false) || (defined('_JEXEC') && class_exists($c))`. Modelo: `UserHelper::hasHelper`.

## Registro no IncludeHelper

Para helper novo, dependência nova ou classe extra no arquivo, atualize em `IncludeHelper.php`:
- `$dependencies`: helper => helpers que ele usa.
- `$autoloadMap`: classe => arquivo, quando o nome da classe difere do arquivo (exceções, por exemplo).
- `$classMap`: arquivo => classe, quando a classe principal tem outro nome (`ChunkUploadHelper` => `ChunkHelper`).

**O que entra em `$dependencies`:**
- Só as dependências **obrigatórias**.
- Uma dependência opcional, usada atrás de `class_exists` ou `hasHelper`, **não** entra. O `IncludeHelper` carrega as dependências antes do helper, e se uma delas for um dos 14 helpers com `die`, o script morre em silêncio fora do Joomla.
- Isso já aconteceu com `UserHelper` => `PermissionHelper`.

**Efeito no `loadAll()`:** registrar um helper faz o `loadAll()` declará-lo logo no início. Antes de registrar, confirme que o nome da classe não colide com outra classe global do site.

## Banco: MySQL e PostgreSQL

- Use `OrmTables` / `OrmBase` e `DbConnectionHelper`. Aceite a opção `connection` e repasse-a; o padrão `null` é a conexão do Joomla.
- SQL cru vai por `OrmTables::rawExecute($sql, $bindings, ['connection' => ...])` com placeholders `?`.
- Nomes de tabela e coluna passam pelo `quoteName` do driver. Nunca use crase nem aspas fixas no SQL.
- Para ramificar por banco, use `DbConnectionHelper::isPostgres($conexao)` ou `isMysql($conexao)`. Diferenças já tratadas no código:
  - upsert: `ON DUPLICATE KEY UPDATE` (MySQL) ou `ON CONFLICT ... DO UPDATE` (PostgreSQL)
  - boolean no PostgreSQL vai como `'1'`/`'0'`
  - `TRUNCATE` no MySQL faz commit implícito
- Transações e savepoints: use `DbTransactionHelper::begin/commit/rollback` ou `run()`. `SAVEPOINT` cru falha no driver mysqli do Joomla 4+, que usa prepared statements.
- `OrmBase::transaction()` embrulha a exceção em `RuntimeException`. A original fica em `getPrevious()`.

## Convenções observadas

- Métodos `public static`, com opções em array associativo (`array $options = []`).
- Comentários, mensagens de erro e chaves de retorno em português.
- `echo` e `exit` só aparecem em helpers de resposta ou download: `ApiResponseHelper`, `PermissionHelper`, `PlatformHelper`, `FileHelper`, `UploadMaster` e `WebhookHelper`. Helpers de dados retornam arrays de resultado ou lançam a exceção do próprio helper.
- O Joomla é detectado por `defined('_JEXEC')` ou `JVERSION`, nunca por `class_exists('JFactory')`.
- Não verificado aqui: a disponibilidade dos aliases `J*` (`JFactory`) no Joomla 5/6 depende do plugin de compatibilidade. Prefira `PlatformHelper`.
