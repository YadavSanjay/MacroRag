#!/usr/bin/env python3
"""Build, gate, and push the two MacroRAG container images (the APP side of deploy).

Builds macrorag (backend) + macrorag-ui from THIS repo's Dockerfiles for a target
platform (default linux/arm64 — the Graviton estate default), SHA-tags them, runs a
self-contained acceptance gate INSIDE the built backend image before pushing to ECR.
The infra repo (aws-genai-iac) owns the downstream deploy — scripts/deploy_macrorag_bundle.py
uploads the compose bundle + renders deploy.env, and the EC2 bootstrap pulls these images.
The two repos meet at the ECR registry: infra provisions the genai-dev/macrorag* repos,
this pushes to them. Keeping build+gate here (next to the Dockerfiles) is the clean boundary.

ECR image refs are:  <registry>/<namespace>/<repo>:<arch>-<sha>
  e.g.  123456789012.dkr.ecr.us-east-1.amazonaws.com/genai-dev/macrorag:arm64-abc1234
        123456789012.dkr.ecr.us-east-1.amazonaws.com/genai-dev/macrorag-ui:arm64-abc1234
NOTE the repos are genai-dev/macrorag and genai-dev/macrorag-ui — the backend's repo is
bare `macrorag` (the compose `app` service), NOT `macrorag-backend`.

THE GATE (backend only). MacroRAG's serving image ships NO torch — embedding/rerank are
remote (RagInference) — so a full retrieval/synthesis gate cannot run in-image. This gate
is instead the strongest self-contained deterministic check the image supports: it loads
the typed pydantic config in aws mode and resolves the dual-mode storage façade to S3
(utils.config.load_settings + utils.objectstore.corpus_store/manifest_store, the same
pre-provision smoke as docs/guides/09-packaging-and-deployment.md §3.2). It fails non-zero
on a missing dependency, an import error, an invalid config, or broken S3 wiring — i.e. it
proves the shipped image actually starts and its config/storage layer is sound, WITHOUT any
network / Qdrant / RagInference / LLM. It needs NO mounted config (unlike the runtime, which
mounts rag.aws.yaml): it runs the bare image with two env vars. A live retrieval gate is a
documented follow-on. Gate is ON by default; --no-gate skips it (a push without it is
un-accepted). On an x86 host the arm64 build + gate runs under QEMU emulation (slow).

Run:
    python scripts/build_push_images.py --dry-run     # print the plan, touch nothing
    python scripts/build_push_images.py --no-push     # build + gate locally, no push
    AWS_PROFILE=genai-tf python scripts/build_push_images.py --tag arm64-abc1234  # build->push
    python scripts/build_push_images.py --registry <acct>... --tag arm64-abc1234  # pin registry
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# component -> (Dockerfile, ECR repository name). Both build from the repo root so the
# shared .dockerignore applies. The repo names are NOT uniform: the backend is bare
# `macrorag` (compose `app` service / MACRORAG_IMAGE), the UI is `macrorag-ui`.
COMPONENTS: dict[str, tuple[str, str]] = {
    "backend": ("infra/docker/Dockerfile", "macrorag"),
    "ui": ("ui/Dockerfile", "macrorag-ui"),
}

# In-image acceptance: load the typed config in aws mode and resolve the storage façade
# to S3. Self-contained + deterministic (no network/Qdrant/RagInference/LLM). PYTHONPATH
# is /app/src in the image, so `utils.*` imports resolve. See §3.2 of the packaging guide.
GATE_ENV = {"MACRORAG_DEPLOY_MODE": "aws", "MACRORAG_S3_BUCKET": "gate-smoke"}
GATE_SCRIPT = (
    "from utils.config import load_settings; "
    "from utils.objectstore import corpus_store, manifest_store; "
    "s = load_settings(); "
    "assert s.deploy_mode == 'aws', s.deploy_mode; "
    "assert s.storage_backend == 's3', s.storage_backend; "
    "loc = corpus_store(s).location; "
    "man = manifest_store(s)[0].location; "
    "assert loc.startswith('s3://') and man.startswith('s3://'), (loc, man); "
    "print('gate OK:', s.deploy_mode, s.storage_backend, loc, man)"
)
GATE_CMD = ["python", "-c", GATE_SCRIPT]


def _run(cmd: list[str], dry: bool, **kw: object) -> int:
    print("  $ " + " ".join(cmd))
    if dry:
        return 0
    return subprocess.run(cmd, **kw).returncode  # type: ignore[arg-type]


def _git_sha() -> str:
    out = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True
    )
    return out.stdout.strip() or "dev"


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--platform", default="linux/arm64", help="target platform (default %(default)s)"
    )
    p.add_argument(
        "--namespace",
        default="genai-dev",
        help="ECR repo namespace (default %(default)s) — the repos are <namespace>/macrorag*",
    )
    p.add_argument(
        "--registry",
        default=None,
        help="ECR registry host <acct>.dkr.ecr.<region>.amazonaws.com; else resolved from STS.",
    )
    p.add_argument(
        "--tag", default=None, help="image tag (default <arch>-<git-sha>, e.g. arm64-abc1234)."
    )
    p.add_argument("--region", default="us-east-1")
    p.add_argument("--profile", default=None, help="AWS profile for the STS resolve + ECR login.")
    p.add_argument(
        "--component",
        action="append",
        choices=list(COMPONENTS),
        default=[],
        metavar="NAME",
        help="build only this component (repeatable; default: both).",
    )
    p.add_argument(
        "--no-gate",
        action="store_true",
        help="skip the in-image config/storage gate; a push without it is un-accepted.",
    )
    p.add_argument("--no-push", action="store_true", help="build + gate only; do not push to ECR.")
    p.add_argument("--dry-run", action="store_true", help="print the plan; run nothing.")
    args = p.parse_args()

    components = args.component or list(COMPONENTS)
    sha = _git_sha()
    arch = args.platform.rsplit("/", 1)[-1]  # linux/arm64 -> arm64
    tag = args.tag or f"{arch}-{sha}"
    dry = args.dry_run

    # Resolve the registry only when pushing: explicit --registry wins; else STS (or a placeholder).
    registry = args.registry
    if not args.no_push and not registry:
        if dry:
            registry = f"<acct>.dkr.ecr.{args.region}.amazonaws.com"
        else:
            import boto3

            session = boto3.Session(profile_name=args.profile, region_name=args.region)
            account = session.client("sts").get_caller_identity()["Account"]
            registry = f"{account}.dkr.ecr.{args.region}.amazonaws.com"

    def ref(component: str) -> str:
        repo = f"{args.namespace}/{COMPONENTS[component][1]}"
        return f"{registry}/{repo}:{tag}" if registry else f"{repo}:{tag}"

    print(f"platform={args.platform}  tag={tag}  registry={registry or '(local; --no-push)'}")
    print(
        f"components={components}  gate={'off' if args.no_gate else 'on'}  "
        f"push={'off' if args.no_push else 'on'}"
    )
    for c in components:
        print(f"  {c:8s} {COMPONENTS[c][0]:26s} -> {ref(c)}")

    # 1. Build each (single platform, --load so the gate can run it + push works from the store).
    for c in components:
        cmd = [
            "docker", "buildx", "build", "--platform", args.platform, "--load",
            "-f", str(REPO_ROOT / COMPONENTS[c][0]), "-t", ref(c), str(REPO_ROOT),
        ]
        print(f"\n== build {c} ==")
        if (rc := _run(cmd, dry)) != 0:
            print(f"ERROR: build {c} failed (rc={rc})", file=sys.stderr)
            return rc

    # 2. Gate: run the config/storage smoke INSIDE the built backend image (no data mount).
    if not args.no_gate and "backend" in components:
        print("\n== config/storage gate (inside macrorag backend) ==")
        gate = ["docker", "run", "--rm", "--platform", args.platform]
        for k, v in GATE_ENV.items():
            gate += ["-e", f"{k}={v}"]
        gate += [ref("backend"), *GATE_CMD]
        if (rc := _run(gate, dry)) != 0:
            print(
                f"GATE FAILED (rc={rc}) — NOT pushing. The image's config load or storage "
                "wiring is broken (missing dep / import error / invalid config). Fix it.",
                file=sys.stderr,
            )
            return rc
        print("  gate PASS")
    elif not args.no_gate:
        print("\n  (gate skipped — 'backend' is not in the build set)")

    # 3. Push (ECR login, then docker push each).
    if args.no_push:
        print("\n--no-push: built + gated; nothing pushed.")
        return 0
    if not registry:
        print(
            "ERROR: pushing needs a registry (--registry, or AWS creds for the STS resolve).",
            file=sys.stderr,
        )
        return 2

    print("\n== ecr login + push ==")
    if dry:
        print(f"  $ aws ecr get-login-password --region {args.region} \\")
        print(f"      | docker login --username AWS --password-stdin {registry}")
    else:
        import base64

        import boto3

        session = boto3.Session(profile_name=args.profile, region_name=args.region)
        auth = session.client("ecr").get_authorization_token()["authorizationData"][0]
        user, pwd = base64.b64decode(auth["authorizationToken"]).decode().split(":", 1)
        login = subprocess.run(
            ["docker", "login", "--username", user, "--password-stdin", registry],
            input=pwd,
            text=True,
        )
        if login.returncode:
            print("ERROR: docker login to ECR failed", file=sys.stderr)
            return login.returncode
    for c in components:
        if (rc := _run(["docker", "push", ref(c)], dry)) != 0:
            print(f"ERROR: push {c} failed (rc={rc})", file=sys.stderr)
            return rc

    if dry:
        print("\nDry run complete; nothing built or pushed.")
    else:
        print(
            f"\nDone. Pushed {len(components)} image(s) at tag {tag}. Next: bump the tag in "
            "aws-genai-iac's deploy.env and run its scripts/deploy_macrorag_bundle.py."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
