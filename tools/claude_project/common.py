import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath

STATE_DIR = ".claude-project"
CONFIG_FILE = "config.json"
MANIFEST_FILE = "manifest.json"
INVENTORY_FILE = "inventory.json"

DEFAULT_CONFIG = {
    "schema_version": 1,
    "use_gitignore": True,
    "exclude_dirs": [
        ".git", ".claude", ".claude-project", "vendor", "node_modules",
        "tools/claude_project", "exports", "logs", "backups", "cache", "tmp",
        "storage", "exemplo",
    ],
    "exclude_globs": [
        "CLAUDE.md", "CLAUDE.local.md", "config.php", ".env", ".env.*", "*.csv",
        "*.xlsx", "*.xls", "*.ods", "*.sql", "*.sql.gz", "*.dump", "*.zip", "*.gz",
        "*.log", "*.pem", "*.key", "*.p12", "*.pfx", "*.crt", "id_rsa*", "*.bak",
        "*.backup", "*.tmp", "*.swp", "*.pyc",
    ],
    "human_files": ["arquiteto.md", "subregra.md", "CLAUDE.local.md"],
    "transversal": [
        "composer.json", "composer.lock", ".gitignore",
        "packages/heuderdev/helpers/composer.json",
        "packages/heuderdev/helpers/src/IncludeHelper.php",
        "packages/heuderdev/helpers/src/PlatformHelper.php",
        "packages/heuderdev/helpers/src/DbConnectionHelper.php",
        "packages/heuderdev/helpers/src/DbTransactionHelper.php",
        "packages/heuderdev/helpers/src/OrmBase.php",
        "packages/heuderdev/helpers/src/OrmTables.php",
    ],
    "registry_dirs": ["packages/heuderdev/helpers/src"],
    "registry_file": "packages/heuderdev/helpers/src/IncludeHelper.php",
    "limits": {
        "max_file_bytes": 2 * 1024 * 1024,
        "context_file_bytes": 60000,
        "context_total_bytes": 400000,
        "artifact_max_bytes": 65536,
    },
}

MANAGED_TARGETS = (
    "CLAUDE.md",
    ".claude/settings.json",
    ".claude/rules/**/*.md",
    ".claude/agents/**/*.md",
    ".claude/skills/*/**",
)


class ToolError(Exception):
    pass


def find_root(start=None):
    current = Path(start or os.getcwd()).resolve()
    for candidate in [current] + list(current.parents):
        if (candidate / STATE_DIR / CONFIG_FILE).is_file():
            return candidate
    for candidate in [current] + list(current.parents):
        if (candidate / ".git").exists():
            return candidate
    return current


def state_dir(root):
    return Path(root) / STATE_DIR


def load_config(root):
    path = state_dir(root) / CONFIG_FILE
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    if path.is_file():
        try:
            user = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as error:
            raise ToolError("config.json inválido: %s" % error)
        for key, value in user.items():
            if key == "limits" and isinstance(value, dict):
                config["limits"].update(value)
            else:
                config[key] = value
    return config


def config_hash(config):
    return sha256_bytes(canonical_json(config).encode("utf-8"))


def canonical_json(data):
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def content_hash(data):
    """Hash de conteúdo que ignora CRLF/LF em texto (core.autocrlf não vira 'edição manual')."""
    if not is_binary(data):
        data = data.replace(b"\r\n", b"\n")
    return sha256_bytes(data)


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def rel_posix(root, path):
    return PurePosixPath(Path(path).resolve().relative_to(Path(root).resolve())).as_posix()


def safe_path(root, relative):
    """Resolve um caminho relativo dentro da raiz; rejeita absolutos e traversal."""
    if not isinstance(relative, str) or not relative.strip():
        raise ToolError("Caminho vazio.")
    text = relative.replace("\\", "/")
    if text.startswith("/") or re.match(r"^[A-Za-z]:", text) or text.startswith("//"):
        raise ToolError("Caminho absoluto não autorizado: %s" % relative)
    parts = PurePosixPath(text).parts
    if any(part == ".." for part in parts):
        raise ToolError("Traversal não autorizado: %s" % relative)
    root_resolved = Path(root).resolve()
    target = (root_resolved / Path(*parts)).resolve()
    try:
        target.relative_to(root_resolved)
    except ValueError:
        raise ToolError("Caminho fora da raiz: %s" % relative)
    return target


def read_json(path, default=None):
    path = Path(path)
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except ValueError as error:
        raise ToolError("JSON inválido em %s: %s" % (path, error))


def write_if_changed(path, text):
    """Grava só se o conteúdo mudou (evita diffs causados por reexecução)."""
    path = Path(path)
    data = text.encode("utf-8")
    if path.is_file() and path.read_bytes() == data:
        return False
    atomic_write(path, data)
    return True


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name("." + path.name + ".cp-tmp")
    with open(temp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(str(temp), str(path))


_glob_cache = {}


def glob_to_regex(pattern):
    if pattern in _glob_cache:
        return _glob_cache[pattern]
    out = []
    i = 0
    n = len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            if pattern[i:i + 3] == "**/":
                out.append("(?:.*/)?")
                i += 3
                continue
            if pattern[i:i + 2] == "**":
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif c == "?":
            out.append("[^/]")
        elif c == "{":
            end = pattern.find("}", i)
            if end == -1:
                out.append(re.escape(c))
            else:
                options = pattern[i + 1:end].split(",")
                out.append("(?:" + "|".join(re.escape(o) for o in options) + ")")
                i = end
        else:
            out.append(re.escape(c))
        i += 1
    regex = re.compile("^" + "".join(out) + "$")
    _glob_cache[pattern] = regex
    return regex


def glob_match(pattern, path):
    """Casa um caminho POSIX relativo. Padrão sem '/' casa pelo nome do arquivo."""
    if pattern.startswith("./"):
        pattern = pattern[2:]
    if "/" not in pattern:
        return bool(glob_to_regex(pattern).match(PurePosixPath(path).name))
    return bool(glob_to_regex(pattern.lstrip("/")).match(path))


def is_managed_target(path):
    return any(glob_match(pattern, path) for pattern in MANAGED_TARGETS)


SECRET_PATTERNS = [
    ("chave_privada", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----")),
    ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("token_github", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b")),
    ("chave_anthropic", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}")),
    ("chave_sk", re.compile(r"\bsk-[A-Za-z0-9]{32,}\b")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}")),
    ("credencial_literal", re.compile(
        r"""(?i)['"]?\b(?:password|passwd|senha|secret|api[_-]?key|apikey|access[_-]?token|auth[_-]?token|client[_-]?secret)\b['"]?\s*(?:=>|=|:)\s*['"]([^'"\s$]{6,})['"]""")),
    ("url_com_senha", re.compile(r"\b[a-z][a-z0-9+.\-]*://[^/\s:@]+:[^/\s:@]{4,}@")),
]

PLACEHOLDER = re.compile(r"(?i)^(?:x+|\*+|changeme|example|exemplo|(?:sua|minha)?[_-]?senha[\w-]*|your[_-]?\w+|<[^>]+>|\.\.\.|null|none|password|secret|api[_-]?key|token)$")


def scan_secrets(text):
    hits = []
    for line_number, line in enumerate(text.splitlines(), 1):
        for name, pattern in SECRET_PATTERNS:
            match = pattern.search(line)
            if not match:
                continue
            if match.groups() and match.group(1) and PLACEHOLDER.match(match.group(1)):
                continue
            hits.append({"tipo": name, "linha": line_number})
    return hits


def is_binary(data):
    return b"\x00" in data[:8192]


class FrontmatterError(Exception):
    pass


def split_frontmatter(text):
    """Retorna (dict|None, corpo). Frontmatter precisa começar na primeira linha."""
    text = text.lstrip("﻿")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, text
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            block = "\n".join(lines[1:index])
            body = "\n".join(lines[index + 1:])
            return parse_yaml(block), body
    raise FrontmatterError("Frontmatter sem '---' de fechamento.")


def parse_yaml(block):
    try:
        import yaml  # type: ignore
    except ImportError:
        return _parse_yaml_subset(block)
    try:
        data = yaml.safe_load(block)
    except yaml.YAMLError as error:
        raise FrontmatterError("YAML inválido: %s" % str(error).splitlines()[0])
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise FrontmatterError("Frontmatter precisa ser um mapa chave: valor.")
    return data


def _scalar(text, line_number):
    text = text.strip()
    if text == "":
        return None
    if text[0] in "'\"":
        quote = text[0]
        if len(text) < 2 or text[-1] != quote:
            raise FrontmatterError("Linha %d: aspas não fechadas." % line_number)
        inner = text[1:-1]
        return inner.replace("''", "'") if quote == "'" else inner.replace('\\"', '"')
    if text.startswith("[") or text.startswith("{"):
        if not text.endswith("]") and not text.endswith("}"):
            raise FrontmatterError("Linha %d: lista/mapa inline não fechado." % line_number)
        if text.startswith("["):
            inner = text[1:-1].strip()
            return [] if not inner else [_scalar(item, line_number) for item in inner.split(",")]
        raise FrontmatterError("Linha %d: mapa inline não suportado sem PyYAML." % line_number)
    lowered = text.lower()
    if lowered in ("true", "yes", "on"):
        return True
    if lowered in ("false", "no", "off"):
        return False
    if lowered in ("null", "~"):
        return None
    if re.match(r"^-?\d+$", text):
        return int(text)
    if ": " in text or text.endswith(":"):
        raise FrontmatterError("Linha %d: ':' em valor sem aspas." % line_number)
    return text


def _parse_yaml_subset(block):
    data = {}
    lines = block.split("\n")
    i = 0
    while i < len(lines):
        raw = lines[i]
        number = i + 1
        if not raw.strip() or raw.lstrip().startswith("#"):
            i += 1
            continue
        if raw.startswith((" ", "\t")):
            raise FrontmatterError("Linha %d: indentação inesperada." % number)
        match = re.match(r"^([A-Za-z_][\w\-]*)\s*:(.*)$", raw)
        if not match:
            raise FrontmatterError("Linha %d: esperado 'chave: valor'." % number)
        key, rest = match.group(1), match.group(2).strip()
        if key in data:
            raise FrontmatterError("Linha %d: chave duplicada '%s'." % (number, key))
        i += 1
        if rest in ("|", ">", "|-", ">-"):
            collected = []
            while i < len(lines) and (lines[i].startswith((" ", "\t")) or not lines[i].strip()):
                collected.append(lines[i].strip())
                i += 1
            joiner = "\n" if rest.startswith("|") else " "
            data[key] = joiner.join(collected).strip()
        elif rest == "":
            items = []
            nested = {}
            while i < len(lines) and (lines[i].startswith((" ", "\t")) or not lines[i].strip()):
                inner = lines[i].strip()
                if inner.startswith("- "):
                    items.append(_scalar(inner[2:], i + 1))
                elif inner:
                    sub = re.match(r"^([A-Za-z_][\w\-]*)\s*:(.*)$", inner)
                    if not sub:
                        raise FrontmatterError("Linha %d: item inválido." % (i + 1))
                    nested[sub.group(1)] = _scalar(sub.group(2), i + 1)
                i += 1
            if items and nested:
                raise FrontmatterError("Linha %d: lista e mapa misturados." % number)
            data[key] = items if items else (nested if nested else None)
        else:
            data[key] = _scalar(rest, number)
    return data
