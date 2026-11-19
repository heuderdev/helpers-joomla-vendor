import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from tools.claude_project import cli
from tools.claude_project.common import (
    FrontmatterError, ToolError, _parse_yaml_subset, canonical_json, safe_path, sha256_bytes,
)

SKILL = """---
name: exemplo
description: Skill de exemplo para testes.
---

# Exemplo
"""
AGENT = """---
name: revisor
description: Revisa sem editar.
tools: Read, Grep
disallowedTools: Write, Edit
skills:
  - exemplo
---

Revise.
"""
RULE = """---
paths:
  - "packages/heuderdev/helpers/src/**/*.php"
---

Regra.
"""
CLAUDE_MD = "# Projeto\n\nVeja [a regra](.claude/rules/php.md).\n"


class ToolTestCase(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="cp test "))
        self.root = self.base / "projeto ação"
        src = self.root / "packages" / "heuderdev" / "helpers" / "src"
        src.mkdir(parents=True)
        (self.root / "composer.json").write_text('{"require": {"php": ">=7.0"}}\n', encoding="utf-8")
        (self.root / "index.php").write_text("<?php\necho 1;\n", encoding="utf-8")
        (self.root / "notas.txt").write_text("livre\n", encoding="utf-8")
        (self.root / "arquiteto.md").write_text("instrução humana\n", encoding="utf-8")
        (src / "IncludeHelper.php").write_text(
            "<?php\nclass IncludeHelper {\n private static $dependencies = array(\n  'AHelper' => array(),\n );\n}\n", encoding="utf-8")
        (src / "AHelper.php").write_text("<?php\nclass AHelper {\n public static function a($x = null) { return $x ?? 1; }\n}\n", encoding="utf-8")
        (src / "Relatório Mensal.php").write_text("<?php\nclass RelatorioMensal {}\n", encoding="utf-8")
        (self.root / "config.php").write_text("<?php return ['password' => 'S3nhaReal!x'];\n", encoding="utf-8")
        (self.root / ".env").write_text("DB_PASSWORD=S3nhaReal!x\n", encoding="utf-8")
        state = self.root / ".claude-project"
        state.mkdir()
        (state / "config.json").write_text(json.dumps({"use_gitignore": False}), encoding="utf-8")

    def tearDown(self):
        os.environ.pop("CLAUDE_PROJECT_FAIL_AFTER", None)
        shutil.rmtree(str(self.base), ignore_errors=True)

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--root", str(self.root)] + list(args))
        return code, out.getvalue() + err.getvalue()

    def write_proposal(self, ident, files, bases=None, extra=None):
        directory = self.root / ".claude-project" / "proposals" / ident
        if directory.exists():
            shutil.rmtree(str(directory))
        entries = []
        for path, text in files.items():
            target = directory / "files" / Path(*path.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(text.encode("utf-8"))
            entries.append({"caminho": path, "base_sha256": (bases or {}).get(path),
                            "fontes": ["index.php"] if path == "CLAUDE.md" else ["packages/heuderdev/helpers/src/*.php"],
                            "evidencias": ["teste"]})
        proposal = {"id": ident, "arquivos": entries, "obsoletos": []}
        proposal.update(extra or {})
        (directory / "proposal.json").write_text(json.dumps(proposal), encoding="utf-8")

    def full_files(self):
        return {
            "CLAUDE.md": CLAUDE_MD,
            ".claude/rules/php.md": RULE,
            ".claude/skills/exemplo/SKILL.md": SKILL,
            ".claude/agents/revisor.md": AGENT,
        }

    def first_apply(self):
        self.write_proposal("p1", self.full_files())
        self.assertEqual(self.run_cli("validate", "--proposal", "p1")[0], 0)
        code, out = self.run_cli("apply", "--proposal", "p1", "--yes")
        self.assertEqual(code, 0, out)

    def diff(self):
        code, out = self.run_cli("diff", "--json")
        self.assertEqual(code, 0)
        return json.loads(out)

    def manifest(self):
        return json.loads((self.root / ".claude-project" / "manifest.json").read_text(encoding="utf-8"))

    # 1
    def test_primeira_geracao(self):
        data = self.diff()
        self.assertTrue(data["primeira_geracao"])
        self.assertEqual(data["impacto"], "transversal")
        code, out = self.run_cli("prepare")
        self.assertEqual(code, 0)
        contexts = list((self.root / ".claude-project" / "context").glob("*/context.md"))
        self.assertEqual(len(contexts), 1)
        self.first_apply()
        self.assertEqual((self.root / "CLAUDE.md").read_text(encoding="utf-8"), CLAUDE_MD)
        manifest = self.manifest()
        self.assertEqual(manifest["baseline"]["proposta"], "p1")
        self.assertEqual(set(manifest["artefatos"]), set(self.full_files()))

    # 2
    def test_execucao_sem_mudancas_nao_reescreve(self):
        self.first_apply()
        inventory = self.root / ".claude-project" / "inventory.json"
        manifest = self.root / ".claude-project" / "manifest.json"
        before = (inventory.read_bytes(), inventory.stat().st_mtime_ns, manifest.read_bytes(), manifest.stat().st_mtime_ns)
        backups = len(list((self.root / ".claude-project" / "backups").iterdir()))
        self.run_cli("scan")
        code, out = self.run_cli("prepare")
        self.assertIn("Sem mudanças", out)
        code, out = self.run_cli("apply", "--proposal", "p1", "--yes")
        self.assertIn("Nada a aplicar", out)
        after = (inventory.read_bytes(), inventory.stat().st_mtime_ns, manifest.read_bytes(), manifest.stat().st_mtime_ns)
        self.assertEqual(before, after)
        self.assertEqual(backups, len(list((self.root / ".claude-project" / "backups").iterdir())))
        self.assertFalse(any(item["mudanca"] for item in self.diff()["fontes"]))

    # 3
    def test_arquivo_adicionado(self):
        self.first_apply()
        (self.root / "packages/heuderdev/helpers/src/BHelper.php").write_text("<?php class BHelper {}\n", encoding="utf-8")
        data = self.diff()
        item = [i for i in data["fontes"] if i["caminho"].endswith("BHelper.php")][0]
        self.assertEqual((item["mudanca"], item["impacto"]), ("adicionado", "transversal"))
        code, out = self.run_cli("scan")
        self.assertIn("Helpers fora do IncludeHelper: BHelper", out)

    # 4
    def test_arquivo_alterado(self):
        self.first_apply()
        (self.root / "index.php").write_text("<?php\necho 2;\n", encoding="utf-8")
        (self.root / "notas.txt").write_text("mudou\n", encoding="utf-8")
        data = {i["caminho"]: i for i in self.diff()["fontes"]}
        self.assertEqual(data["index.php"]["impacto"], "localizado")
        self.assertEqual(data["index.php"]["artefatos"], ["CLAUDE.md"])
        self.assertEqual(data["notas.txt"]["impacto"], "sem_impacto_conhecido")
        code, out = self.run_cli("prepare")
        context = next((self.root / ".claude-project" / "context").glob("*/context.md")).read_text(encoding="utf-8")
        self.assertIn("echo 2;", context)

    # 5
    def test_arquivo_removido_e_renomeacao(self):
        self.first_apply()
        src = self.root / "packages/heuderdev/helpers/src"
        (src / "AHelper.php").rename(src / "AHelperNovo.php")
        data = self.diff()
        kinds = {(i["caminho"].split("/")[-1], i["mudanca"]) for i in data["fontes"]}
        self.assertIn(("AHelper.php", "removido"), kinds)
        self.assertIn(("AHelperNovo.php", "adicionado"), kinds)
        self.assertEqual(len(data["renomeacoes_provaveis"]), 1)

    # 6
    def test_mudanca_transversal(self):
        self.first_apply()
        (self.root / "composer.json").write_text('{"require": {"php": ">=8.3"}}\n', encoding="utf-8")
        self.assertEqual(self.diff()["impacto"], "transversal")
        (self.root / "arquiteto.md").write_text("nova instrução\n", encoding="utf-8")
        data = self.diff()
        self.assertEqual(data["humanos"], [{"caminho": "arquiteto.md", "mudanca": "modificado"}])

    # 7
    def test_instrucao_editada_manualmente(self):
        self.first_apply()
        edited = CLAUDE_MD + "\nRegra humana importante.\n"
        (self.root / "CLAUDE.md").write_bytes(edited.encode("utf-8"))
        states = {i["caminho"]: i["estado"] for i in self.diff()["artefatos"]}
        self.assertEqual(states["CLAUDE.md"], "editado_manualmente")
        self.run_cli("prepare")
        context = next((self.root / ".claude-project" / "context").glob("*/context.md")).read_text(encoding="utf-8")
        self.assertIn("+Regra humana importante.", context)
        files = self.full_files()
        files["CLAUDE.md"] = CLAUDE_MD + "\nNovo gerado.\n"
        self.write_proposal("p2", files)
        self.assertEqual(self.run_cli("validate", "--proposal", "p2")[0], 0)
        code, out = self.run_cli("apply", "--proposal", "p2", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("Conflitos", out)
        self.assertEqual((self.root / "CLAUDE.md").read_text(encoding="utf-8"), edited)
        self.assertTrue((self.root / ".claude-project/conflicts/p2/CLAUDE.md").is_file())
        merged = edited + "Novo gerado.\n"
        files["CLAUDE.md"] = merged
        self.write_proposal("p3", files, bases={"CLAUDE.md": sha256_bytes(edited.encode("utf-8"))})
        self.run_cli("validate", "--proposal", "p3")
        code, out = self.run_cli("apply", "--proposal", "p3", "--yes")
        self.assertEqual(code, 0, out)
        self.assertEqual((self.root / "CLAUDE.md").read_text(encoding="utf-8"), merged)

    def test_arquivo_humano_preexistente_nao_e_sobrescrito(self):
        (self.root / "CLAUDE.md").write_text("# Escrito por uma pessoa\n", encoding="utf-8")
        self.write_proposal("p1", self.full_files())
        self.run_cli("validate", "--proposal", "p1")
        code, out = self.run_cli("apply", "--proposal", "p1", "--yes")
        self.assertEqual(code, 1)
        self.assertEqual((self.root / "CLAUDE.md").read_text(encoding="utf-8"), "# Escrito por uma pessoa\n")
        code, out = self.run_cli("apply", "--proposal", "p1", "--yes", "--skip-conflicts")
        self.assertEqual(code, 3)
        self.assertTrue((self.root / ".claude/rules/php.md").is_file())
        self.assertIsNone(self.manifest()["baseline"])

    def test_crlf_do_git_nao_e_edicao_manual(self):
        self.first_apply()
        target = self.root / "CLAUDE.md"
        target.write_bytes(target.read_bytes().replace(b"\n", b"\r\n"))
        self.assertIn(b"\r\n", target.read_bytes())
        states = {i["caminho"]: i["estado"] for i in self.diff()["artefatos"]}
        self.assertNotIn("CLAUDE.md", states)
        code, out = self.run_cli("apply", "--proposal", "p1", "--yes")
        self.assertIn("Nada a aplicar", out)

    # 8
    def test_yaml_invalido(self):
        files = self.full_files()
        files[".claude/skills/exemplo/SKILL.md"] = "---\nname: exemplo\ndescription: \"aberta, sem fechar\n---\n"
        self.write_proposal("p1", files)
        code, out = self.run_cli("validate", "--proposal", "p1")
        self.assertEqual(code, 1)
        self.assertIn("[ERRO] .claude/skills/exemplo/SKILL.md", out)
        code, out = self.run_cli("apply", "--proposal", "p1", "--yes")
        self.assertEqual(code, 1)
        self.assertFalse((self.root / "CLAUDE.md").exists())

    def test_parser_yaml_sem_pyyaml(self):
        data = _parse_yaml_subset("name: x\ndescription: 'a: b'\ntools: Read, Grep\nskills:\n  - um\n  - dois\nflag: true\ndesc: >\n  linha um\n  linha dois")
        self.assertEqual(data["skills"], ["um", "dois"])
        self.assertEqual(data["description"], "a: b")
        self.assertIs(data["flag"], True)
        self.assertEqual(data["desc"], "linha um linha dois")
        for bad in ("name: x\nname: y", "  name: x", "description: \"aberta", "desc: a: b"):
            with self.assertRaises(FrontmatterError):
                _parse_yaml_subset(bad)

    # 9
    def test_referencia_inexistente(self):
        files = self.full_files()
        files[".claude/agents/revisor.md"] = AGENT.replace("- exemplo", "- nao-existe")
        files["CLAUDE.md"] = "# P\n\nVeja [x](docs/inexistente.md) e @docs/tambem-nao.md\n"
        self.write_proposal("p1", files)
        code, out = self.run_cli("validate", "--proposal", "p1")
        self.assertEqual(code, 1)
        self.assertIn("skill inexistente 'nao-existe'", out)
        self.assertIn("docs/inexistente.md", out)
        self.assertIn("docs/tambem-nao.md", out)

    def test_campos_por_tipo_e_paths(self):
        files = self.full_files()
        files[".claude/agents/revisor.md"] = AGENT.replace("tools: Read, Grep", "tools: Read, Grep\nallowed-tools: Bash\nmodel: gpt-4")
        files[".claude/rules/php.md"] = RULE.replace("src/**/*.php", "nao/existe/*.php")
        self.write_proposal("p1", files)
        code, out = self.run_cli("validate", "--proposal", "p1")
        self.assertIn("Campo 'allowed-tools' não é aceito em agents", out)
        self.assertIn("Modelo inválido", out)
        self.assertIn("não casa com nenhum arquivo", out)

    # 10 (raiz com espaço e acento já é usada em todos os testes)
    def test_caminhos_com_espacos_e_nao_ascii(self):
        code, out = self.run_cli("scan", "--json")
        data = json.loads(out)
        self.assertIn("packages/heuderdev/helpers/src/Relatório Mensal.php", data["fontes"])

    # 11
    def test_bloqueio_fora_da_raiz(self):
        for bad in ("../fora.md", "/etc/passwd", "C:/Windows/x.md", "a/../../b.md", "//servidor/x"):
            with self.assertRaises(ToolError):
                safe_path(self.root, bad)
        files = self.full_files()
        files["tools/malicioso.py"] = "print(1)\n"
        self.write_proposal("p1", files)
        code, out = self.run_cli("validate", "--proposal", "p1")
        self.assertEqual(code, 1)
        self.assertIn("fora da área de configuração", out)
        files = self.full_files()
        files["CLAUDE.md"] = "# P\n\n[fora](../../segredo.md)\n"
        self.write_proposal("p2", files)
        code, out = self.run_cli("validate", "--proposal", "p2")
        self.assertIn("sai da raiz", out)
        with self.assertRaises(ToolError):
            from tools.claude_project.apply import proposal_dir
            proposal_dir(self.root, "../fora")
        outside = self.base / "fora.txt"
        outside.write_text("x", encoding="utf-8")
        try:
            os.symlink(str(outside), str(self.root / "link.txt"))
        except (OSError, NotImplementedError):
            self.skipTest("Sem permissão para criar link simbólico neste sistema.")
        data = json.loads(self.run_cli("scan", "--json")[1])
        self.assertNotIn("link.txt", data["fontes"])
        self.assertIn("link.txt", data["ignorados"]["link_simbolico"])

    # 12
    def test_exclusao_de_segredos(self):
        src = self.root / "packages/heuderdev/helpers/src"
        (src / "SecretHelper.php").write_text("<?php\n$c = ['password' => 'S3nhaForte!9'];\n", encoding="utf-8")
        (src / "PlaceholderHelper.php").write_text("<?php\n$c = ['password' => 'sua_senha'];\n", encoding="utf-8")
        data = json.loads(self.run_cli("scan", "--json")[1])
        self.assertNotIn("config.php", data["fontes"])
        self.assertNotIn(".env", data["fontes"])
        self.assertIn("credencial_literal", data["fontes"]["packages/heuderdev/helpers/src/SecretHelper.php"]["sensivel"])
        self.assertNotIn("sensivel", data["fontes"]["packages/heuderdev/helpers/src/PlaceholderHelper.php"])
        self.run_cli("prepare", "--incluir-fontes")
        context = next((self.root / ".claude-project" / "context").glob("*/context.md")).read_text(encoding="utf-8")
        self.assertNotIn("S3nhaForte", context)
        self.assertNotIn("S3nhaReal", context)
        files = self.full_files()
        files["CLAUDE.md"] = "# P\n\napi_key = 'abcd1234efgh5678'\n"
        self.write_proposal("p1", files)
        code, out = self.run_cli("validate", "--proposal", "p1")
        self.assertEqual(code, 1)
        self.assertIn("Possível segredo", out)

    # 13
    def test_falha_na_aplicacao_e_recuperacao(self):
        self.first_apply()
        original = {p: (self.root / p).read_bytes() for p in self.full_files()}
        manifest_before = (self.root / ".claude-project/manifest.json").read_bytes()
        files = {p: t + "\nv2\n" for p, t in self.full_files().items()}
        files[".claude/rules/nova.md"] = RULE
        self.write_proposal("p2", files)
        self.run_cli("validate", "--proposal", "p2")
        os.environ["CLAUDE_PROJECT_FAIL_AFTER"] = "2"
        with self.assertRaises(OSError):
            self.run_cli("apply", "--proposal", "p2", "--yes")
        os.environ.pop("CLAUDE_PROJECT_FAIL_AFTER")
        for path, data in original.items():
            self.assertEqual((self.root / path).read_bytes(), data, path)
        self.assertFalse((self.root / ".claude/rules/nova.md").exists())
        self.assertEqual((self.root / ".claude-project/manifest.json").read_bytes(), manifest_before)
        self.assertEqual(list(self.root.rglob("*.cp-tmp")), [])

        backup = self.root / ".claude-project/backups/crash"
        (backup / "files" / ".claude/rules").mkdir(parents=True)
        (backup / "files/.claude/rules/php.md").write_bytes(original[".claude/rules/php.md"])
        (self.root / ".claude/rules/php.md").write_text("meio aplicado\n", encoding="utf-8")
        (self.root / ".claude/rules/criado.md").write_text("x\n", encoding="utf-8")
        (backup / "journal.json").write_text(canonical_json({"proposta": "p2", "status": "em_andamento", "entradas": [
            {"caminho": ".claude/rules/php.md", "existia": True, "feito": True},
            {"caminho": ".claude/rules/criado.md", "existia": False, "feito": True},
        ]}), encoding="utf-8")
        code, out = self.run_cli("status")
        self.assertIn("apply --recover", out)
        code, out = self.run_cli("apply", "--proposal", "p2", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("interrompida", out)
        self.assertEqual(self.run_cli("apply", "--recover")[0], 0)
        self.assertEqual((self.root / ".claude/rules/php.md").read_bytes(), original[".claude/rules/php.md"])
        self.assertFalse((self.root / ".claude/rules/criado.md").exists())

    def test_proposta_parcial(self):
        self.first_apply()
        current = (self.root / "CLAUDE.md").read_bytes()
        self.write_proposal("p2", {"CLAUDE.md": CLAUDE_MD + "\nMais.\n"}, bases={"CLAUDE.md": sha256_bytes(current)})
        code, out = self.run_cli("validate", "--proposal", "p2")
        self.assertEqual(code, 0, out)
        code, out = self.run_cli("apply", "--proposal", "p2", "--yes")
        self.assertEqual(code, 0, out)
        self.assertEqual(len(self.manifest()["artefatos"]), 4)

    def test_apply_exige_validacao_atual(self):
        self.write_proposal("p1", self.full_files())
        code, out = self.run_cli("apply", "--proposal", "p1", "--yes")
        self.assertIn("não validada", out)
        self.run_cli("validate", "--proposal", "p1")
        (self.root / ".claude-project/proposals/p1/files/CLAUDE.md").write_text("# mudou\n", encoding="utf-8")
        code, out = self.run_cli("apply", "--proposal", "p1", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("alterada após a validação", out)
        code, out = self.run_cli("apply", "--proposal", "p1")
        self.assertFalse((self.root / "CLAUDE.md").exists())


if __name__ == "__main__":
    unittest.main()
