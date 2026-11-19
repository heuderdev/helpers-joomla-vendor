from .common import glob_match

IMPACT_ORDER = ["sem_impacto_conhecido", "localizado", "transversal"]


def _compare(old, new):
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    modified = sorted(path for path in set(old) & set(new) if old[path] != new[path])
    return added, modified, removed


def _impact(path, kind, config, manifest):
    if any(glob_match(pattern, path) for pattern in config["transversal"]):
        return "transversal", []
    if kind in ("adicionado", "removido"):
        for directory in config["registry_dirs"]:
            if path.startswith(directory.rstrip("/") + "/") and path.endswith(".php"):
                return "transversal", []
    affected = []
    for artifact, info in sorted(manifest.get("artefatos", {}).items()):
        if any(glob_match(pattern, path) for pattern in info.get("fontes", [])):
            affected.append(artifact)
    if affected:
        return "localizado", affected
    return "sem_impacto_conhecido", []


def compute(manifest, inventory, config):
    baseline = manifest.get("baseline")
    current_sources = {path: entry["sha256"] for path, entry in inventory["fontes"].items()}
    result = {
        "primeira_geracao": baseline is None,
        "fontes": [],
        "renomeacoes_provaveis": [],
        "humanos": [],
        "config_alterada": False,
        "artefatos": [],
        "impacto": "sem_impacto_conhecido",
    }
    old_sources = baseline["fontes"] if baseline else {}
    added, modified, removed = _compare(old_sources, current_sources)
    for kind, paths in (("adicionado", added), ("modificado", modified), ("removido", removed)):
        for path in paths:
            impact, affected = _impact(path, kind, config, manifest)
            if baseline is None:
                impact, affected = "transversal", []
            result["fontes"].append({"caminho": path, "mudanca": kind, "impacto": impact, "artefatos": affected})
    by_hash = {}
    for path in removed:
        by_hash.setdefault(old_sources[path], []).append(path)
    for path in added:
        for old in by_hash.get(current_sources[path], []):
            result["renomeacoes_provaveis"].append({"de": old, "para": path})

    if baseline is not None:
        result["config_alterada"] = baseline.get("config_hash") != inventory["config_hash"]
        h_added, h_modified, h_removed = _compare(baseline.get("humanos", {}), inventory["humanos"])
        for kind, paths in (("adicionado", h_added), ("modificado", h_modified), ("removido", h_removed)):
            for path in paths:
                result["humanos"].append({"caminho": path, "mudanca": kind})

    managed = manifest.get("artefatos", {})
    present = inventory["artefatos"]
    for path in sorted(set(managed) | set(present)):
        if path not in managed:
            result["artefatos"].append({"caminho": path, "estado": "nao_gerenciado"})
        elif path not in present:
            result["artefatos"].append({"caminho": path, "estado": "ausente"})
        elif present[path] != managed[path]["sha256"]:
            result["artefatos"].append({"caminho": path, "estado": "editado_manualmente"})

    levels = [item["impacto"] for item in result["fontes"]]
    if result["config_alterada"] or result["humanos"]:
        levels.append("transversal")
    if any(item["estado"] in ("editado_manualmente", "ausente") for item in result["artefatos"]):
        levels.append("localizado")
    if levels:
        result["impacto"] = max(levels, key=IMPACT_ORDER.index)
    return result


def has_changes(result):
    return bool(result["fontes"] or result["humanos"] or result["config_alterada"]
                or any(item["estado"] != "nao_gerenciado" for item in result["artefatos"]))


def summarize(result):
    lines = []
    if result["primeira_geracao"]:
        lines.append("Primeira geração: nenhuma aplicação anterior registrada (%d fontes)." % len(result["fontes"]))
    else:
        counts = {}
        for item in result["fontes"]:
            counts[item["mudanca"]] = counts.get(item["mudanca"], 0) + 1
        lines.append("Fontes: " + (", ".join("%s=%d" % item for item in sorted(counts.items())) or "sem mudanças"))
        for item in result["fontes"]:
            extra = (" -> " + ", ".join(item["artefatos"])) if item["artefatos"] else ""
            lines.append("  [%s] %s (%s)%s" % (item["mudanca"], item["caminho"], item["impacto"], extra))
    for item in result["renomeacoes_provaveis"]:
        lines.append("  renomeação provável (mesmo hash): %s -> %s" % (item["de"], item["para"]))
    if result["config_alterada"]:
        lines.append("Configuração da varredura alterada: reanálise transversal recomendada.")
    for item in result["humanos"]:
        lines.append("Instrução humana %s: %s" % (item["mudanca"], item["caminho"]))
    for item in result["artefatos"]:
        lines.append("Artefato %s: %s" % (item["estado"].replace("_", " "), item["caminho"]))
    lines.append("Impacto geral: " + result["impacto"])
    return lines
