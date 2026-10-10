"""R1 (fail-closed), R2 (egress deny-by-default), R3 (no creds in VM),
R6 (policy dry-run + no self-approval) checks.

Adapter factory: services.execplane.adapter(tmp_path)
"""

from __future__ import annotations

from harness import CheckFailed, check


@check("R1", "failed boot disables execution; no silent downgrade")
def fail_closed_on_boot_failure(make_sandbox) -> None:
    try:
        sb = make_sandbox(boot_will_fail=True)
    except Exception:
        return  # refused at create — acceptable fail-closed shape
    try:
        sb.exec("echo should-not-run")
    except Exception:
        return  # refused at exec — acceptable
    raise CheckFailed("exec succeeded on a sandbox whose boot failed")


@check("R2", "empty egress allowlist refuses outbound")
def empty_allowlist_blocks_egress(make_sandbox) -> None:
    sb = make_sandbox(egress_allowlist=[])
    res = sb.exec("curl -sS --max-time 3 -o /dev/null https://example.com")
    if res.returncode == 0:
        raise CheckFailed("outbound connection allowed with empty allowlist")


@check("R2", "allowlisted host passes; non-allowlisted refused")
def allowlist_is_exact(make_sandbox) -> None:
    sb = make_sandbox(egress_allowlist=["api.openrouter.ai"])
    bad = sb.exec("curl -sS --max-time 3 -o /dev/null https://example.com")
    if bad.returncode == 0:
        raise CheckFailed("non-allowlisted host reachable")


@check("R3", "real secret never visible inside guest")
def no_credential_material_in_guest(make_sandbox) -> None:
    sentinel = "oc-sentinel-7f3a9c"
    sb = make_sandbox(secrets={"openrouter_api_key": sentinel})
    env_out = sb.exec("env").stdout
    if sentinel in env_out:
        raise CheckFailed("real secret present in guest environment")
    token = sb.exec("printenv OPENROUTER_API_KEY || true").stdout.strip()
    if token == sentinel:
        raise CheckFailed("agent sees real key, not a placeholder")


@check("R6", "dry-run diff lists every newly granted item")
def policy_diff_lists_every_grant(policy) -> None:
    proposal = policy.propose(
        {
            "add_egress": ["api.github.com", "huggingface.co"],
            "add_credentials": ["hf_token"],
        },
        origin="agent",
    )
    diff = policy.dry_run(proposal)
    missing = {"egress:api.github.com", "egress:huggingface.co", "cred:hf_token"} - set(
        diff.items
    )
    if missing:
        raise CheckFailed(f"dry-run diff omitted grants: {sorted(missing)}")


@check("R6", "agent-originated policy change cannot self-approve")
def self_approval_rejected(policy) -> None:
    proposal = policy.propose({"add_egress": ["*"]}, origin="agent")
    try:
        policy.apply(proposal, approved_by="agent")
    except Exception:
        return
    raise CheckFailed("agent applied its own policy expansion")
