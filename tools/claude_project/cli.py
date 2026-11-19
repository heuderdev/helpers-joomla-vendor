import argparse
import json
import sys
from pathlib import Path

from . import GENERATOR_VERSION
from . import apply as applier
from . import changes, context, inventory, validate
from .common import ToolError, find_root, load_config, safe_path, state_dir


def _out(lines):
    for line in lines:
        print(line)


def _setup(args):
    root = Path(args.root).resolve() if args.root else find_root()
    if not root.is_dir():
        raise ToolError("Raiz inexistente: %s" % root)
    config = load_config(root)
    return root, config


def _scan(root, config):
    data = inventory.scan(root, config)
    inventory.save_inventory(root, data)
    return data


def cmd_scan(args):
    root, config = _setup(args)
    data = _scan(root, config)
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print("Raiz: %s" % root)
        _out(inventory.summarize(data))
        print("Inventário: %s" % (state_dir(root) / "inventory.json"))
    return 0


def cmd_diff(args):
    root, config = _setup(args)
    data = _scan(root, config)
    manifest = applier.load_manifest(root)
    result = changes.compute(manifest, data, config)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _out(changes.summarize(result))
    return 0


def cmd_prepare(args):
    root, config = _setup(args)
    data = _scan(root, config)
    manifest = applier.load_manifest(root)
    result = changes.compute(manifest, data, config)
    if not changes.has_changes(result) and not args.force:
        print("Sem mudanças desde a última aplicação; nenhum pacote gerado (use --force para gerar mesmo assim).")
        return 0
    info = context.prepare(root, config, manifest, data, result, include_sources=args.incluir_fontes)
    print("Pacote %s: %s (%d bytes)%s" % (info["id"], info["arquivo"], info["bytes"], "" if info["alterado"] else " [inalterado]"))
    print("Proposta esperada em: %s" % (state_dir(root) / "proposals" / info["id"]))
    for key, label in (("sensiveis", "Fora do pacote por possível segredo"), ("truncados", "Truncados"), ("omitidos", "Omitidos")):
        if info[key]:
            print("%s: %s" % (label, ", ".join(info[key])))
    return 0


def _print_report(report):
    for item in report.to_dict():
        print("[%s] %s: %s" % (item["nivel"].upper(), item["arquivo"], item["mensagem"]))
    counts = {}
    for item in report.items:
        counts[item["nivel"]] = counts.get(item["nivel"], 0) + 1
    print("Resumo: " + ", ".join("%s=%d" % pair for pair in sorted(counts.items())))


def cmd_validate(args):
    root, config = _setup(args)
    data = _scan(root, config)
    manifest = applier.load_manifest(root)
    source_paths = sorted(set(data["fontes"]) | set(data["artefatos"]) | set(data["humanos"]))
    if args.proposal:
        proposal, files = applier.load_proposal(root, args.proposal)
        merged = {}
        for path in data["artefatos"]:
            merged[path] = (root / path).read_text(encoding="utf-8", errors="replace")
        merged.update(files)
        report = validate.run(root, merged, source_paths, config["limits"], proposal, manifest)
        report.items = [item for item in report.items if item["arquivo"] in files or item["arquivo"] in ("-", "proposal.json")
                        or item["nivel"] == validate.ERRO or item["arquivo"] in [e.get("caminho") for e in proposal["arquivos"]]]
        digest = applier.proposal_hash(proposal, files)
        applier.save_validation(root, args.proposal, digest, report)
    else:
        files = {path: (root / path).read_text(encoding="utf-8", errors="replace") for path in data["artefatos"]}
        report = validate.run(root, files, source_paths, config["limits"])
        for item in changes.compute(manifest, data, config)["artefatos"]:
            level = validate.INFO if item["estado"] == "nao_gerenciado" else validate.AVISO
            report.add(level, item["caminho"], "Artefato %s." % item["estado"].replace("_", " "))
    _print_report(report)
    return 1 if report.errors else 0


def cmd_apply(args):
    root, config = _setup(args)
    if args.recover:
        results = applier.recover(root)
        if not results:
            print("Nenhuma aplicação interrompida encontrada.")
        for result in results:
            print("Revertido a partir de %s%s" % (result["backup"], (": " + "; ".join(result["problemas"])) if result["problemas"] else ""))
        return 0
    if not args.proposal:
        raise ToolError("Informe --proposal ID (ou --recover).")
    if applier.incomplete_journals(root):
        raise ToolError("Há uma aplicação interrompida; rode 'apply --recover' antes.")
    proposal, files = applier.load_proposal(root, args.proposal)
    digest = applier.proposal_hash(proposal, files)
    record = applier.read_json(applier.proposal_dir(root, args.proposal) / "validation.json", None)
    if record is None or record.get("hash") != digest:
        raise ToolError("Proposta não validada ou alterada após a validação: rode 'validate --proposal %s'." % args.proposal)
    if record.get("erros"):
        raise ToolError("A validação da proposta tem %d erro(s) bloqueante(s)." % record["erros"])
    data = _scan(root, config)
    manifest = applier.load_manifest(root)
    items = applier.plan(root, proposal, files, manifest)
    _out(applier.render_plan(items))
    if not args.yes:
        print("\nSimulação: nada foi gravado. Revise o diff e rode novamente com --yes para aplicar.")
        return 0
    registered = all(item["caminho"] in manifest.get("artefatos", {}) for item in items)
    current = {path: entry["sha256"] for path, entry in data["fontes"].items()}
    up_to_date = bool(manifest.get("baseline")) and manifest["baseline"]["fontes"] == current \
        and manifest["baseline"].get("humanos") == data["humanos"] and manifest["baseline"].get("config_hash") == data["config_hash"]
    if all(item["acao"] == "inalterado" for item in items) and registered and up_to_date:
        print("Nada a aplicar: arquivos idênticos aos já aplicados.")
        return 0
    result = applier.apply(root, args.proposal, manifest, data, items, proposal, skip_conflicts=args.skip_conflicts)
    _scan(root, config)
    print("Aplicado. Escritos: %s" % (", ".join(result["escritos"]) or "nenhum"))
    print("Backup: %s" % result["backup"])
    if result["conflitos"]:
        print("Conflitos não aplicados (veja .claude-project/conflicts/%s): %s" % (args.proposal, ", ".join(result["conflitos"])))
        print("A base de fontes NÃO foi avançada enquanto houver conflitos.")
        return 3
    if result["obsoletos"]:
        print("Possivelmente obsoletos (não removidos; revise): %s" % ", ".join(result["obsoletos"]))
    return 0


def cmd_status(args):
    root, config = _setup(args)
    data = _scan(root, config)
    manifest = applier.load_manifest(root)
    result = changes.compute(manifest, data, config)
    print("Raiz: %s | gerador %s" % (root, GENERATOR_VERSION))
    baseline = manifest.get("baseline")
    print("Última aplicação: %s" % (baseline["proposta"] if baseline else "nenhuma"))
    print("Artefatos gerenciados: %d" % len(manifest.get("artefatos", {})))
    states = {item["caminho"]: item["estado"] for item in result["artefatos"]}
    for path in sorted(set(manifest.get("artefatos", {})) | set(data["artefatos"])):
        print("  %-55s %s" % (path, states.get(path, "ok")))
    for item in manifest.get("pendencias", []):
        print("Pendência: %s em %s (proposta %s)" % (item["tipo"], item["caminho"], item["proposta"]))
    for name, state in applier.proposals_overview(root):
        print("Proposta %s: %s" % (name, state))
    for backup, _ in applier.incomplete_journals(root):
        print("ATENÇÃO: aplicação interrompida em %s -> rode 'apply --recover'." % backup)
    print("Mudanças: " + ("sim" if changes.has_changes(result) else "nenhuma") + " | impacto: " + result["impacto"])
    return 0


def build_parser():
    parser = argparse.ArgumentParser(prog="python -m tools.claude_project",
                                     description="Mantém a configuração de Claude Code deste repositório (modo sem API).")
    parser.add_argument("--root", help="Raiz do projeto (padrão: sobe até achar .claude-project/config.json ou .git).")
    sub = parser.add_subparsers(dest="command")
    sub.required = True
    scan = sub.add_parser("scan", help="Gera inventário e evidências.")
    scan.add_argument("--json", action="store_true")
    scan.set_defaults(func=cmd_scan)
    diff = sub.add_parser("diff", help="Compara com o último estado aplicado.")
    diff.add_argument("--json", action="store_true")
    diff.set_defaults(func=cmd_diff)
    prepare = sub.add_parser("prepare", help="Produz o pacote de contexto para o Claude.")
    prepare.add_argument("--incluir-fontes", action="store_true", help="Na primeira geração, inclui todas as fontes (respeita limites).")
    prepare.add_argument("--force", action="store_true", help="Gera o pacote mesmo sem mudanças.")
    prepare.set_defaults(func=cmd_prepare)
    check = sub.add_parser("validate", help="Valida a proposta ou os artefatos instalados.")
    check.add_argument("--proposal")
    check.set_defaults(func=cmd_validate)
    run = sub.add_parser("apply", help="Aplica uma proposta validada (simulação sem --yes).")
    run.add_argument("--proposal")
    run.add_argument("--yes", action="store_true", help="Autoriza a gravação.")
    run.add_argument("--skip-conflicts", action="store_true", help="Aplica o restante e deixa os conflitos pendentes.")
    run.add_argument("--recover", action="store_true", help="Reverte uma aplicação interrompida.")
    run.set_defaults(func=cmd_apply)
    status = sub.add_parser("status", help="Situação da configuração e pendências.")
    status.set_defaults(func=cmd_status)
    return parser


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (ValueError, OSError):
            pass
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ToolError as error:
        print("Erro: %s" % error, file=sys.stderr)
        return 1
