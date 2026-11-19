import difflib
import json
import os
import shutil
import time
from pathlib import Path

from . import GENERATOR_VERSION, SCHEMA_VERSION
from .common import (
    MANIFEST_FILE, ToolError, atomic_write, canonical_json, read_json, safe_path,
    sha256_bytes, state_dir, write_if_changed,
)

FAIL_ENV = "CLAUDE_PROJECT_FAIL_AFTER"


def empty_manifest():
    return {"schema_version": SCHEMA_VERSION, "generator_version": GENERATOR_VERSION,
            "baseline": None, "artefatos": {}, "pendencias": []}


def load_manifest(root):
    manifest = read_json(state_dir(root) / MANIFEST_FILE)
    if manifest is None:
        return empty_manifest()
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ToolError("manifest.json com schema %s; esperado %s." % (manifest.get("schema_version"), SCHEMA_VERSION))
    return manifest


def save_manifest(root, manifest):
    return write_if_changed(state_dir(root) / MANIFEST_FILE, canonical_json(manifest))


def proposal_dir(root, ident):
    if not ident or "/" in ident or "\\" in ident or ident.startswith("."):
        raise ToolError("Identificador de proposta inválido: %r" % ident)
    directory = state_dir(root) / "proposals" / ident
    if not (directory / "proposal.json").is_file():
        raise ToolError("Proposta não encontrada: %s" % (directory / "proposal.json"))
    return directory


def load_proposal(root, ident):
    directory = proposal_dir(root, ident)
    proposal = read_json(directory / "proposal.json")
    if not isinstance(proposal, dict) or not isinstance(proposal.get("arquivos"), list):
        raise ToolError("proposal.json precisa ter a lista 'arquivos'.")
    files = {}
    base = directory / "files"
    if base.is_dir():
        for path in sorted(base.rglob("*")):
            if path.is_symlink():
                raise ToolError("Link simbólico não permitido na proposta: %s" % path)
            if path.is_file():
                rel = path.relative_to(base).as_posix()
                files[rel] = path.read_bytes().decode("utf-8")
    return proposal, files


def proposal_hash(proposal, files):
    payload = {"proposal": proposal, "files": {path: sha256_bytes(text.encode("utf-8")) for path, text in files.items()}}
    return sha256_bytes(canonical_json(payload).encode("utf-8"))


def save_validation(root, ident, digest, report):
    record = {"hash": digest, "erros": len(report.errors), "itens": report.to_dict()}
    write_if_changed(proposal_dir(root, ident) / "validation.json", canonical_json(record))


def plan(root, proposal, files, manifest):
    entries = {entry["caminho"]: entry for entry in proposal["arquivos"]}
    result = []
    for path in sorted(files):
        target = safe_path(root, path)
        new = files[path].encode("utf-8")
        current = target.read_bytes() if target.is_file() else None
        current_hash = sha256_bytes(current) if current is not None else None
        managed = manifest.get("artefatos", {}).get(path)
        if current is None:
            action = "criar"
        elif current == new:
            action = "inalterado"
        elif entries.get(path, {}).get("base_sha256") == current_hash:
            action = "atualizar"
        elif managed and managed.get("sha256") == current_hash:
            action = "atualizar"
        else:
            action = "conflito"
        result.append({"caminho": path, "acao": action, "atual": current, "novo": new})
    return result


def render_plan(items):
    lines = []
    for item in items:
        lines.append("[%s] %s" % (item["acao"], item["caminho"]))
        if item["acao"] in ("atualizar", "conflito") and item["atual"] is not None:
            before = item["atual"].decode("utf-8", errors="replace").splitlines(True)
            after = item["novo"].decode("utf-8").splitlines(True)
            lines.append("".join(difflib.unified_diff(before, after, "atual/" + item["caminho"], "proposto/" + item["caminho"])).rstrip("\n"))
    return lines


def _write_conflicts(root, ident, items):
    out = []
    for item in items:
        if item["acao"] != "conflito":
            continue
        target = state_dir(root) / "conflicts" / ident / Path(*item["caminho"].split("/"))
        atomic_write(target, item["novo"])
        out.append(str(target))
    return out


def _journal_path(backup):
    return backup / "journal.json"


def _rollback(root, backup, journal):
    problems = []
    for entry in reversed(journal["entradas"]):
        if not entry.get("feito"):
            continue
        target = safe_path(root, entry["caminho"])
        try:
            if entry["existia"]:
                atomic_write(target, (backup / "files" / Path(*entry["caminho"].split("/"))).read_bytes())
            elif target.exists():
                target.unlink()
        except OSError as error:
            problems.append("%s: %s" % (entry["caminho"], error))
    temp_names = [safe_path(root, entry["caminho"]) for entry in journal["entradas"]]
    for target in temp_names:
        temp = target.with_name("." + target.name + ".cp-tmp")
        if temp.exists():
            temp.unlink()
    journal["status"] = "revertido" if not problems else "reversao_incompleta"
    journal["problemas"] = problems
    atomic_write(_journal_path(backup), canonical_json(journal).encode("utf-8"))
    return problems


def apply(root, ident, manifest, inventory, items, proposal, skip_conflicts=False):
    root = Path(root)
    to_write = [item for item in items if item["acao"] in ("criar", "atualizar")]
    conflicts = [item for item in items if item["acao"] == "conflito"]
    conflict_paths = _write_conflicts(root, ident, items) if conflicts else []
    if conflicts and not skip_conflicts:
        paths = conflict_paths
        raise ToolError("Conflitos com edições manuais; nada foi aplicado. Versões propostas em:\n  " + "\n  ".join(paths))

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = state_dir(root) / "backups" / ("%s-%s" % (stamp, ident))
    suffix = 1
    while backup.exists():
        suffix += 1
        backup = state_dir(root) / "backups" / ("%s-%s-%d" % (stamp, ident, suffix))
    (backup / "files").mkdir(parents=True)
    manifest_file = state_dir(root) / MANIFEST_FILE
    if manifest_file.is_file():
        shutil.copy2(str(manifest_file), str(backup / MANIFEST_FILE))
    journal = {"proposta": ident, "status": "em_andamento", "entradas": []}
    for item in to_write:
        existed = item["atual"] is not None
        if existed:
            atomic_write(backup / "files" / Path(*item["caminho"].split("/")), item["atual"])
        journal["entradas"].append({"caminho": item["caminho"], "existia": existed, "feito": False})
    atomic_write(_journal_path(backup), canonical_json(journal).encode("utf-8"))

    fail_after = int(os.environ.get(FAIL_ENV, "-1"))
    try:
        for index, item in enumerate(to_write):
            if index == fail_after:
                raise OSError("falha simulada (%s)" % FAIL_ENV)
            atomic_write(safe_path(root, item["caminho"]), item["novo"])
            journal["entradas"][index]["feito"] = True
            atomic_write(_journal_path(backup), canonical_json(journal).encode("utf-8"))

        entries = {entry["caminho"]: entry for entry in proposal["arquivos"]}
        for item in items:
            if item["acao"] == "conflito":
                continue
            snapshot = state_dir(root) / "applied" / Path(*item["caminho"].split("/"))
            write_if_changed(snapshot, item["novo"].decode("utf-8"))
            entry = entries.get(item["caminho"], {})
            manifest["artefatos"][item["caminho"]] = {
                "sha256": sha256_bytes(item["novo"]),
                "fontes": sorted(entry.get("fontes", [])),
                "evidencias": sorted(entry.get("evidencias", [])),
                "proposta": ident,
            }
        manifest["generator_version"] = GENERATOR_VERSION
        manifest["pendencias"] = [
            {"tipo": "conflito", "caminho": item["caminho"], "proposta": ident} for item in conflicts
        ]
        if not conflicts:
            manifest["baseline"] = {
                "fontes": {path: entry["sha256"] for path, entry in inventory["fontes"].items()},
                "humanos": inventory["humanos"],
                "config_hash": inventory["config_hash"],
                "proposta": ident,
            }
        save_manifest(root, manifest)
    except Exception:
        problems = _rollback(root, backup, journal)
        manifest_backup = backup / MANIFEST_FILE
        if manifest_backup.is_file():
            atomic_write(manifest_file, manifest_backup.read_bytes())
        if problems:
            raise ToolError("Falha na aplicação e reversão incompleta: %s. Backup em %s" % ("; ".join(problems), backup))
        raise
    journal["status"] = "concluido"
    atomic_write(_journal_path(backup), canonical_json(journal).encode("utf-8"))
    obsolete = []
    if proposal.get("completa"):
        obsolete = sorted(set(manifest["artefatos"]) - set(item["caminho"] for item in items))
    return {"backup": str(backup), "escritos": [item["caminho"] for item in to_write],
            "conflitos": [item["caminho"] for item in conflicts], "obsoletos": obsolete}


def incomplete_journals(root):
    base = state_dir(root) / "backups"
    found = []
    if base.is_dir():
        for journal in sorted(base.glob("*/journal.json")):
            data = read_json(journal, {})
            if data.get("status") in ("em_andamento", "reversao_incompleta"):
                found.append((journal.parent, data))
    return found


def recover(root):
    results = []
    for backup, journal in incomplete_journals(root):
        problems = _rollback(root, backup, journal)
        manifest_backup = backup / MANIFEST_FILE
        if manifest_backup.is_file():
            atomic_write(state_dir(root) / MANIFEST_FILE, manifest_backup.read_bytes())
        results.append({"backup": str(backup), "problemas": problems})
    return results


def proposals_overview(root):
    base = state_dir(root) / "proposals"
    rows = []
    if not base.is_dir():
        return rows
    for directory in sorted(p for p in base.iterdir() if p.is_dir()):
        validation = read_json(directory / "validation.json", None)
        state = "sem proposal.json"
        if (directory / "proposal.json").is_file():
            try:
                proposal, files = load_proposal(root, directory.name)
                digest = proposal_hash(proposal, files)
                if validation is None:
                    state = "não validada"
                elif validation.get("hash") != digest:
                    state = "alterada após validação"
                else:
                    state = "validada (%d erro(s))" % validation.get("erros", 0)
            except (ToolError, UnicodeDecodeError, json.JSONDecodeError) as error:
                state = "inválida: %s" % error
        rows.append((directory.name, state))
    return rows
