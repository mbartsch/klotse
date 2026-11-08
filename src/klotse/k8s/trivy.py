"""Trivy Operator reports (pure, over what the cluster returns; no network, no Qt). The operator (aquasecurity.github.io/v1alpha1) writes
a report object next to each scanned workload: VulnerabilityReport (CVEs in the images), ConfigAuditReport (misconfigurations in the
workload), ExposedSecretReport (secrets baked into images) and RbacAssessmentReport. Klotse only reads them.

The lists are read as the server-side **Table** of each custom resource (the operator's own printer columns: a few counts per report, not
the thousands of vulnerabilities inside), the labels the operator puts on every report say which workload and container it is about, and one
report is read in full only when the user opens it. **Exposed secret reports never show the matched text**: only the rule, category,
severity and target."""
from datetime import datetime, timezone

GROUP, VERSION = "aquasecurity.github.io", "v1alpha1"
KINDS = {  # key -> (plural, tab title, kind of report)
    "vulnerabilities": ("vulnerabilityreports", "Vulnerabilities", "VulnerabilityReport"),
    "config": ("configauditreports", "Config audit", "ConfigAuditReport"),
    "secrets": ("exposedsecretreports", "Exposed secrets", "ExposedSecretReport"),
    "rbac": ("rbacassessmentreports", "RBAC", "RbacAssessmentReport"),
}
SEVERITIES = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN")
COUNT_COLUMNS = ("Critical", "High", "Medium", "Low", "Unknown")
LABEL_KIND, LABEL_NAME, LABEL_CONTAINER = ("trivy-operator.resource.kind", "trivy-operator.resource.name", "trivy-operator.container.name")


def _int(text):
    try:
        return int(str(text).strip())
    except ValueError:
        return 0


def parse_table(names, rows):
    """Report rows from the Table of a report kind. `names` are the column names, `rows` are (cells, metadata) pairs. A row is a dict with
    namespace, report (the object's name), workload_kind, workload, container, repository, tag, scanner and the five severity counts
    (a column the operator version does not have counts 0). The workload comes from the operator's labels."""
    index = {name.lower(): i for i, name in enumerate(names)}
    out = []
    for cells, meta in rows:
        def cell(column):
            i = index.get(column.lower())
            return cells[i] if i is not None and i < len(cells) else ""

        labels = meta.get("labels") or {}
        out.append({"namespace": meta.get("namespace", ""), "report": meta.get("name", cell("Name")),
                    "workload_kind": labels.get(LABEL_KIND, ""), "workload": labels.get(LABEL_NAME, ""),
                    "container": labels.get(LABEL_CONTAINER, ""), "repository": cell("Repository"), "tag": cell("Tag"),
                    "scanner": cell("Scanner"), **{c.lower(): _int(cell(c)) for c in COUNT_COLUMNS}})
    return sorted(out, key=lambda r: (-r["critical"], -r["high"], -r["medium"], r["namespace"], r["workload"], r["container"]))


def totals(rows):
    """Sums of the five counts over report rows, and how many reports have any critical or high finding."""
    sums = {c.lower(): sum(r[c.lower()] for r in rows) for c in COUNT_COLUMNS}
    sums["reports"] = len(rows)
    sums["serious"] = sum(1 for r in rows if r["critical"] or r["high"])
    return sums


def summary_text(rows, title):
    t = totals(rows)
    if not rows:
        return f"No {title.lower()} reports."
    return (f"{t['reports']} reports; {t['critical']} critical, {t['high']} high, {t['medium']} medium, {t['low']} low"
            f"{', ' + str(t['unknown']) + ' unknown' if t['unknown'] else ''}. {t['serious']} have a critical or high finding.")


def _severity_rank(severity):
    return SEVERITIES.index(severity) if severity in SEVERITIES else len(SEVERITIES)


def vulnerabilities(report):
    """[{id, package, installed, fixed, severity, title, score, link}] of one VulnerabilityReport, worst first. `fixed` is empty when no fix
    exists yet."""
    out = []
    for v in (report.get("report") or {}).get("vulnerabilities") or []:
        out.append({"id": v.get("vulnerabilityID", ""), "package": v.get("resource", ""), "installed": v.get("installedVersion", ""),
                    "fixed": v.get("fixedVersion", ""), "severity": str(v.get("severity", "")).upper(), "title": v.get("title", ""),
                    "score": v.get("score"), "link": v.get("primaryLink", "")})
    return sorted(out, key=lambda v: (_severity_rank(v["severity"]), -(v["score"] or 0), v["id"]))


def failed_checks(report):
    """[{id, title, severity, description, remediation, messages}] of the checks that FAILED in a ConfigAuditReport or RbacAssessmentReport."""
    out = []
    for c in (report.get("report") or {}).get("checks") or []:
        if c.get("success"):
            continue
        out.append({"id": c.get("checkID", ""), "title": c.get("title", ""), "severity": str(c.get("severity", "")).upper(),
                    "description": c.get("description", ""), "remediation": c.get("remediation", ""), "messages": list(c.get("messages") or [])})
    return sorted(out, key=lambda c: (_severity_rank(c["severity"]), c["id"]))


def exposed_secrets(report):
    """[{rule, title, severity, category, target}] of an ExposedSecretReport. The matched text is deliberately dropped."""
    out = []
    for s in (report.get("report") or {}).get("secrets") or []:
        out.append({"rule": s.get("ruleID", ""), "title": s.get("title", ""), "severity": str(s.get("severity", "")).upper(),
                    "category": s.get("category", ""), "target": s.get("target", "")})
    return sorted(out, key=lambda s: (_severity_rank(s["severity"]), s["rule"]))


def details(key, report):
    """The rows of the detail window for a report of tab `key`, and its column headings."""
    if key == "vulnerabilities":
        rows = vulnerabilities(report)
        return ["ID", "Package", "Installed", "Fixed in", "Severity", "Title"], [[v["id"], v["package"], v["installed"], v["fixed"], v["severity"], v["title"]] for v in rows]
    if key == "secrets":
        rows = exposed_secrets(report)
        return ["Rule", "Category", "Severity", "Target", "Title"], [[s["rule"], s["category"], s["severity"], s["target"], s["title"]] for s in rows]
    rows = failed_checks(report)
    return ["Check", "Severity", "Title", "Remediation"], [[c["id"], c["severity"], c["title"], c["remediation"]] for c in rows]


def scanned(report):
    """When the operator last updated a report (text), or ''."""
    stamp = (report.get("report") or {}).get("updateTimestamp") or ""
    try:
        when = datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return ""
    return when.strftime("%Y-%m-%d %H:%M UTC")
