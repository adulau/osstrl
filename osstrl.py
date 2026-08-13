#!/usr/bin/env python3
"""Estimate an Open Source Software Technology Readiness Level (OSSTRL)
from GitHub-verifiable evidence inspired by the Apereo OSS Health and
Sustainability Rubric.

This is deliberately an *estimate*, not an official Apereo score and not a
substitute for classical Technology Readiness Level assessment. Criteria that
cannot be established from GitHub are not silently treated as failures.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from urllib.parse import quote, urlparse

import requests

API_BASE = "https://api.github.com"
DEFAULT_API_VERSION = os.environ.get("GITHUB_API_VERSION", "2026-03-10")


def dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def days_since(value: str | None, now: datetime) -> float | None:
    parsed = dt(value)
    if not parsed:
        return None
    return max(0.0, (now - parsed).total_seconds() / 86400.0)


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def step_score(value: float, levels: list[tuple[float, float]]) -> float:
    """Return score for first threshold >= value; final level should be inf."""
    for threshold, score in levels:
        if value <= threshold:
            return score
    return levels[-1][1]


@dataclass
class CriterionResult:
    key: str
    category: str
    title: str
    weight: float
    score: float | None
    evidence: list[str]
    github_verifiable: bool = True
    apereo_mapping: str = ""

    @property
    def status(self) -> str:
        return "unknown" if self.score is None else "evaluated"

    @property
    def weighted_points(self) -> float:
        return 0.0 if self.score is None else self.weight * self.score


class GitHubError(RuntimeError):
    pass


class GitHubClient:
    def __init__(self, token: str | None = None, timeout: int = 20):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": DEFAULT_API_VERSION,
                "User-Agent": "osstrl-apereo-prototype/0.1",
            }
        )
        if token:
            self.session.headers["Authorization"] = f"Bearer {token}"

    def get(self, path: str, params: dict[str, Any] | None = None, *, optional: bool = False) -> Any:
        url = path if path.startswith("http") else API_BASE + path
        r = self.session.get(url, params=params, timeout=self.timeout)
        if r.status_code == 403 and "rate limit" in r.text.lower():
            reset = r.headers.get("X-RateLimit-Reset", "unknown")
            raise GitHubError(
                f"GitHub API rate limit exceeded (reset={reset}). Set GITHUB_TOKEN for a higher limit."
            )
        if optional and r.status_code in (403, 404, 409, 422):
            return None
        if r.status_code >= 400:
            raise GitHubError(f"GitHub API {r.status_code} for {r.url}: {r.text[:300]}")
        return r.json()


REPO_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def parse_repo(value: str) -> tuple[str, str]:
    value = value.strip().rstrip("/")
    if REPO_RE.match(value):
        owner, repo = value.split("/", 1)
        return owner, repo.removesuffix(".git")
    u = urlparse(value)
    if u.netloc.lower() in {"github.com", "www.github.com"}:
        parts = [p for p in u.path.split("/") if p]
        if len(parts) >= 2:
            return parts[0], parts[1].removesuffix(".git")
    raise ValueError("Repository must be OWNER/REPO or a github.com/OWNER/REPO URL")


def contains_any(paths_lower: set[str], patterns: Iterable[str]) -> bool:
    for p in paths_lower:
        for pattern in patterns:
            if pattern in p:
                return True
    return False


def basename_any(paths_lower: set[str], names: set[str]) -> bool:
    return any(p.rsplit("/", 1)[-1] in names for p in paths_lower)


def semantic_version(tag: str) -> tuple[int, int, int] | None:
    m = re.search(r"(?:^|[^0-9])v?(\d+)\.(\d+)\.(\d+)(?:$|[-+])", tag)
    if not m:
        return None
    return tuple(int(x) for x in m.groups())


def release_cadence_score(releases: list[dict[str, Any]], now: datetime) -> tuple[float | None, list[str], dict[str, Any]]:
    stable = [r for r in releases if not r.get("draft") and not r.get("prerelease") and r.get("published_at")]
    dates = sorted((dt(r["published_at"]) for r in stable), reverse=True)
    dates = [x for x in dates if x is not None]
    if not dates:
        return 0.0, ["No stable GitHub releases detected."], {"stable_releases": 0, "recent_24m": 0}

    last_days = (now - dates[0]).total_seconds() / 86400
    recent = [d for d in dates if d >= now - timedelta(days=730)]
    evidence = [f"{len(stable)} stable releases observed; {len(recent)} published in the last 24 months.", f"Latest stable release is {last_days:.0f} days old."]

    recency = 1.0 if last_days <= 90 else 0.8 if last_days <= 180 else 0.55 if last_days <= 365 else 0.25 if last_days <= 730 else 0.05
    frequency = min(1.0, len(recent) / 8.0)

    regularity = 0.5
    if len(recent) >= 3:
        asc = sorted(recent)
        gaps = [(b - a).total_seconds() / 86400 for a, b in zip(asc, asc[1:])]
        mean = statistics.fmean(gaps)
        if mean > 0:
            cv = statistics.pstdev(gaps) / mean if len(gaps) > 1 else 0.0
            regularity = clamp(1.0 - cv / 1.5)
            evidence.append(f"Median release interval: {statistics.median(gaps):.0f} days; cadence regularity={regularity:.2f}.")

    score = 0.45 * recency + 0.35 * frequency + 0.20 * regularity
    return clamp(score), evidence, {"stable_releases": len(stable), "recent_24m": len(recent), "latest_release_days": round(last_days, 1)}


def responsiveness_score(items: list[dict[str, Any]], kind: str) -> tuple[float | None, list[str]]:
    closed = [x for x in items if x.get("created_at") and x.get("closed_at")]
    if not items:
        return None, [f"No recent {kind} sample was available; responsiveness left unknown."]
    comment_fraction = sum(1 for x in items if (x.get("comments") or 0) > 0) / len(items)
    if closed:
        durations = [max(0.0, (dt(x["closed_at"]) - dt(x["created_at"])).total_seconds() / 86400) for x in closed]
        med = statistics.median(durations)
        speed = 1.0 if med <= 2 else 0.85 if med <= 7 else 0.65 if med <= 30 else 0.4 if med <= 90 else 0.15
        score = 0.7 * speed + 0.3 * comment_fraction
        return score, [f"{len(closed)}/{len(items)} sampled {kind} are closed; median time-to-close={med:.1f} days.", f"{comment_fraction:.0%} of sampled {kind} have at least one comment."]
    return 0.25 * comment_fraction, [f"No sampled {kind} are closed; {comment_fraction:.0%} have at least one comment."]


def collect_github(client: GitHubClient, owner: str, repo: str, now: datetime) -> dict[str, Any]:
    slug = f"{owner}/{repo}"
    qslug = f"/{quote(owner)}/{quote(repo)}"
    base = f"/repos{qslug}"

    meta = client.get(base)
    community = client.get(base + "/community/profile", optional=True)
    contributors = client.get(base + "/contributors", {"per_page": 100, "anon": "1"}, optional=True) or []
    commits = client.get(base + "/commits", {"since": (now - timedelta(days=365)).isoformat(), "per_page": 100}, optional=True) or []
    releases = client.get(base + "/releases", {"per_page": 100}, optional=True) or []
    issues_raw = client.get(base + "/issues", {"state": "all", "sort": "updated", "direction": "desc", "per_page": 100}, optional=True) or []
    issues = [x for x in issues_raw if "pull_request" not in x]
    pulls = client.get(base + "/pulls", {"state": "all", "sort": "updated", "direction": "desc", "per_page": 100}, optional=True) or []
    workflows = client.get(base + "/actions/workflows", {"per_page": 100}, optional=True)
    tree = client.get(base + f"/git/trees/{quote(meta['default_branch'], safe='')}", {"recursive": "1"}, optional=True)

    paths = set()
    tree_truncated = False
    if tree and isinstance(tree, dict):
        tree_truncated = bool(tree.get("truncated"))
        paths = {str(x.get("path", "")).lower() for x in tree.get("tree", []) if x.get("path")}

    workflow_list = [] if not workflows else workflows.get("workflows", [])

    return {
        "slug": slug,
        "meta": meta,
        "community": community,
        "contributors": contributors,
        "commits_365d": commits,
        "releases": releases,
        "issues": issues,
        "pulls": pulls,
        "workflows": workflow_list,
        "paths": paths,
        "tree_truncated": tree_truncated,
    }


def evaluate(data: dict[str, Any], now: datetime) -> tuple[list[CriterionResult], dict[str, Any]]:
    meta = data["meta"]
    paths: set[str] = data["paths"]
    community = data["community"] or {}
    files = (community.get("files") or {}) if isinstance(community, dict) else {}

    def has_file(*names: str) -> bool:
        lowered = {n.lower() for n in names}
        if basename_any(paths, lowered):
            return True
        for name in names:
            key = name.upper().replace(".MD", "")
            if files.get(key):
                return True
        return False

    created = dt(meta.get("created_at"))
    age_years = (now - created).total_seconds() / (365.25 * 86400) if created else 0.0
    pushed_days = days_since(meta.get("pushed_at"), now)
    contributor_count = len([c for c in data["contributors"] if c.get("type") != "Bot" and not str(c.get("login", "")).endswith("[bot]")])
    commit_count = len(data["commits_365d"])
    commit_capped = commit_count >= 100
    release_score, release_ev, release_stats = release_cadence_score(data["releases"], now)
    issue_resp, issue_ev = responsiveness_score(data["issues"], "issues")
    pr_resp, pr_ev = responsiveness_score(data["pulls"], "pull requests")
    if issue_resp is None and pr_resp is None:
        response = None
        response_ev = issue_ev + pr_ev
    elif issue_resp is None:
        response, response_ev = pr_resp, pr_ev
    elif pr_resp is None:
        response, response_ev = issue_resp, issue_ev
    else:
        response = (issue_resp + pr_resp) / 2
        response_ev = issue_ev + pr_ev

    readme = has_file("readme.md", "readme.rst", "readme.txt") or bool(files.get("readme"))
    contributing = has_file("contributing.md", "contributing.rst") or bool(files.get("contributing"))
    coc = has_file("code_of_conduct.md", "code-of-conduct.md") or bool(files.get("code_of_conduct"))
    license_present = bool(meta.get("license")) or has_file("license", "license.md", "copying", "copying.md") or bool(files.get("license"))
    issue_template = contains_any(paths, [".github/issue_template", "issue_template.md"]) or bool(files.get("issue_template"))
    pr_template = contains_any(paths, ["pull_request_template"]) or bool(files.get("pull_request_template"))
    governance = basename_any(paths, {"governance.md", "maintainers.md", "maintainers", "codeowners"}) or contains_any(paths, [".github/codeowners"])
    roadmap = basename_any(paths, {"roadmap.md", "roadmap.rst", "roadmap.txt"}) or contains_any(paths, ["docs/roadmap", "documentation/roadmap"])
    funding = contains_any(paths, [".github/funding.yml", ".github/funding.yaml"])
    docs_dir = any(p.startswith("docs/") or p.startswith("documentation/") for p in paths)
    changelog = basename_any(paths, {"changelog.md", "changes.md", "history.md", "news.md"})
    tests = any(
        p.startswith(("test/", "tests/", "spec/", "specs/"))
        or "/tests/" in p
        or "/test/" in p
        or p.rsplit("/", 1)[-1].startswith(("test_", "tests_"))
        for p in paths
    )
    ci = bool(data["workflows"]) or contains_any(paths, [".github/workflows/", ".gitlab-ci.yml", "jenkinsfile", ".circleci/"])
    security_policy = has_file("security.md") or bool(files.get("security"))
    security_automation = contains_any(paths, [".github/dependabot.yml", ".github/dependabot.yaml", "codeql", "dependency-review", "trivy", "snyk", "renovate.json"])
    privacy_policy = basename_any(paths, {"privacy.md", "privacy-policy.md", "privacy_policy.md"}) or contains_any(paths, ["docs/privacy", "documentation/privacy"])
    integration_artifacts = contains_any(paths, ["openapi", "swagger", "dockerfile", "docker-compose", "compose.yml", "compose.yaml", "helm/", "charts/"]) or basename_any(paths, {"pyproject.toml", "package.json", "pom.xml", "build.gradle", "cargo.toml", "go.mod"})
    semver_release = any(semantic_version(str(r.get("tag_name", ""))) for r in data["releases"] if not r.get("draft"))

    # Category weights total exactly 100. These are prototype weights, not Apereo's original weights.
    c: list[CriterionResult] = []
    add = c.append

    age_score = 0.1 if age_years < 0.25 else 0.3 if age_years < 0.5 else 0.5 if age_years < 1 else 0.7 if age_years < 2 else 0.85 if age_years < 3 else 1.0
    add(CriterionResult("community.age", "Community", "Project maturity / years in practice", 6, age_score, [f"Repository age: {age_years:.2f} years."], apereo_mapping="maturity; number of years in practice"))

    contributor_score = 0.1 if contributor_count <= 1 else 0.3 if contributor_count < 5 else 0.6 if contributor_count < 10 else 0.8 if contributor_count < 20 else 1.0
    add(CriterionResult("community.contributors", "Community", "Contributor diversity", 8, contributor_score, [f"{contributor_count} non-bot contributors observed (GitHub endpoint capped to the first 100)."], apereo_mapping="diversity; number of unique contributors"))
    add(CriterionResult("community.coc", "Community", "Code of Conduct", 4, 1.0 if coc else 0.0, ["Code of Conduct detected." if coc else "No Code of Conduct file detected."], apereo_mapping="code of conduct"))
    add(CriterionResult("community.responsiveness", "Community", "Community responsiveness", 6, response, response_ev, apereo_mapping="responsiveness; response time"))
    openness_score = (0.5 if not meta.get("private") else 0.0) + (0.5 if contributing else 0.0)
    add(CriterionResult("community.openness", "Community", "Openness to contribution", 4, openness_score, [f"Repository is {'public' if not meta.get('private') else 'private'}.", "CONTRIBUTING guidance detected." if contributing else "No CONTRIBUTING guidance detected."], apereo_mapping="openness"))

    add(CriterionResult("governance.management", "Governance", "Management / governance documentation", 5, 1.0 if governance else 0.0, ["Governance/maintainer/CODEOWNERS artifact detected." if governance else "No GOVERNANCE, MAINTAINERS, or CODEOWNERS artifact detected."], apereo_mapping="management; governance documentation"))
    add(CriterionResult("governance.roadmap", "Governance", "Published roadmap", 3, 1.0 if roadmap else 0.0, ["Roadmap artifact detected." if roadmap else "No roadmap artifact detected in the repository."], apereo_mapping="roadmaps"))
    resolution_score = 0.5 * float(issue_template) + 0.5 * float(pr_template)
    add(CriterionResult("governance.resolution", "Governance", "Documented contribution/resolution practices", 4, resolution_score, [f"Issue template: {'yes' if issue_template else 'no'}; pull-request template: {'yes' if pr_template else 'no'}."], apereo_mapping="resolution practices"))
    add(CriterionResult("governance.funding", "Governance", "Funding metadata", 3, 1.0 if funding else 0.0, ["GitHub FUNDING metadata detected." if funding else "No .github/FUNDING.yml detected."], apereo_mapping="funding"))
    mgmt_score = 0.5 * float(contributing) + 0.5 * float(governance)
    add(CriterionResult("governance.documentation", "Governance", "Contributor/management documentation", 3, mgmt_score, [f"CONTRIBUTING: {'yes' if contributing else 'no'}; governance artifact: {'yes' if governance else 'no'}."], apereo_mapping="documentation"))

    if pushed_days is None:
        dev_score = None
        dev_ev = ["Repository push date unavailable."]
    else:
        recency = 1.0 if pushed_days <= 30 else 0.8 if pushed_days <= 90 else 0.55 if pushed_days <= 180 else 0.3 if pushed_days <= 365 else 0.05
        volume = min(1.0, commit_count / 60.0)
        dev_score = 0.55 * recency + 0.45 * volume
        dev_ev = [f"Last push: {pushed_days:.0f} days ago.", f"Commits in last 365 days: {'100+' if commit_capped else commit_count}."]
    add(CriterionResult("development.activity", "Development", "Development activity", 7, dev_score, dev_ev, apereo_mapping="development activity"))
    add(CriterionResult("development.releases", "Development", "Release consistency", 7, release_score, release_ev, apereo_mapping="consistency of product releases"))
    add(CriterionResult("development.license", "Development", "Open-source license metadata", 5, 1.0 if license_present else 0.0, [f"Detected license: {(meta.get('license') or {}).get('spdx_id', 'file present')}." if license_present else "No license detected by GitHub or repository tree."], apereo_mapping="license agreements"))
    add(CriterionResult("development.ci", "Development", "Continuous integration", 4, 1.0 if ci else 0.0, [f"{len(data['workflows'])} GitHub Actions workflows found." if data["workflows"] else ("CI configuration detected." if ci else "No CI configuration detected.")], apereo_mapping="development activity / integrations"))
    add(CriterionResult("development.tests", "Development", "Automated test suite", 4, 1.0 if tests else 0.0, ["Test tree/files detected." if tests else "No conventional test directory/file pattern detected."], apereo_mapping="development quality (also aligns with Apereo's proposed future test coverage criterion)"))
    add(CriterionResult("development.integrations", "Development", "Integration / standards artifacts", 3, 1.0 if integration_artifacts else 0.0, ["Integration/API/package/deployment metadata detected." if integration_artifacts else "No generic integration/API/deployment artifact detected."], apereo_mapping="integrations; standards"))

    docs_score = 0.55 * float(readme) + 0.45 * float(docs_dir)
    add(CriterionResult("support.documentation", "Support", "User/developer documentation", 5, docs_score, [f"README: {'yes' if readme else 'no'}; docs directory: {'yes' if docs_dir else 'no'}."], apereo_mapping="documentation"))
    release_notes_score = 0.5 * float(changelog) + 0.5 * float(semver_release)
    add(CriterionResult("support.compatibility", "Support", "Change/backwards-compatibility communication proxy", 4, release_notes_score, [f"CHANGELOG/history file: {'yes' if changelog else 'no'}; semantic-version-like release tags: {'yes' if semver_release else 'no'}."], apereo_mapping="backwards compatibility (proxy only)"))
    support_score = 0.5 * float(bool(meta.get("has_issues"))) + 0.5 * float(bool(meta.get("has_discussions")))
    add(CriterionResult("support.channels", "Support", "Public support channels", 3, support_score, [f"GitHub Issues: {'enabled' if meta.get('has_issues') else 'disabled'}; Discussions: {'enabled' if meta.get('has_discussions') else 'disabled'}."], apereo_mapping="services and support"))
    add(CriterionResult("support.website", "Support", "Project website / external documentation", 2, 1.0 if meta.get("homepage") else 0.0, [f"Homepage: {meta.get('homepage')}" if meta.get("homepage") else "No repository homepage configured."], apereo_mapping="support/documentation discoverability"))

    add(CriterionResult("security.policy", "Security / Privacy", "Security policy / vulnerability reporting", 4, 1.0 if security_policy else 0.0, ["SECURITY.md/security policy detected." if security_policy else "No SECURITY.md/security policy detected."], apereo_mapping="security review / security practices"))
    add(CriterionResult("security.automation", "Security / Privacy", "Automated dependency/security maintenance", 3, 1.0 if security_automation else 0.0, ["Dependabot/CodeQL/security automation detected." if security_automation else "No common repository security automation detected."], apereo_mapping="security review (automatable proxy)"))
    add(CriterionResult("security.privacy", "Security / Privacy", "Privacy policy artifact", 3, 1.0 if privacy_policy else 0.0, ["Privacy policy artifact detected." if privacy_policy else "No repository privacy-policy artifact detected; applicability may require manual review."], apereo_mapping="privacy policy"))

    aux = {
        "age_years": age_years,
        "contributors": contributor_count,
        "commits_365d": commit_count,
        "commit_count_capped": commit_capped,
        "pushed_days": pushed_days,
        "release_stats": release_stats,
        "readme": readme,
        "license": license_present,
        "ci": ci,
        "tests": tests,
        "security_policy": security_policy,
        "governance": governance,
        "code_of_conduct": coc,
        "tree_truncated": data["tree_truncated"],
    }
    return c, aux


def compute_osstrl(criteria: list[CriterionResult], aux: dict[str, Any]) -> dict[str, Any]:
    total_weight = sum(x.weight for x in criteria)
    evaluated_weight = sum(x.weight for x in criteria if x.score is not None)
    points = sum(x.weighted_points for x in criteria)
    score = 100.0 * points / evaluated_weight if evaluated_weight else 0.0
    confidence = evaluated_weight / total_weight if total_weight else 0.0

    # Score bands are intentionally simple and configurable for the prototype.
    bands = [(15, 1), (25, 2), (35, 3), (45, 4), (55, 5), (65, 6), (75, 7), (85, 8), (math.inf, 9)]
    raw_level = next(level for threshold, level in bands if score < threshold)

    # Readiness gates prevent a high aggregate score from claiming advanced readiness
    # without basic operational evidence. These are OSSTRL prototype gates, not Apereo rules.
    max_level = 9
    failed_gates: list[str] = []
    if not aux["license"] or not aux["readme"]:
        max_level = min(max_level, 2)
        failed_gates.append("OSSTRL >=3 requires both a detected license and README.")
    if (aux["commits_365d"] == 0 and (aux["pushed_days"] is None or aux["pushed_days"] > 365)):
        max_level = min(max_level, 3)
        failed_gates.append("OSSTRL >=4 requires evidence of development activity in the last year.")
    if not (aux["ci"] or aux["tests"]):
        max_level = min(max_level, 4)
        failed_gates.append("OSSTRL >=5 requires CI or a detectable automated test suite.")
    if aux["release_stats"].get("stable_releases", 0) < 1 or aux["contributors"] < 2:
        max_level = min(max_level, 4)
        failed_gates.append("OSSTRL >=5 requires at least one stable release and two contributors.")
    if aux["contributors"] < 5 or (aux["pushed_days"] is not None and aux["pushed_days"] > 180) or aux["release_stats"].get("latest_release_days", 99999) > 365:
        max_level = min(max_level, 5)
        failed_gates.append("OSSTRL >=6 requires >=5 contributors, recent development, and a release within 12 months.")
    if aux["age_years"] < 2 or aux["contributors"] < 10 or not (aux["security_policy"] or aux["governance"]):
        max_level = min(max_level, 6)
        failed_gates.append("OSSTRL >=7 requires >=2 years history, >=10 contributors, and security or governance documentation.")
    if aux["age_years"] < 3 or aux["contributors"] < 20 or not aux["security_policy"] or not aux["governance"]:
        max_level = min(max_level, 7)
        failed_gates.append("OSSTRL >=8 requires >=3 years history, >=20 contributors, governance documentation, and a security policy.")
    if aux["age_years"] < 5 or aux["contributors"] < 30 or not aux["code_of_conduct"] or not aux["ci"] or not aux["tests"] or aux["release_stats"].get("recent_24m", 0) < 5:
        max_level = min(max_level, 8)
        failed_gates.append("OSSTRL 9 requires >=5 years history, >=30 contributors, CoC, CI, tests, and >=5 stable releases in 24 months.")

    level = min(raw_level, max_level)
    return {
        "osstrl": level,
        "raw_osstrl_from_score": raw_level,
        "maximum_level_allowed_by_gates": max_level,
        "score_percent": round(score, 2),
        "confidence_percent": round(100 * confidence, 2),
        "evaluated_weight": evaluated_weight,
        "total_weight": total_weight,
        "failed_gates": failed_gates,
    }


def build_report(data: dict[str, Any], criteria: list[CriterionResult], result: dict[str, Any], aux: dict[str, Any], now: datetime) -> dict[str, Any]:
    categories: dict[str, dict[str, float]] = {}
    for c in criteria:
        x = categories.setdefault(c.category, {"points": 0.0, "evaluated_weight": 0.0, "total_weight": 0.0})
        x["total_weight"] += c.weight
        if c.score is not None:
            x["evaluated_weight"] += c.weight
            x["points"] += c.weighted_points
    for x in categories.values():
        x["score_percent"] = round(100 * x["points"] / x["evaluated_weight"], 2) if x["evaluated_weight"] else 0.0
        x["confidence_percent"] = round(100 * x["evaluated_weight"] / x["total_weight"], 2) if x["total_weight"] else 0.0

    meta = data["meta"]
    return {
        "schema": "osstrl-apereo-prototype/0.1",
        "generated_at": now.isoformat(),
        "repository": {
            "full_name": meta.get("full_name"),
            "url": meta.get("html_url"),
            "description": meta.get("description"),
            "default_branch": meta.get("default_branch"),
            "archived": meta.get("archived"),
            "fork": meta.get("fork"),
            "stars": meta.get("stargazers_count"),
            "forks": meta.get("forks_count"),
            "open_issues": meta.get("open_issues_count"),
        },
        "result": result,
        "category_scores": categories,
        "criteria": [
            {
                **asdict(c),
                "status": c.status,
                "weighted_points": round(c.weighted_points, 3),
                "score": None if c.score is None else round(c.score, 3),
            }
            for c in criteria
        ],
        "diagnostics": aux,
        "methodology": {
            "basis": "GitHub-verifiable subset/proxies of the Apereo OSS Health and Sustainability Rubric.",
            "important": "OSSTRL is a prototype readiness estimate. It is not an official Apereo assessment and is not equivalent to formal TRL evidence from operational deployments.",
            "unknown_policy": "Unavailable/non-verifiable criteria are excluded from the denominator and reduce confidence rather than automatically scoring zero.",
            "manual_evidence_not_proven_by_github": [
                "number and significance of real-world implementations/adopters",
                "validation in relevant or operational environments",
                "funding sustainability beyond repository funding metadata",
                "formal accessibility conformance",
                "mobile/user-experience suitability",
                "commercial/professional support actually available",
                "independent security reviews or audits not published in the repository",
                "privacy-policy applicability and compliance",
            ],
        },
    }


def markdown_report(report: dict[str, Any]) -> str:
    r = report["result"]
    repo = report["repository"]
    lines = [
        f"# OSSTRL report — {repo['full_name']}",
        "",
        f"**Estimated OSSTRL:** **{r['osstrl']} / 9**  ",
        f"**GitHub evidence score:** **{r['score_percent']:.2f}%**  ",
        f"**Evidence confidence:** **{r['confidence_percent']:.2f}%**",
        "",
        "> Prototype estimate based only on GitHub-verifiable evidence inspired by the Apereo OSS Health and Sustainability Rubric; not an official Apereo or formal TRL assessment.",
        "",
        "## Category scores",
        "",
        "| Category | Score | Evidence coverage |",
        "|---|---:|---:|",
    ]
    for name, v in report["category_scores"].items():
        lines.append(f"| {name} | {v['score_percent']:.1f}% | {v['confidence_percent']:.1f}% |")
    lines += ["", "## Criteria", "", "| Category | Criterion | Weight | Score | Evidence |", "|---|---|---:|---:|---|"]
    for c in report["criteria"]:
        score = "unknown" if c["score"] is None else f"{100*c['score']:.0f}%"
        ev = " ".join(c["evidence"]).replace("|", "\\|")
        lines.append(f"| {c['category']} | {c['title']} | {c['weight']:.0f} | {score} | {ev} |")
    if r["failed_gates"]:
        lines += ["", "## Readiness gates limiting the level", ""]
        for gate in r["failed_gates"]:
            lines.append(f"- {gate}")
    lines += ["", "## Notes", "", "- Criteria that GitHub cannot establish reliably (for example real-world implementations/adoption, funding sustainability beyond repository metadata, accessibility conformance, and actual deployment environment) require manual evidence.", "- `time-to-close` is used as a practical GitHub proxy for community responsiveness; it is not the same as time-to-first-response."]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Estimate an OSSTRL 1-9 from GitHub-verifiable Apereo OSS Rubric evidence.")
    parser.add_argument("repository", help="OWNER/REPO or https://github.com/OWNER/REPO")
    parser.add_argument("--format", choices=("json", "markdown", "summary"), default="summary")
    parser.add_argument("--output", help="Write full JSON/Markdown report to this path")
    args = parser.parse_args()

    try:
        owner, repo = parse_repo(args.repository)
        now = datetime.now(timezone.utc)
        client = GitHubClient(os.environ.get("GITHUB_TOKEN"))
        data = collect_github(client, owner, repo, now)
        criteria, aux = evaluate(data, now)
        result = compute_osstrl(criteria, aux)
        report = build_report(data, criteria, result, aux, now)

        if args.format == "json":
            rendered = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
        elif args.format == "markdown":
            rendered = markdown_report(report)
        else:
            rendered = (
                f"{data['slug']}: OSSTRL {result['osstrl']}/9 | "
                f"score {result['score_percent']:.1f}% | confidence {result['confidence_percent']:.1f}%\n"
            )

        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                # Output file should follow requested --format. Summary still writes summary only.
                f.write(rendered)
        sys.stdout.write(rendered)
        return 0
    except (ValueError, GitHubError, requests.RequestException) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
