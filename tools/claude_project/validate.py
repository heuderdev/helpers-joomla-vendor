import json
import re
from pathlib import Path, PurePosixPath

from .common import (
    FrontmatterError, ToolError, glob_match, is_managed_target, safe_path,
    content_hash, scan_secrets, sha256_bytes, split_frontmatter,
)

ERRO, AVISO, INFO, NAO_EXECUTADA = "erro", "aviso", "info", "nao_executada"

SKILL_FIELDS = {
    "name", "description", "when_to_use", "argument-hint", "arguments", "disable-model-invocation",
    "user-invocable", "allowed-tools", "disallowed-tools", "model", "effort", "context", "agent",
    "background", "hooks", "paths", "shell", "metadata", "license", "compatibility",
}
AGENT_FIELDS = {
    "name", "description", "tools", "disallowedTools", "model", "permissionMode", "skills", "mcpServers",
    "hooks", "maxTurns", "autoCompactWindow", "memory", "background", "omitClaudeMd", "effort",
    "isolation", "color", "initialPrompt", "experimental",
}
RULE_FIELDS = {"paths"}
KNOWN_TOOLS = {
    "Read", "Write", "Edit", "Glob", "Grep", "Bash", "PowerShell", "WebFetch", "WebSearch",
    "NotebookEdit", "Agent", "Task", "TodoWrite", "Skill", "LSP", "Monitor",
}
MODELS = {"sonnet", "opus", "haiku", "fable", "inherit"}
PERMISSION_MODES = {"default", "acceptEdits", "auto", "dontAsk", "bypassPermissions", "plan", "manual"}
EFFORTS = {"low", "medium", "high", "xhigh", "max"}
COLORS = {"red", "blue", "green", "yellow", "purple", "orange", "pink", "cyan"}
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
IMPORT_RE = re.compile(r"(?:^|\s)@([\w./\-]+\.\w+)")
SETTINGS_KEYS = {"permissions", "env", "hooks", "model", "statusLine", "enabledPlugins", "claudeMdExcludes",
                 "includeCoAuthoredBy", "attribution", "cleanupPeriodDays", "outputStyle", "$schema"}


class Report:
    def __init__(self):
        self.items = []

    def add(self, level, path, message):
        self.items.append({"nivel": level, "arquivo": path, "mensagem": message})

    @property
    def errors(self):
        return [item for item in self.items if item["nivel"] == ERRO]

    def to_dict(self):
        order = [ERRO, AVISO, INFO, NAO_EXECUTADA]
        return sorted(self.items, key=lambda item: (order.index(item["nivel"]), item["arquivo"], item["mensagem"]))


def _strip_code(text):
    text = re.sub(r"(?ms)^(```+|~~~+).*?^\1\s*$", "", text)
    return re.sub(r"`[^`\n]*`", "", text)


def _tool_name(spec):
    return spec.split("(", 1)[0].strip()


def _as_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value).split(",") if item.strip()]


def _paths_list(value):
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def _kind(path):
    if PurePosixPath(path).name == "CLAUDE.md":
        return "claude_md"
    if path == ".claude/settings.json":
        return "settings"
    if path.startswith(".claude/rules/"):
        return "rule"
    if path.startswith(".claude/agents/"):
        return "agent"
    if path.startswith(".claude/skills/"):
        parts = path.split("/")
        return "skill" if len(parts) == 4 and parts[3] == "SKILL.md" else "skill_file"
    return "outro"


def validate_files(root, files, source_paths, limits):
    """files: {caminho_destino: texto}. source_paths: caminhos existentes no repositório (para globs)."""
    report = Report()
    root = Path(root)
    skills = {}
    agents = {}
    paragraphs = {}

    for path in sorted(files):
        try:
            safe_path(root, path)
        except ToolError as error:
            report.add(ERRO, path, str(error))
            continue
        if not is_managed_target(path):
            report.add(ERRO, path, "Destino fora da área de configuração do Claude Code.")
            continue
        text = files[path]
        size = len(text.encode("utf-8"))
        if size > limits["artifact_max_bytes"]:
            report.add(ERRO, path, "Arquivo com %d bytes excede o limite de %d." % (size, limits["artifact_max_bytes"]))
        for hit in scan_secrets(text):
            report.add(ERRO, path, "Possível segredo (%s) na linha %d." % (hit["tipo"], hit["linha"]))
        kind = _kind(path)
        if kind == "settings":
            _check_settings(report, path, text)
            continue
        if not path.endswith(".md"):
            continue
        try:
            meta, body = split_frontmatter(text)
        except FrontmatterError as error:
            report.add(ERRO, path, str(error))
            continue
        lines = text.count("\n") + 1
        if kind == "claude_md":
            if meta is not None:
                report.add(AVISO, path, "CLAUDE.md não usa frontmatter.")
            if lines > 200:
                report.add(AVISO, path, "CLAUDE.md com %d linhas (recomendado < 200)." % lines)
        elif kind == "rule":
            _check_rule(report, path, meta, source_paths)
        elif kind == "agent":
            info = _check_agent(report, path, meta)
            if info:
                if info["name"] in agents:
                    report.add(ERRO, path, "Agent duplicado '%s' (também em %s)." % (info["name"], agents[info["name"]]["path"]))
                agents[info["name"]] = info
        elif kind == "skill":
            info = _check_skill(report, path, meta, lines, source_paths)
            if info:
                if info["name"] in skills:
                    report.add(ERRO, path, "Skill duplicada '%s'." % info["name"])
                skills[info["name"]] = info
        _check_links(report, root, path, body if meta is not None else text, files)
        for block in re.split(r"\n\s*\n", _strip_code(body if meta is not None else text)):
            normalized = " ".join(block.split())
            if len(normalized) >= 200:
                paragraphs.setdefault(normalized, []).append(path)

    for name, info in agents.items():
        for skill in info["skills"]:
            if skill not in skills:
                report.add(ERRO, info["path"], "Agent referencia skill inexistente '%s'." % skill)
            elif skills[skill]["manual"]:
                report.add(ERRO, info["path"], "Skill '%s' tem disable-model-invocation e não pode ser pré-carregada." % skill)
    for block, paths in paragraphs.items():
        if len(set(paths)) > 1:
            report.add(AVISO, paths[0], "Parágrafo duplicado em: %s." % ", ".join(sorted(set(paths))))
    return report


def _check_settings(report, path, text):
    try:
        data = json.loads(text)
    except ValueError as error:
        report.add(ERRO, path, "JSON inválido: %s" % error)
        return
    if not isinstance(data, dict):
        report.add(ERRO, path, "settings.json precisa ser um objeto.")
        return
    for key in data:
        if key not in SETTINGS_KEYS:
            report.add(AVISO, path, "Chave não reconhecida por esta ferramenta: '%s' (confira na documentação)." % key)
    permissions = data.get("permissions", {})
    if not isinstance(permissions, dict):
        report.add(ERRO, path, "'permissions' precisa ser um objeto.")
        return
    for group in ("allow", "deny", "ask"):
        for rule in permissions.get(group, []) or []:
            if not isinstance(rule, str) or not re.match(r"^[A-Za-z_][\w:\-]*(\(.+\))?$", rule):
                report.add(ERRO, path, "Regra de permissão inválida em %s: %r." % (group, rule))
            elif _tool_name(rule) not in KNOWN_TOOLS and not _tool_name(rule).startswith("mcp__"):
                report.add(AVISO, path, "Ferramenta desconhecida na regra %r." % rule)


def _check_rule(report, path, meta, source_paths):
    if meta is None:
        report.add(INFO, path, "Rule sem frontmatter: carregada em toda sessão.")
        return
    for key in meta:
        if key not in RULE_FIELDS:
            report.add(AVISO, path, "Campo '%s' não é usado em rules (ignorado pelo Claude Code)." % key)
    patterns = _paths_list(meta.get("paths"))
    if "paths" in meta and not patterns:
        report.add(ERRO, path, "'paths' vazio ou em formato inválido.")
    for pattern in patterns:
        if not any(glob_match(pattern, source) for source in source_paths):
            report.add(AVISO, path, "Padrão de 'paths' não casa com nenhum arquivo: %s" % pattern)


def _common_fields(report, path, meta, allowed, kind):
    for key in meta:
        if key not in allowed:
            report.add(AVISO, path, "Campo '%s' não é aceito em %s (seria ignorado)." % (key, kind))
    model = meta.get("model")
    if model is not None and str(model) not in MODELS and not str(model).startswith("claude-"):
        report.add(ERRO, path, "Modelo inválido: %r." % model)
    effort = meta.get("effort")
    if effort is not None and effort not in EFFORTS:
        report.add(ERRO, path, "effort inválido: %r." % effort)


def _check_tools(report, path, value, field):
    for spec in _as_list(value):
        name = _tool_name(spec)
        if name not in KNOWN_TOOLS and not name.startswith("mcp__"):
            report.add(AVISO, path, "Ferramenta desconhecida em %s: %r." % (field, spec))


def _check_agent(report, path, meta):
    if meta is None:
        report.add(ERRO, path, "Agent sem frontmatter (o Claude Code trata como documentação).")
        return None
    _common_fields(report, path, meta, AGENT_FIELDS, "agents")
    name = meta.get("name")
    if not name or not isinstance(name, str):
        report.add(ERRO, path, "Agent sem 'name'.")
        return None
    if name.startswith("-") or ":" in name or len(name) > 256:
        report.add(ERRO, path, "Nome de agent inválido: %r." % name)
    if not meta.get("description"):
        report.add(ERRO, path, "Agent sem 'description'.")
    if meta.get("permissionMode") is not None and meta["permissionMode"] not in PERMISSION_MODES:
        report.add(ERRO, path, "permissionMode inválido: %r." % meta["permissionMode"])
    if meta.get("color") is not None and meta["color"] not in COLORS:
        report.add(AVISO, path, "color inválida: %r." % meta["color"])
    if meta.get("isolation") is not None and meta["isolation"] != "worktree":
        report.add(ERRO, path, "isolation só aceita 'worktree'.")
    _check_tools(report, path, meta.get("tools"), "tools")
    _check_tools(report, path, meta.get("disallowedTools"), "disallowedTools")
    if "Skill" in _as_list(meta.get("tools")):
        report.add(AVISO, path, "Use 'skills' para pré-carregar skills, não 'Skill' em tools.")
    return {"name": name, "path": path, "skills": _as_list(meta.get("skills"))}


def _check_skill(report, path, meta, lines, source_paths):
    directory = path.split("/")[2]
    if meta is None:
        report.add(AVISO, path, "SKILL.md sem frontmatter: descrição vira a primeira linha do corpo.")
        meta = {}
    _common_fields(report, path, meta, SKILL_FIELDS, "skills")
    name = meta.get("name") or directory
    if not isinstance(name, str) or not NAME_RE.match(name):
        report.add(AVISO, path, "Nome de skill fora da convenção minúsculas-com-hífen: %r." % name)
    if name in ("anthropic-skills",) or directory == "synced":
        report.add(ERRO, path, "Nome de skill reservado.")
    description = meta.get("description")
    if not description:
        report.add(AVISO, path, "Skill sem 'description': o Claude decide o uso por ela.")
    elif len(str(description)) + len(str(meta.get("when_to_use") or "")) > 1536:
        report.add(AVISO, path, "description + when_to_use passam de 1536 caracteres e serão truncados.")
    if meta.get("context") is not None and meta["context"] != "fork":
        report.add(ERRO, path, "context só aceita 'fork'.")
    if meta.get("shell") is not None and meta["shell"] not in ("bash", "powershell"):
        report.add(ERRO, path, "shell só aceita bash ou powershell.")
    _check_tools(report, path, meta.get("allowed-tools"), "allowed-tools")
    for pattern in _paths_list(meta.get("paths")):
        if not any(glob_match(pattern, source) for source in source_paths):
            report.add(AVISO, path, "Padrão de 'paths' não casa com nenhum arquivo: %s" % pattern)
    if lines > 500:
        report.add(AVISO, path, "SKILL.md com %d linhas (recomendado < 500)." % lines)
    manual = meta.get("disable-model-invocation") in (True, "true", 1)
    return {"name": name, "path": path, "manual": manual}


def _check_links(report, root, path, text, files):
    clean = _strip_code(text)
    base = PurePosixPath(path).parent
    targets = [(m, "link") for m in LINK_RE.findall(clean)]
    if PurePosixPath(path).name == "CLAUDE.md":
        targets += [(m, "import") for m in IMPORT_RE.findall(clean)]
    for target, kind in targets:
        if re.match(r"^[a-z][a-z0-9+.\-]*:", target) or target.startswith("#") or "$" in target:
            continue
        target = target.split("#", 1)[0]
        if not target:
            continue
        relative = (base / target).as_posix()
        parts = []
        for part in relative.split("/"):
            if part == "..":
                if not parts:
                    report.add(ERRO, path, "Referência sai da raiz: %s" % target)
                    break
                parts.pop()
            elif part not in ("", "."):
                parts.append(part)
        else:
            resolved = "/".join(parts)
            if resolved in files or (Path(root) / resolved).exists():
                continue
            report.add(ERRO, path, "Referência local quebrada (%s): %s" % (kind, target))


def run(root, files, source_paths, limits, proposal=None, manifest=None, proposal_files=None):
    report = validate_files(root, files, source_paths, limits)
    if proposal is not None:
        _check_proposal(report, root, proposal, files if proposal_files is None else proposal_files, manifest or {})
    report.add(NAO_EXECUTADA, "-", "Comportamento no Claude Code (acionamento de skills/agents) não é verificável por esta ferramenta.")
    return report


def _check_proposal(report, root, proposal, files, manifest):
    declared = {}
    for entry in proposal.get("arquivos", []):
        path = entry.get("caminho")
        if not path:
            report.add(ERRO, "proposal.json", "Entrada sem 'caminho'.")
            continue
        declared[path] = entry
        if not entry.get("evidencias"):
            report.add(AVISO, path, "Sem evidências associadas na proposta.")
        if not entry.get("fontes"):
            report.add(INFO, path, "Sem 'fontes': mudanças nas fontes não apontarão este artefato.")
        try:
            target = safe_path(root, path)
        except ToolError:
            continue
        current = content_hash(target.read_bytes()) if target.is_file() else None
        managed = manifest.get("artefatos", {}).get(path)
        base = entry.get("base_sha256")
        if current is None:
            continue
        if base == current:
            continue
        if managed and managed.get("sha256") == current:
            continue
        reason = "editado manualmente desde a última aplicação" if managed else "existe e não é gerenciado pela ferramenta"
        report.add(AVISO, path, "Conflito: arquivo %s; o apply não vai sobrescrevê-lo sem base_sha256 = %s." % (reason, current))
    for path in files:
        if path not in declared:
            report.add(ERRO, path, "Arquivo em files/ não declarado no proposal.json.")
    for path in declared:
        if path not in files:
            report.add(ERRO, path, "Declarado no proposal.json mas ausente em files/.")
    for path in proposal.get("obsoletos", []):
        report.add(INFO, path, "Marcado como obsoleto: revise e remova manualmente se for o caso.")
