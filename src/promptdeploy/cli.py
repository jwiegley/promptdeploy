import argparse
import os
import sys
from pathlib import Path

from .config import Config, expand_target_arg, load_config


def _load_source_dotenv(path: Path) -> None:
    """Load source secrets without allowing it to redefine host identity."""
    from .envsubst import load_dotenv

    key = "PROMPTDEPLOY_HOST"
    had_override = key in os.environ
    original_override = os.environ.get(key)
    try:
        load_dotenv(path)
    finally:
        if had_override:
            assert original_override is not None
            os.environ[key] = original_override
        else:
            os.environ.pop(key, None)


def _load_config_or_exit(args: argparse.Namespace | None = None) -> Config:
    """Load deploy.yaml, exiting with a clean error if it is invalid."""
    try:
        config_path = getattr(args, "config", None)
        bundle_bindings_file = getattr(args, "bundle_bindings_file", None)
        bundle_source_overrides = tuple(getattr(args, "bundle_source", None) or ())
        require_immutable_bundles = bool(
            getattr(args, "require_immutable_bundles", False)
        )
        if (
            config_path is None
            and bundle_bindings_file is None
            and not bundle_source_overrides
            and not require_immutable_bundles
        ):
            return load_config()
        return load_config(
            config_path=config_path,
            bundle_bindings_file=bundle_bindings_file,
            bundle_source_overrides=bundle_source_overrides,
            require_immutable_bundles=require_immutable_bundles,
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


def _select_operation_targets(
    config: Config,
    args: argparse.Namespace,
    *,
    runtime_host: str | None = None,
) -> tuple[list[str], str | None]:
    """Expand target arguments and enforce the optional no-SSH boundary."""
    target_ids = expand_target_arg(getattr(args, "target", None), config)
    if not getattr(args, "local_only", False):
        return target_ids, None

    from .config import current_host, filter_local_target_ids

    runtime_host = runtime_host or current_host()
    target_ids = filter_local_target_ids(
        config,
        target_ids,
        runtime_host=runtime_host,
    )
    if not target_ids:
        raise ValueError(
            f"No selected targets are local to runtime host '{runtime_host}'"
        )
    return target_ids, runtime_host


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="promptdeploy",
        description=(
            "Deploy prompts, agents, skills, and MCP servers to multiple tools."
        ),
    )
    parser.add_argument(
        "--config",
        type=Path,
        metavar="FILE",
        help="Load deployment configuration from FILE instead of searching the CWD",
    )
    parser.add_argument(
        "--bundle-bindings-file",
        type=Path,
        metavar="FILE",
        help="Read immutable bundle source bindings from FILE",
    )
    parser.add_argument(
        "--bundle-source",
        action="append",
        metavar="NAME=ABSOLUTE_PATH",
        help="Bind one bundle to a deliberately mutable development checkout",
    )
    parser.add_argument(
        "--require-immutable-bundles",
        action="store_true",
        help="Reject mutable or non-store bundle bindings",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # deploy subcommand
    deploy_parser = subparsers.add_parser("deploy", help="Deploy items to targets")
    deploy_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )
    deploy_parser.add_argument(
        "--target", action="append", help="Target environment(s) to deploy to"
    )
    deploy_parser.add_argument(
        "--local-only",
        action="store_true",
        help="Exclude every target that would require SSH",
    )
    selection = deploy_parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--only-type",
        action="append",
        choices=[
            "agents",
            "bundles",
            "commands",
            "skills",
            "mcp",
            "models",
            "hooks",
            "marketplaces",
            "prompts",
            "settings",
        ],
        help="Only deploy specific item types",
    )
    selection.add_argument(
        "--only-item",
        action="append",
        metavar="TYPE:NAME",
        help="Deploy exactly one named item; may be repeated",
    )
    deploy_parser.add_argument("--verbose", action="store_true", help="Verbose output")
    deploy_parser.add_argument("--quiet", action="store_true", help="Suppress output")
    deploy_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite items even when unchanged or pre-existing",
    )
    deploy_parser.add_argument(
        "--target-root",
        type=Path,
        metavar="DIR",
        help=(
            "Redirect all deployment output under DIR"
            " (using target IDs as subdirectories)"
        ),
    )

    verify_parser = subparsers.add_parser(
        "verify", help="Strictly verify exact deployed items"
    )
    verify_parser.add_argument(
        "--target", action="append", help="Target environment(s) to verify"
    )
    verify_parser.add_argument(
        "--local-only",
        action="store_true",
        help="Exclude every target that would require SSH",
    )
    verify_parser.add_argument(
        "--only-item",
        action="append",
        required=True,
        metavar="TYPE:NAME",
        help="Verify exactly one named item; may be repeated",
    )
    verify_parser.add_argument(
        "--target-root",
        type=Path,
        metavar="DIR",
        help=("Redirect verification under DIR (using target IDs as subdirectories)"),
    )

    # validate subcommand
    subparsers.add_parser("validate", help="Validate source items and configuration")

    # status subcommand
    status_parser = subparsers.add_parser("status", help="Show deployment status")
    status_parser.add_argument(
        "--target", action="append", help="Target environment(s) to check"
    )
    status_parser.add_argument(
        "--target-root",
        type=Path,
        metavar="DIR",
        help=(
            "Redirect all deployment output under DIR"
            " (using target IDs as subdirectories)"
        ),
    )

    # list subcommand
    list_parser = subparsers.add_parser("list", help="List deployable items")
    list_parser.add_argument(
        "--target", action="append", help="Filter by target environment(s)"
    )
    list_parser.add_argument(
        "--target-root",
        type=Path,
        metavar="DIR",
        help=(
            "Redirect all deployment output under DIR"
            " (using target IDs as subdirectories)"
        ),
    )

    # settings subcommand group
    settings_parser = subparsers.add_parser("settings", help="Manage settings.yaml")
    settings_sub = settings_parser.add_subparsers(
        dest="settings_command", required=True
    )

    init_parser = settings_sub.add_parser(
        "init", help="Bootstrap settings.yaml from live hosts"
    )
    init_parser.add_argument(
        "--from", dest="from_ref", help="Reference target for base"
    )
    init_parser.add_argument("--target", action="append", help="Targets to pull from")
    init_parser.add_argument(
        "--force", action="store_true", help="Overwrite existing settings.yaml"
    )

    rec_parser = settings_sub.add_parser(
        "reconcile", help="Pull host settings drift into overrides"
    )
    rec_parser.add_argument("--target", action="append", help="Targets to reconcile")
    rec_parser.add_argument(
        "--apply", action="store_true", help="Write drift into overrides"
    )

    return parser


def main() -> None:
    args = _build_parser().parse_args()

    if args.command == "deploy":
        _run_deploy(args)
    elif args.command == "verify":
        _run_verify(args)
    elif args.command == "validate":
        _run_validate(args)
    elif args.command == "status":
        _run_status(args)
    elif args.command == "list":
        _run_list(args)
    else:
        # args.command == "settings": both subparsers are declared with
        # required=True, so argparse guarantees the command names.
        if args.settings_command == "init":
            _run_settings_init(args)
        else:
            _run_settings_reconcile(args)


def _run_deploy(args: argparse.Namespace) -> None:
    from .deploy import deploy, parse_item_selector
    from .filters import FilterError
    from .output import Output, Verbosity

    if args.verbose:
        verbosity = Verbosity.VERBOSE
    elif args.quiet:
        verbosity = Verbosity.QUIET
    else:
        verbosity = Verbosity.NORMAL

    out = Output(verbosity)
    out.start_timer()

    config = _load_config_or_exit(args)

    # Capture host identity before the source-controlled dotenv is loaded.
    # An explicitly exported PROMPTDEPLOY_HOST remains honored by current_host,
    # while a repository .env cannot spoof a remote target as local.
    from .config import current_host

    selected_runtime_host = current_host()
    _load_source_dotenv(config.source_root / ".env")

    try:
        target_ids, runtime_host = _select_operation_targets(
            config, args, runtime_host=selected_runtime_host
        )
        raw_selectors = getattr(args, "only_item", None)
        item_selectors = (
            [parse_item_selector(raw) for raw in raw_selectors]
            if raw_selectors is not None
            else None
        )
        target_root = getattr(args, "target_root", None)
        if target_root:
            from .config import remap_targets_to_root

            config = remap_targets_to_root(config, target_root)
    except (OSError, ValueError) as exc:
        out.error(str(exc))
        sys.exit(1)

    from .envsubst import EnvVarError
    from .frontmatter import FrontmatterError
    from .poet import PoetError
    from .ssh import SSHError
    from .targets.claude import JsonConfigError
    from .targets.codex import CodexConfigError

    try:
        actions = deploy(
            config,
            target_ids=target_ids,
            dry_run=args.dry_run,
            verbose=args.verbose,
            item_types=getattr(args, "only_type", None),
            item_selectors=item_selectors,
            force=args.force,
            local_host=runtime_host,
        )
    except (
        FilterError,
        EnvVarError,
        FrontmatterError,
        CodexConfigError,
        JsonConfigError,
        PoetError,
        SSHError,
        ValueError,
    ) as exc:
        out.error(str(exc))
        sys.exit(1)

    prefix = "[dry-run] " if args.dry_run else ""
    symbols = {
        "create": "A",
        "update": "M",
        "remove": "D",
        "skip": " ",
        "pre-existing": "P",
    }

    for act in actions:
        if act.action == "skip" and verbosity < Verbosity.VERBOSE:
            continue
        symbol = symbols.get(act.action, "?")
        out.action(symbol, act.item_type, act.name, act.target_id, prefix=prefix)
        # Surface deploy-time warnings (e.g. undefined Jinja variables) so
        # users running `deploy` without a prior `validate` still see them.
        for warning in act.warnings:
            out.warning(f"{act.item_type} {act.name} -> {act.target_id}: {warning}")

    created = sum(1 for a in actions if a.action == "create")
    updated = sum(1 for a in actions if a.action == "update")
    removed = sum(1 for a in actions if a.action == "remove")
    skipped = sum(1 for a in actions if a.action == "skip")
    pre_existing = sum(1 for a in actions if a.action == "pre-existing")
    out.summary(
        created, updated, removed, skipped, pre_existing=pre_existing, prefix=prefix
    )


def _run_verify(args: argparse.Namespace) -> None:
    from .config import current_host
    from .deploy import parse_item_selector
    from .envsubst import EnvVarError
    from .filters import FilterError
    from .frontmatter import FrontmatterError
    from .ssh import SSHError
    from .verify import verify_items

    config = _load_config_or_exit(args)
    selected_runtime_host = current_host()
    _load_source_dotenv(config.source_root / ".env")
    try:
        target_ids, runtime_host = _select_operation_targets(
            config, args, runtime_host=selected_runtime_host
        )
        selectors = [parse_item_selector(raw) for raw in getattr(args, "only_item", [])]
        target_root = getattr(args, "target_root", None)
        if target_root:
            from .config import remap_targets_to_root

            config = remap_targets_to_root(config, target_root)
        failures = verify_items(
            config,
            target_ids=target_ids,
            item_selectors=selectors,
            local_host=runtime_host,
        )
    except SSHError:
        print("ERROR: remote verification failed", file=sys.stderr)
        sys.exit(1)
    except (EnvVarError, FilterError, FrontmatterError, OSError, ValueError) as exc:
        print(f"ERROR: verification could not run: {exc}", file=sys.stderr)
        sys.exit(1)

    if failures:
        for failure in failures:
            print(
                f"ERROR: {failure.item_type}:{failure.name} -> "
                f"{failure.target_id}: {failure.reason}",
                file=sys.stderr,
            )
        sys.exit(1)
    print(f"Verified {len(selectors)} exact item selector(s).")


def _run_validate(args: argparse.Namespace | None = None) -> None:
    from .validate import validate_all

    config = _load_config_or_exit(args)
    issues = validate_all(config)
    if not issues:
        print("All items valid.")
        return
    errors = 0
    warnings = 0
    for issue in issues:
        prefix = "ERROR" if issue.level == "error" else "WARNING"
        print(f"{prefix}: {issue.file_path}: {issue.message}")
        if issue.level == "error":
            errors += 1
        else:
            warnings += 1
    print(f"\n{errors} error(s), {warnings} warning(s)")
    if errors > 0:
        sys.exit(1)


def _run_status(args: argparse.Namespace) -> None:
    from .envsubst import EnvVarError
    from .filters import FilterError
    from .frontmatter import FrontmatterError
    from .ssh import SSHError
    from .status import get_status

    config = _load_config_or_exit(args)
    try:
        target_ids = expand_target_arg(args.target, config)
        if args.target_root:
            from .config import remap_targets_to_root

            config = remap_targets_to_root(config, args.target_root)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    try:
        entries = get_status(config, target_ids)
    except SSHError:
        print("ERROR: remote status failed", file=sys.stderr)
        sys.exit(1)
    except (EnvVarError, FilterError, FrontmatterError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    if not entries:
        print("No items to report.")
        return
    for entry in entries:
        symbol = {
            "current": " ",
            "changed": "M",
            "new": "A",
            "pending_removal": "D",
        }.get(entry.state, "?")
        print(f"  {symbol}  {entry.item_type:8s} {entry.name:30s} -> {entry.target_id}")


def _run_list(args: argparse.Namespace) -> None:
    from .manifest import load_manifest
    from .targets import create_target

    config = _load_config_or_exit(args)
    try:
        target_ids = expand_target_arg(args.target, config)
        if args.target_root:
            from .config import remap_targets_to_root

            config = remap_targets_to_root(config, args.target_root)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    for target_id in target_ids:
        target_config = config.targets[target_id]
        target = create_target(target_config)
        try:
            target.prepare()

            if not target.exists():
                print(f"{target_id} (not installed)")
                continue

            manifest = load_manifest(target.manifest_path())
            total = sum(len(items) for items in manifest.items.values())

            if total == 0:
                print(f"{target_id}: no managed items")
                continue

            print(f"{target_id}:")
            category_labels = {
                "agents": "Agents",
                "bundles": "Bundles",
                "commands": "Commands",
                "skills": "Skills",
                "mcp_servers": "MCP Servers",
                "models": "Models",
                "hooks": "Hooks",
                "marketplaces": "Marketplaces",
                "prompts": "Prompts",
                "settings": "Settings",
            }
            for category in (
                "agents",
                "bundles",
                "commands",
                "skills",
                "mcp_servers",
                "models",
                "hooks",
                "marketplaces",
                "prompts",
                "settings",
            ):
                items = manifest.items.get(category, {})
                if not items:
                    continue
                label = category_labels.get(category, category)
                print(f"  {label}:")
                for name in sorted(items):
                    print(f"    - {name}")
        finally:
            target.cleanup()


def _run_settings_init(args: argparse.Namespace) -> None:
    from .settings_sync import init_settings

    config = _load_config_or_exit(args)
    out_path = config.source_root / "settings.yaml"
    try:
        target_ids = expand_target_arg(args.target, config)
        init_settings(
            config,
            target_ids,
            from_ref=args.from_ref,
            out_path=out_path,
            force=args.force,
        )
    except (FileExistsError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"Wrote {out_path}")


def _run_settings_reconcile(args: argparse.Namespace) -> None:
    from .settings_sync import reconcile_settings

    config = _load_config_or_exit(args)
    settings_path = config.source_root / "settings.yaml"
    try:
        target_ids = expand_target_arg(args.target, config)
        diffs = reconcile_settings(
            config, target_ids, settings_path=settings_path, apply=args.apply
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    if not diffs:
        print("settings.yaml is in sync with all selected targets.")
        return
    for d in diffs:
        detail = {
            "+": f"{d.key} = {d.host_value!r} (host only)",
            "~": f"{d.key}: {d.rendered_value!r} -> {d.host_value!r}",
            "-": f"{d.key} (settings.yaml only; deploy would add)",
        }[d.kind]
        print(f"  {d.kind}  {d.target_id}: {detail}")
    if args.apply:
        print("Applied host drift into overrides.")
    else:
        print("Re-run with --apply to write these into overrides.")


if __name__ == "__main__":
    main()
