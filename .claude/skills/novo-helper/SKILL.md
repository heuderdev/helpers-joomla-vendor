---
name: novo-helper
description: Cria um helper novo em packages/heuderdev/helpers/src ou adiciona dependência entre helpers, mantendo o IncludeHelper, o vendor/ e a compatibilidade PHP 7 / 8.3+, Joomla 3-6, Laravel e PHP puro.
when_to_use: 'Pedidos como "crie um helper", "novo Helper para...", "faça o helper X usar o helper Y", ou nova classe de exceção num helper.'
---

# Novo helper ou nova dependência entre helpers

## Quando usar

- Criar um arquivo novo em `packages/heuderdev/helpers/src/`.
- Fazer um helper passar a usar outro, ou declarar uma classe extra (por exemplo, uma exceção) no arquivo de um helper.

## Quando não usar

- Mudança interna num helper que não envolve outros helpers: siga só `.claude/rules/helpers-php.md`.
- Commit e push: use a skill `publicar-helpers`, que é acionada só pelo usuário.

## Pré-requisitos e entradas

- Nome do helper (sufixo `Helper`, PascalCase) e a responsabilidade dele.
- Helpers que ele vai usar. Confira se algum é dos 14 que fazem `die` fora do Joomla (lista na rule).
- Se acessa banco: em qual conexão e se precisa rodar em MySQL e PostgreSQL. A resposta padrão é sim.

## Procedimento

1. **Verifique se já existe.** Procure por `grep -l "function nomeParecido" packages/heuderdev/helpers/src/*.php`. Se houver algo parecido (por exemplo, `CsvHelper` antes de criar um importador), diga qual é a diferença antes de criar outro.
2. **Crie o arquivo `src/NomeHelper.php`:**
   - Copie a guarda de acesso direto de `DbConnectionHelper.php`, linhas 1 a 6.
   - Use `class NomeHelper` sem namespace, com métodos `public static`.
   - Sintaxe no nível PHP 7.0, conforme a rule.
3. **Dependências opcionais de helpers só-Joomla:** use o guard `class_exists($c, false) || (defined('_JEXEC') && class_exists($c))`, como em `UserHelper::hasHelper`.
4. **Banco:**
   - Aceite a opção `connection` e repasse-a para `OrmTables::table(...)`.
   - Use bindings `?` e `quoteName`.
   - Ramifique com `DbConnectionHelper::isPostgres()` quando a sintaxe mudar.
5. **Registre no `IncludeHelper.php`:**
   - em `$dependencies`: `'NomeHelper' => array('Dep1', 'Dep2')`;
   - em `$autoloadMap`: cada classe extra do arquivo, `'NomeHelperException' => 'NomeHelper'`.
6. **Nova dependência externa:** só com pedido explícito. Ela entra em `packages/heuderdev/helpers/composer.json`, com uma faixa compatível com PHP 7 quando existir. Na raiz, rode `composer update heuderdev/helpers`, o que muda o `composer.lock`.
7. **Espelhe no vendor:** na raiz, rode `composer reinstall heuderdev/helpers`.
8. **Teste manual:** faça um script temporário fora do repositório, ou em `exemplo/`, que carregue `vendor/autoload.php` e registre a conexão:

   ```php
   DbConnectionHelper::register('upload', $config['UPLOAD']);
   ```

   Rode-o com `php`. Não grave dados reais sem `dry_run` ou sem autorização.

## Verificações

- `php -l packages/heuderdev/helpers/src/NomeHelper.php` e `php -l packages/heuderdev/helpers/src/IncludeHelper.php` passam.
- `diff -rq packages/heuderdev/helpers/src vendor/heuderdev/helpers/src` não imprime nada.
- `python -m tools.claude_project scan`:
  - o helper não aparece em "Helpers fora do IncludeHelper";
  - "PHP mínimo pela sintaxe" do arquivo é 7.0.
- Opcional: peça ao agent `revisor-compat` uma revisão dos arquivos alterados.

## Critério de conclusão

O arquivo novo está registrado e espelhado em `vendor/`, o lint passou e foi executado um teste manual com a saída mostrada ao usuário. Se o teste não foi executado, diga isso explicitamente.

## Falhas

- **`php -l` falha:** corrija antes de seguir. Nunca deixe `vendor/` com código quebrado.
- **`composer reinstall` falha:** mostre o erro e não copie arquivos para `vendor/` à mão.
- **"Class JFactory not found" no teste:** faltou passar a opção `connection`.
- **O script morre sem mensagem:** algum helper com `defined('_JEXEC') or die` foi carregado fora do Joomla. Veja o passo 3.
