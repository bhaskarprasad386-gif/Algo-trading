from pathlib import Path

import yaml


WORKFLOW_PATH = (
    Path(__file__).resolve().parents[2]
    / ".github"
    / "workflows"
    / "cloudflare-production-deploy.yml"
)


def test_cloudflare_production_deploy_workflow_is_present_and_safe():
    assert WORKFLOW_PATH.is_file(), "production Cloudflare deploy workflow must exist"

    workflow = yaml.load(WORKFLOW_PATH.read_text(), Loader=yaml.BaseLoader)
    triggers = workflow["on"]

    assert triggers["push"]["branches"] == ["main"]
    assert "frontend/**" in triggers["push"]["paths"]
    assert "workflow_dispatch" in triggers
    assert "pull_request" not in triggers, "production secrets must not run on PRs"

    assert workflow["permissions"]["contents"] == "read"
    assert str(workflow["concurrency"]["cancel-in-progress"]).lower() == "false"

    deploy_job = workflow["jobs"]["deploy"]
    assert deploy_job["defaults"]["run"]["working-directory"] == "frontend"
    steps = {step["name"]: step for step in deploy_job["steps"]}

    credential_step = steps["Validate Cloudflare credentials"]
    assert "CLOUDFLARE_API_TOKEN" in credential_step["env"]
    assert "CLOUDFLARE_ACCOUNT_ID" in credential_step["env"]
    assert "Set repository secrets" in credential_step["run"]

    assert steps["Build Cloudflare OpenNext worker"]["run"] == "npm run build:cloudflare"
    assert steps["Deploy production to Cloudflare"]["run"] == "npm run cf:deploy"
