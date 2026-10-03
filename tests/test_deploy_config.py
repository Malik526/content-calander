"""Guards for the Railway build configuration (Milestone 3.14 follow-up:
build secret hardening). No Docker is needed: these read the committed
Dockerfile / railway*.json / .dockerignore and fail if a change would put
runtime secrets back into the image build, drop ffmpeg, or reintroduce a
start command that relies on shell expansion (Railway runs a Dockerfile
service's start command in exec form). The real image build is Railway's;
see docs/evaluations/productization/milestone-3.14-hosted-e2e-validation.md."""

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DOCKERFILE = (REPO / "Dockerfile").read_text()
INSTRUCTIONS = [line.strip() for line in DOCKERFILE.splitlines() if line.strip() and not line.strip().startswith("#")]

# Every runtime secret the services read (AGENTS.md "Data and Credential Safety").
RUNTIME_SECRETS = [
    "DATABASE_URL", "SERVICE_ROLE_KEY", "SUPABASE_JWT_SECRET", "CREDENTIAL_ENCRYPTION_KEY",
    "TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET", "ANTHROPIC_API_KEY",
]


def test_dockerfile_declares_no_build_args():
    """Railway passes a service variable into a Dockerfile build only when an
    ARG declares it — no ARG means no variable (secret or not) enters the build."""
    assert not [line for line in INSTRUCTIONS if line.upper().startswith("ARG")]


@pytest.mark.parametrize("secret", RUNTIME_SECRETS)
def test_dockerfile_never_references_a_runtime_secret(secret):
    assert secret not in "\n".join(INSTRUCTIONS)


def test_dockerfile_installs_ffmpeg_and_the_package():
    joined = "\n".join(INSTRUCTIONS)
    assert re.search(r"apt-get install[^\n]*\bffmpeg\b", joined)
    assert "pip install -r requirements.txt" in joined
    assert re.search(r"pip install \.$", joined, re.MULTILINE)  # non-editable: ships migrations as package data


@pytest.mark.parametrize("config_name,expected_command", [
    ("railway.json", "python3 cli/run_api.py"),
    ("railway.worker.json", "python3 cli/run_worker.py"),
])
def test_railway_services_build_from_the_dockerfile(config_name, expected_command):
    config = json.loads((REPO / config_name).read_text())
    assert config["build"] == {"builder": "DOCKERFILE", "dockerfilePath": "Dockerfile"}
    command = config["deploy"]["startCommand"]
    assert command == expected_command
    assert "$" not in command  # exec form: no shell to expand variables (run_api reads $PORT itself)


def test_api_service_keeps_its_healthcheck():
    assert json.loads((REPO / "railway.json").read_text())["deploy"]["healthcheckPath"] == "/api/health"


def test_dockerignore_keeps_local_secrets_and_data_out_of_the_context():
    ignored = {line.strip() for line in (REPO / ".dockerignore").read_text().splitlines()}
    for entry in (".env", ".env.*", "**/.env", "data/", "content/", ".venv"):
        assert entry in ignored


def test_api_binds_to_platform_port_without_flags(monkeypatch):
    """The start command no longer passes --host/--port; run_api must pick
    both up from $PORT on its own."""
    import importlib.util
    import sys

    monkeypatch.setenv("PORT", "4321")
    monkeypatch.setattr(sys, "argv", ["run_api.py"])
    spec = importlib.util.spec_from_file_location("run_api", REPO / "cli" / "run_api.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    args = module.parse_args()
    assert (args.host, args.port) == ("0.0.0.0", 4321)
