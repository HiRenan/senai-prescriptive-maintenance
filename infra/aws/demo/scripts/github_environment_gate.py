"""Fail-closed GitHub environment protection preflight without AWS access."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping
from typing import Any, NoReturn, cast
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

EXPECTED_REPOSITORY = "HiRenan/senai-prescriptive-maintenance"
EXPECTED_ENVIRONMENTS = {
    "aws-demo-deploy",
    "aws-demo-plan",
    "aws-demo-teardown",
}
GITHUB_API_ROOT = "https://api.github.com"
GITHUB_API_VERSION = "2022-11-28"
MAX_RESPONSE_BYTES = 1_000_000


class GitHubEnvironmentGateError(RuntimeError):
    """Raised without token, URL, reviewer, or response details."""


def fail(message: str) -> NoReturn:
    raise GitHubEnvironmentGateError(message)


def mapping(value: object, *, context: str) -> Mapping[str, Any]:
    if type(value) is not dict:
        fail(f"{context} não é um objeto JSON base.")
    return cast(dict[str, Any], value)


def sequence(value: object, *, context: str) -> list[object]:
    if type(value) is not list:
        fail(f"{context} não é uma lista JSON base.")
    return cast(list[object], value)


def strict_json(content: bytes) -> Mapping[str, Any]:
    def object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                fail("Resposta GitHub possui chave JSON duplicada.")
            result[key] = value
        return result

    def reject_nonfinite(value: str) -> NoReturn:
        del value
        fail("Resposta GitHub possui número não finito.")

    try:
        parsed = json.loads(
            content.decode("utf-8", errors="strict"),
            object_pairs_hook=object_pairs,
            parse_constant=reject_nonfinite,
        )
    except (UnicodeError, json.JSONDecodeError):
        raise GitHubEnvironmentGateError(
            "Resposta GitHub não é JSON UTF-8 válido."
        ) from None
    return mapping(parsed, context="resposta GitHub")


def audit_environment(
    environment: object,
    branch_policies: object,
    *,
    expected_environment: str,
) -> None:
    if expected_environment not in EXPECTED_ENVIRONMENTS:
        fail("Environment solicitado não pertence ao contrato de entrega.")
    document = mapping(environment, context="environment GitHub")
    if document.get("name") != expected_environment:
        fail("API GitHub devolveu environment diferente do solicitado.")

    rules = sequence(document.get("protection_rules"), context="protection_rules")
    reviewer_rules = [
        mapping(rule, context="regra de proteção")
        for rule in rules
        if mapping(rule, context="regra de proteção").get("type")
        == "required_reviewers"
    ]
    if len(reviewer_rules) != 1:
        fail("Environment exige exatamente uma regra de reviewers.")
    reviewer_rule = reviewer_rules[0]
    if reviewer_rule.get("prevent_self_review") is not True:
        fail("Environment deve impedir autoaprovação do dispatch.")
    reviewers = sequence(reviewer_rule.get("reviewers"), context="reviewers")
    if not reviewers or len(reviewers) > 6:
        fail("Environment deve possuir entre um e seis reviewers.")
    reviewer_ids: set[tuple[str, int]] = set()
    for raw_reviewer in reviewers:
        reviewer = mapping(raw_reviewer, context="reviewer")
        reviewer_type = reviewer.get("type")
        identity = mapping(reviewer.get("reviewer"), context="identidade do reviewer")
        reviewer_id = identity.get("id")
        if (
            reviewer_type not in {"User", "Team"}
            or type(reviewer_id) is not int
            or reviewer_id <= 0
            or (cast(str, reviewer_type), reviewer_id) in reviewer_ids
        ):
            fail("Environment possui reviewer inválido ou duplicado.")
        reviewer_ids.add((cast(str, reviewer_type), reviewer_id))

    branch_policy = mapping(
        document.get("deployment_branch_policy"),
        context="deployment_branch_policy",
    )
    if branch_policy != {
        "custom_branch_policies": True,
        "protected_branches": False,
    }:
        fail("Environment deve usar somente deployment branch policy customizada.")

    policies = mapping(branch_policies, context="branch policies")
    if policies.get("total_count") != 1:
        fail("Environment deve possuir uma única branch policy.")
    entries = sequence(policies.get("branch_policies"), context="branch_policies")
    if len(entries) != 1:
        fail("Environment deve possuir uma única branch policy.")
    entry = mapping(entries[0], context="branch policy")
    if entry.get("name") != "main" or entry.get("type", "branch") != "branch":
        fail("Environment deve permitir exclusivamente a branch main.")


def fetch_json(path: str, *, token: str) -> Mapping[str, Any]:
    if not path.startswith("/repos/HiRenan/senai-prescriptive-maintenance/"):
        fail("Consulta GitHub saiu do repositório aprovado.")
    request = Request(  # noqa: S310 - URL is built from a fixed HTTPS root.
        GITHUB_API_ROOT + path,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "senai-pm-environment-gate/1",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=15) as response:  # noqa: S310 - fixed HTTPS host.
            if response.status != 200:
                fail("API GitHub recusou o preflight do environment.")
            content = response.read(MAX_RESPONSE_BYTES + 1)
    except (HTTPError, URLError, OSError):
        raise GitHubEnvironmentGateError(
            "API GitHub recusou o preflight do environment."
        ) from None
    if len(content) > MAX_RESPONSE_BYTES:
        fail("Resposta GitHub excede o limite operacional.")
    return strict_json(content)


def run_preflight(expected_environment: str, environment: Mapping[str, str]) -> None:
    if environment.get("GITHUB_REPOSITORY") != EXPECTED_REPOSITORY:
        fail("Preflight GitHub executou fora do repositório aprovado.")
    token = environment.get("GITHUB_TOKEN")
    if (
        type(token) is not str
        or len(token) < 20
        or any(character.isspace() for character in token)
    ):
        fail("GITHUB_TOKEN temporário está ausente ou inválido.")
    if expected_environment not in EXPECTED_ENVIRONMENTS:
        fail("Environment solicitado não pertence ao contrato de entrega.")
    encoded_environment = quote(expected_environment, safe="")
    base_path = (
        f"/repos/HiRenan/senai-prescriptive-maintenance/environments/"
        f"{encoded_environment}"
    )
    document = fetch_json(base_path, token=token)
    policies = fetch_json(
        f"{base_path}/deployment-branch-policies?per_page=100&page=1",
        token=token,
    )
    audit_environment(
        document,
        policies,
        expected_environment=expected_environment,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Valida protections do GitHub environment antes do OIDC."
    )
    parser.add_argument("environment", choices=sorted(EXPECTED_ENVIRONMENTS))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    run_preflight(args.environment, os.environ)
    print("Environment GitHub aprovado por reviewers e branch main exatos.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except GitHubEnvironmentGateError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None
    except Exception:
        print("Preflight do environment GitHub falhou com segurança.", file=sys.stderr)
        raise SystemExit(1) from None
