---
name: publicar-helpers
description: Verifica (reinstall, lint, sincronia com vendor) e faz commit e push das mudanças nos helpers. Só quando o usuário pedir commit/push.
disable-model-invocation: true
argument-hint: "[resumo opcional da mudança]"
allowed-tools: Bash(git status *), Bash(git diff *), Bash(git log *), Bash(php -l *), Bash(composer reinstall *)
---

# Publicar alterações dos helpers

## Quando usar

Quando o usuário pede "crie o commit e o push" ou "commit" depois de mudanças em `packages/heuderdev/helpers/` ou na aplicação da raiz.

## Quando não usar

- O usuário não pediu commit.
- Há mudança em `config.php`, ou só em `exemplo/` (ambos ignorados pelo Git).
- `arquiteto.md` e `subregra.md` não entram em commit sem pedido explícito.

## Entradas

`$ARGUMENTS`: um resumo opcional. Se não vier, escreva a mensagem a partir do diff.

## Procedimento

1. Rode `git status --short` e `git diff --stat`. Liste o que vai entrar. Se aparecer algo fora do escopo da tarefa, pergunte antes de incluir.
2. Se algum arquivo de `packages/heuderdev/helpers/src/` mudou, rode `composer reinstall heuderdev/helpers` na raiz.
3. Rode `php -l` em cada `.php` alterado.
4. Confira que `diff -rq packages/heuderdev/helpers/src vendor/heuderdev/helpers/src` não imprime nada.
5. Faça o stage pelos caminhos, nunca com `git add -A`:
   - os arquivos alterados em `packages/`;
   - `vendor/heuderdev/helpers/`;
   - `vendor/composer/` e `composer.lock`, se mudaram.
6. Commite com a mensagem em português:
   - título no presente, por exemplo "Adiciona X ao UserHelper";
   - linha em branco;
   - lista curta do que mudou e por quê;
   - as linhas de atribuição que a sessão atual indicar.

   Passe a mensagem com heredoc.
7. Rode `git push origin <branch atual>`.
8. Informe o hash curto, o título e o resultado do push.

## Verificações

- `git status --short` depois do commit mostra só o que ficou de fora de propósito.
- `git log -1 --stat` mostra `src` e `vendor` juntos quando o helper mudou.

## Falhas

- **Hook falhou:** corrija a causa. Não use `--no-verify`.
- **Push rejeitado:** rode `git fetch`, mostre a divergência e pergunte. Nunca use `--force` (também bloqueado em `.claude/settings.json`).
- **Lint ou sincronia falharam:** não commite. Mostre o erro.
