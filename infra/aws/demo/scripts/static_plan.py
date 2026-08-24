"""Create offline valid/invalid plans without initializing the remote backend."""

from __future__ import annotations

import argparse
import os
import secrets
import shutil
import subprocess
import tempfile
from collections.abc import Mapping
from pathlib import Path

S3_BACKEND_BLOCK = '\n  backend "s3" {}\n'
INHERITED_ENVIRONMENT_KEYS = {
    "COMSPEC",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "LANG",
    "LC_ALL",
    "NO_PROXY",
    "PATHEXT",
    "SYSTEMROOT",
    "WINDIR",
}
ISOLATED_TERRAFORM_KEYS = {
    "TF_CLI_CONFIG_FILE",
    "TF_DATA_DIR",
    "TF_IN_AUTOMATION",
    "TF_INPUT",
    "TF_VAR_OFFLINE_PLAN_NONCE",
    "TF_VAR_OFFLINE_VALIDATION",
}
OFFLINE_CONTROL_IDENTIFIERS = ("offline_plan_nonce", "offline_validation")


class StaticPlanError(RuntimeError):
    """Raised when an offline Terraform validation does not meet its contract."""


def isolated_environment(
    host_environment: Mapping[str, str],
    *,
    isolation_root: Path,
    offline_plan_nonce: str,
) -> dict[str, str]:
    if len(offline_plan_nonce) != 64 or any(
        character not in "0123456789abcdef" for character in offline_plan_nonce
    ):
        raise StaticPlanError("O nonce efêmero do harness possui formato inválido.")

    child_environment = {
        key: value
        for key, value in host_environment.items()
        if key.upper() in INHERITED_ENVIRONMENT_KEYS
    }
    home = isolation_root / "home"
    app_data = isolation_root / "app-data"
    local_app_data = isolation_root / "local-app-data"
    temporary = isolation_root / "tmp"
    terraform_data = isolation_root / "terraform-data"
    cli_config = isolation_root / "terraform.rc"
    aws_config = isolation_root / "aws-config"
    aws_credentials = isolation_root / "aws-credentials"
    for directory in (home, app_data, local_app_data, temporary, terraform_data):
        directory.mkdir(parents=True, exist_ok=True)
    cli_config.write_text("disable_checkpoint = true\n", encoding="utf-8", newline="\n")
    aws_config.write_text("", encoding="utf-8", newline="\n")
    aws_credentials.write_text("", encoding="utf-8", newline="\n")

    child_environment.update(
        {
            "APPDATA": str(app_data),
            "AWS_ACCESS_KEY_ID": "offline-validation",
            "AWS_CONFIG_FILE": str(aws_config),
            "AWS_EC2_METADATA_DISABLED": "true",
            "AWS_SDK_LOAD_CONFIG": "0",
            "AWS_SECRET_ACCESS_KEY": "offline-validation",
            "AWS_SHARED_CREDENTIALS_FILE": str(aws_credentials),
            "CHECKPOINT_DISABLE": "1",
            "HOME": str(home),
            "LOCALAPPDATA": str(local_app_data),
            "TEMP": str(temporary),
            "TF_CLI_CONFIG_FILE": str(cli_config),
            "TF_DATA_DIR": str(terraform_data),
            "TF_IN_AUTOMATION": "true",
            "TF_INPUT": "false",
            "TF_VAR_offline_plan_nonce": offline_plan_nonce,
            "TF_VAR_offline_validation": "true",
            "TMP": str(temporary),
            "USERPROFILE": str(home),
        }
    )
    terraform_keys = {
        key.upper() for key in child_environment if key.upper().startswith("TF_")
    }
    if terraform_keys != ISOLATED_TERRAFORM_KEYS:
        raise StaticPlanError(
            "O ambiente filho contém controles Terraform fora da allowlist isolada."
        )
    return child_environment


def require_public_var_files_without_offline_controls(*var_files: Path) -> None:
    for var_file in var_files:
        try:
            content = var_file.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise StaticPlanError(
                "Não foi possível verificar um arquivo público de variáveis."
            ) from error
        if any(identifier in content for identifier in OFFLINE_CONTROL_IDENTIFIERS):
            raise StaticPlanError(
                "Arquivos públicos de variáveis não podem declarar controles offline."
            )


def run(
    executable: Path,
    arguments: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - executable is resolved and checked first.
        [str(executable), *arguments],
        check=False,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
    )


def require_success(
    result: subprocess.CompletedProcess[str], *, command_name: str
) -> None:
    if result.returncode != 0:
        raise StaticPlanError(
            f"{command_name} falhou com código {result.returncode}:\n{result.stdout}"
        )


def copy_static_module(module_root: Path, destination: Path) -> None:
    for source in module_root.glob("*.tf"):
        shutil.copy2(source, destination / source.name)

    lock_file = module_root / ".terraform.lock.hcl"
    if not lock_file.is_file():
        raise StaticPlanError(".terraform.lock.hcl ausente; execute providers lock.")
    shutil.copy2(lock_file, destination / lock_file.name)

    versions_path = destination / "versions.tf"
    versions = versions_path.read_text(encoding="utf-8")
    if versions.count(S3_BACKEND_BLOCK) != 1:
        raise StaticPlanError("Bloco backend S3 parcial inesperado em versions.tf.")
    versions_path.write_text(
        versions.replace(S3_BACKEND_BLOCK, "\n", 1),
        encoding="utf-8",
        newline="\n",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Valida o módulo em cópia temporária com backend local implícito e "
            "provider offline."
        )
    )
    parser.add_argument("--terraform", required=True, type=Path)
    parser.add_argument("--plan-json", required=True, type=Path)
    parser.add_argument(
        "--valid-var-file",
        default=Path("examples/demo.tfvars.example"),
        type=Path,
    )
    parser.add_argument(
        "--invalid-var-file",
        default=Path("tests/invalid.tfvars.example"),
        type=Path,
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    executable = args.terraform.resolve(strict=True)
    if not executable.is_file():
        raise StaticPlanError("O caminho de Terraform não é um arquivo.")

    module_root = Path(__file__).resolve().parents[1]
    valid_vars = (module_root / args.valid_var_file).resolve(strict=True)
    invalid_vars = (module_root / args.invalid_var_file).resolve(strict=True)
    plan_json = args.plan_json.resolve()
    require_public_var_files_without_offline_controls(valid_vars, invalid_vars)

    with tempfile.TemporaryDirectory(prefix="sen67-terraform-") as temporary:
        workdir = Path(temporary)
        offline_plan_nonce = secrets.token_hex(32)
        child_env = isolated_environment(
            os.environ,
            isolation_root=workdir / ".isolation",
            offline_plan_nonce=offline_plan_nonce,
        )
        copy_static_module(module_root, workdir)
        shutil.copy2(valid_vars, workdir / "valid.tfvars")
        shutil.copy2(invalid_vars, workdir / "invalid.tfvars")

        require_success(
            run(
                executable,
                ["init", "-backend=false", "-input=false", "-no-color"],
                cwd=workdir,
                env=child_env,
            ),
            command_name="terraform init -backend=false",
        )
        require_success(
            run(executable, ["validate", "-no-color"], cwd=workdir, env=child_env),
            command_name="terraform validate",
        )

        no_nonce_env = child_env.copy()
        del no_nonce_env["TF_VAR_offline_plan_nonce"]
        no_nonce = run(
            executable,
            [
                "plan",
                "-input=false",
                "-lock=false",
                "-no-color",
                "-refresh=false",
                "-var-file=valid.tfvars",
            ],
            cwd=workdir,
            env=no_nonce_env,
        )
        if no_nonce.returncode == 0:
            raise StaticPlanError(
                "terraform plan aceitou offline_validation sem o nonce efêmero."
            )
        nonce_error = "offline_validation=true exige o nonce"
        if nonce_error not in no_nonce.stdout:
            raise StaticPlanError(
                "A prova sem nonce não acionou o gate efêmero esperado."
            )

        require_success(
            run(
                executable,
                [
                    "plan",
                    "-input=false",
                    "-lock=false",
                    "-no-color",
                    "-out=demo.tfplan",
                    "-refresh=false",
                    "-var-file=valid.tfvars",
                ],
                cwd=workdir,
                env=child_env,
            ),
            command_name="terraform plan válido",
        )

        shown = run(
            executable,
            ["show", "-json", "demo.tfplan"],
            cwd=workdir,
            env=child_env,
        )
        require_success(shown, command_name="terraform show -json")
        if offline_plan_nonce in shown.stdout:
            raise StaticPlanError("O plano JSON persistiu o nonce efêmero do harness.")
        plan_json.parent.mkdir(parents=True, exist_ok=True)
        plan_json.write_text(shown.stdout, encoding="utf-8", newline="\n")

        invalid = run(
            executable,
            [
                "plan",
                "-input=false",
                "-lock=false",
                "-no-color",
                "-refresh=false",
                "-var-file=invalid.tfvars",
            ],
            cwd=workdir,
            env=child_env,
        )
        if invalid.returncode == 0:
            raise StaticPlanError("terraform plan aceitou a configuração inválida.")
        required_errors = (
            "api_desired_count deve ser 0 ou 1",
            "api_image_digest deve usar o formato sha256",
            "budget_alert_email deve ter formato de e-mail válido",
            "monthly_budget_usd deve ficar entre USD 1 e USD 16",
        )
        missing_errors = [
            error for error in required_errors if error not in invalid.stdout
        ]
        if missing_errors:
            raise StaticPlanError(
                "O plano inválido não provou todos os gates: "
                + ", ".join(missing_errors)
            )

    print(
        "Planos estáticos aprovados: modo offline sem nonce rejeitado, nonce "
        "ausente do JSON, configuração válida aceita e configuração inválida "
        "rejeitada nos quatro gates esperados."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
