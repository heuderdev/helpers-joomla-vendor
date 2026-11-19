Rode tudo a partir de C:\\laragon\\www\\php:

python -m tools.claude_project status

python -m tools.claude_project diff

python -m tools.claude_project prepare

1. Peça ao Claude: "Leia .claude-project/context/&lt;id&gt;/[context.md](http://context.md) e escreva a proposta".

2. Rode python -m tools.claude\_project validate --proposal &lt;id&gt;.

3. Rode python -m tools.claude\_project apply --proposal &lt;id&gt; para ver o diff sem gravar.

4. Rode python -m tools.claude\_project apply --proposal &lt;id&gt; --yes para aplicar.

Se uma aplicação for interrompida no meio, python -m tools.claude_project apply --recover desfaz o que foi gravado.

Se você editar à mão o [CLAUDE.md](http://CLAUDE.md) ou algum arquivo de .claude/, a ferramenta percebe e não sobrescreve. A versão proposta fica em .claude-project/conflicts/.