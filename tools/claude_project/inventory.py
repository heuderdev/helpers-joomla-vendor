import json
import os
import re
import subprocess
from pathlib import Path, PurePosixPath

from . import GENERATOR_VERSION, SCHEMA_VERSION
from .common import (
    INVENTORY_FILE, canonical_json, config_hash, glob_match, is_binary,
    is_managed_target, scan_secrets, sha256_bytes, state_dir, write_if_changed,
)

PHP_FEATURES = [
    ("7.1", "retorno void", re.compile(r"\)\s*:\s*void\b")),
    ("7.1", "tipo anulável", re.compile(r"(?:\(|,\s*|\)\s*:\s*)\?[A-Za-z_\\]+\s*(?:\$|\{|$)")),
    ("7.4", "arrow function", re.compile(r"(?<![\w$>])fn\s*\(")),
    ("7.4", "operador ??=", re.compile(r"\?\?=")),
    ("7.4", "propriedade tipada", re.compile(r"\b(?:public|protected|private)\s+(?:static\s+)?\??(?:int|string|bool|float|array|iterable|object|self)\s+\$")),
    ("8.0", "match", re.compile(r"(?<![\w$>:])match\s*\(.*\)\s*\{")),
    ("8.0", "nullsafe ?->", re.compile(r"\?->")),
    ("8.0", "tipo mixed/union", re.compile(r"[(,]\s*(?:mixed|[A-Za-z_]+\|[A-Za-z_|]+)\s+\$")),
    ("8.0", "str_contains/str_starts_with/str_ends_with", re.compile(r"\bstr_(?:contains|starts_with|ends_with)\s*\(")),
    ("8.0", "atributo #[", re.compile(r"^\s*#\[")),
    ("8.1", "enum/readonly", re.compile(r"^\s*(?:enum\s+\w+|.*\breadonly\s+(?:public|protected|private|class|\??\w+\s+\$))")),
]

CLASS_RE = re.compile(r"^\s*(?:final\s+|abstract\s+)?(?:class|interface|trait)\s+([A-Za-z_]\w*)", re.M)
DIE_JOOMLA_RE = re.compile(r"defined\(\s*'_JEXEC'\s*\)\s*or\s+die")
GUARD_RE = re.compile(r"realpath\(\s*\$_SERVER\['SCRIPT_FILENAME'\]\s*\)\s*===\s*__FILE__")
REGISTRY_ENTRY_RE = re.compile(r"'(\w+)'\s*=>\s*array\(")


def _version_key(version):
    return tuple(int(part) for part in version.split("."))


def _excluded_dir(rel, name, config):
    for item in config["exclude_dirs"]:
        if "/" in item:
            if rel == item or rel.startswith(item + "/"):
                return True
        elif name == item:
            return True
    return False


def _git_visible(root):
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "-c", "-o", "--exclude-standard"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return {item.decode("utf-8", "surrogateescape") for item in result.stdout.split(b"\x00") if item}


def _category(rel, config):
    name = PurePosixPath(rel).name.lower()
    if name in ("composer.json", "composer.lock", "package.json", "package-lock.json", "requirements.txt", "pyproject.toml"):
        return "dependencias"
    if rel.endswith(".php"):
        for directory in config["registry_dirs"]:
            if rel.startswith(directory.rstrip("/") + "/"):
                return "helper"
        return "php"
    if name.endswith(".md"):
        return "doc"
    if name.endswith((".json", ".xml", ".yml", ".yaml", ".ini", ".neon", ".dist")) or name.startswith("."):
        return "config"
    return "outro"


def _code_lines(text):
    in_block = False
    for line in text.splitlines():
        stripped = line.strip()
        if in_block:
            if "*/" in stripped:
                in_block = False
            continue
        if stripped.startswith("/*"):
            in_block = "*/" not in stripped
            continue
        if stripped.startswith(("//", "*", "#")) and not stripped.startswith("#["):
            continue
        yield line


def php_evidence(text):
    features = {}
    minimum = "7.0"
    for line in _code_lines(text):
        for version, label, pattern in PHP_FEATURES:
            if pattern.search(line):
                features[label] = version
                if _version_key(version) > _version_key(minimum):
                    minimum = version
    if DIE_JOOMLA_RE.search(text):
        header = "die_fora_do_joomla"
    elif GUARD_RE.search(text):
        header = "bloqueia_acesso_direto"
    else:
        header = "sem_guarda"
    evidence = {
        "classes": sorted(set(CLASS_RE.findall(text))),
        "cabecalho": header,
        "php_minimo_sintaxe": minimum,
        "strict_types": bool(re.search(r"declare\s*\(\s*strict_types\s*=\s*1", text)),
    }
    if features:
        evidence["recursos"] = dict(sorted(features.items()))
    return evidence


def composer_evidence(text):
    try:
        data = json.loads(text)
    except ValueError:
        return {"erro": "JSON inválido"}
    evidence = {}
    for key in ("name", "type"):
        if key in data:
            evidence[key] = data[key]
    for key in ("require", "require-dev"):
        if isinstance(data.get(key), dict):
            evidence[key] = dict(sorted(data[key].items()))
    if isinstance(data.get("autoload"), dict):
        evidence["autoload"] = sorted(data["autoload"].keys())
    return evidence


def registry_evidence(root, config, sources):
    registry = config.get("registry_file")
    if not registry or registry not in sources:
        return None
    text = (Path(root) / registry).read_text(encoding="utf-8", errors="replace")
    block = text.split("$dependencies", 1)
    registered = sorted(set(REGISTRY_ENTRY_RE.findall(block[1].split(");", 1)[0]))) if len(block) == 2 else []
    stems = set()
    for directory in config["registry_dirs"]:
        prefix = directory.rstrip("/") + "/"
        for rel in sources:
            if rel.startswith(prefix) and rel.endswith(".php") and "/" not in rel[len(prefix):]:
                stems.add(PurePosixPath(rel).stem)
    infra = {"IncludeHelper"}
    return {
        "arquivo": registry,
        "registrados": registered,
        "nao_registrados": sorted(stems - set(registered) - infra),
        "registrados_sem_arquivo": sorted(set(registered) - stems),
    }


def scan(root, config):
    root = Path(root).resolve()
    limits = config["limits"]
    visible = _git_visible(root) if config.get("use_gitignore", True) else None
    sources = {}
    humans = {}
    artifacts = {}
    skipped = {}
    notes = []
    if config.get("use_gitignore", True) and visible is None:
        notes.append("gitignore não aplicado (git indisponível ou não é repositório).")

    def skip(reason, rel):
        skipped.setdefault(reason, []).append(rel)

    for current, dirs, files in os.walk(str(root), followlinks=False):
        current_path = Path(current)
        rel_dir = "" if current_path == root else current_path.relative_to(root).as_posix()
        kept = []
        for name in sorted(dirs):
            rel = (rel_dir + "/" + name) if rel_dir else name
            full = current_path / name
            if full.is_symlink():
                skip("link_simbolico", rel + "/")
                continue
            if rel == ".claude" or rel.startswith(".claude/"):
                kept.append(name)
                continue
            if _excluded_dir(rel, name, config):
                skip("diretorio_excluido", rel + "/")
                continue
            kept.append(name)
        dirs[:] = kept
        for name in sorted(files):
            rel = (rel_dir + "/" + name) if rel_dir else name
            full = current_path / name
            if full.is_symlink():
                skip("link_simbolico", rel)
                continue
            if is_managed_target(rel):
                artifacts[rel] = sha256_bytes(full.read_bytes())
                continue
            if rel.startswith(".claude/"):
                skip("claude_nao_gerenciado", rel)
                continue
            if rel in config["human_files"]:
                humans[rel] = sha256_bytes(full.read_bytes())
                continue
            if any(glob_match(pattern, rel) for pattern in config["exclude_globs"]):
                skip("padrao_excluido", rel)
                continue
            if visible is not None and rel not in visible:
                skip("gitignore", rel)
                continue
            size = full.stat().st_size
            if size > limits["max_file_bytes"]:
                skip("grande_demais", rel)
                continue
            data = full.read_bytes()
            if is_binary(data):
                skip("binario", rel)
                continue
            text = data.decode("utf-8", errors="replace")
            entry = {"sha256": sha256_bytes(data), "bytes": size, "categoria": _category(rel, config)}
            secrets = scan_secrets(text)
            if secrets:
                entry["sensivel"] = sorted({hit["tipo"] for hit in secrets})
            if rel.endswith(".php"):
                entry["evidencia"] = php_evidence(text)
            elif PurePosixPath(rel).name == "composer.json":
                entry["evidencia"] = composer_evidence(text)
            sources[rel] = entry

    inventory = {
        "schema_version": SCHEMA_VERSION,
        "generator_version": GENERATOR_VERSION,
        "config_hash": config_hash(config),
        "fontes": dict(sorted(sources.items())),
        "humanos": dict(sorted(humans.items())),
        "artefatos": dict(sorted(artifacts.items())),
        "ignorados": {reason: sorted(paths) for reason, paths in sorted(skipped.items())},
        "notas": notes,
    }
    registry = registry_evidence(root, config, sources)
    if registry:
        inventory["registro"] = registry
    return inventory


def save_inventory(root, inventory):
    return write_if_changed(state_dir(root) / INVENTORY_FILE, canonical_json(inventory))


def summarize(inventory):
    lines = []
    sources = inventory["fontes"]
    by_category = {}
    for entry in sources.values():
        by_category[entry["categoria"]] = by_category.get(entry["categoria"], 0) + 1
    lines.append("Fontes analisadas: %d (%s)" % (len(sources), ", ".join("%s=%d" % item for item in sorted(by_category.items()))))
    sensitive = [path for path, entry in sources.items() if entry.get("sensivel")]
    if sensitive:
        lines.append("Possíveis segredos (fora do pacote de contexto): " + ", ".join(sensitive))
    lines.append("Artefatos de Claude presentes: %d" % len(inventory["artefatos"]))
    lines.append("Instruções humanas monitoradas: %s" % (", ".join(inventory["humanos"]) or "nenhuma"))
    for reason, paths in inventory["ignorados"].items():
        sample = ", ".join(paths[:5]) + (" ..." if len(paths) > 5 else "")
        lines.append("Ignorados [%s]: %d (%s)" % (reason, len(paths), sample))
    minimums = {}
    headers = {}
    for path, entry in sources.items():
        evidence = entry.get("evidencia") or {}
        if "php_minimo_sintaxe" in evidence:
            minimums.setdefault(evidence["php_minimo_sintaxe"], []).append(PurePosixPath(path).name)
        if "cabecalho" in evidence and entry["categoria"] == "helper":
            headers.setdefault(evidence["cabecalho"], []).append(PurePosixPath(path).stem)
    for version in sorted(minimums, key=_version_key):
        names = minimums[version]
        lines.append("PHP mínimo pela sintaxe %s: %d arquivo(s)%s" % (
            version, len(names), "" if version == "7.0" else " -> " + ", ".join(sorted(names))))
    for header, names in sorted(headers.items()):
        lines.append("Cabeçalho %s: %d helper(s)" % (header, len(names)))
    registry = inventory.get("registro")
    if registry:
        if registry["nao_registrados"]:
            lines.append("Helpers fora do IncludeHelper: " + ", ".join(registry["nao_registrados"]))
        if registry["registrados_sem_arquivo"]:
            lines.append("Registrados sem arquivo: " + ", ".join(registry["registrados_sem_arquivo"]))
    for note in inventory.get("notas", []):
        lines.append("Nota: " + note)
    return lines
