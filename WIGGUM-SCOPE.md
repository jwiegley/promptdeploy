# Ponytail proportional integration scope (frozen)

This file supersedes every completion and runtime requirement in both
`WIGGUM-PLAN.md` and `docs/ponytail-integration.md`. The user explicitly
rejected the runtime-heavy expansion and clarified that the goal is to bring
Ponytail into the current working environment through promptdeploy. Already
committed work is preserved, but it does not enlarge this scope.

The user subsequently clarified the Nix boundary: normal `nix run` operation
must not deploy from the working checkout or a separately read input. It must
run against one composed deployment derivation in the Nix store, with explicit
flake-input-to-deployment mappings. This raises criterion 4 without reviving
any optional runtime requirement.

## Completion criteria

1. **Reproducible reference.** Ponytail is pinned at a reviewed revision and
   promptdeploy can consume that source from a fresh Nix checkout. A mutable
   `/Users/johnw/Desktop/ponytail` override remains available for development.
2. **Complete skill family.** The complete source trees for `ponytail`,
   `ponytail-review`, `ponytail-audit`, `ponytail-debt`, `ponytail-gain`, and
   `ponytail-help` are referenced from the pin, including auxiliary files.
3. **Proportional target mapping.** Claude, Codex, Droid, and OpenCode receive
   the six native skill trees through their existing promptdeploy skill paths.
   GPTel receives six deterministic prompt projections because it has no native
   skill surface. Remote target definitions use the same mapping as local ones.
4. **Easy operator path.** The repository declares Ponytail; Nix copies the
   repository and mapped external input into one inspectable
   `packages.deployment` store tree; normal apps select that tree's config and
   immutable binding explicitly; and concise documentation gives one copyable
   Ponytail-only preview/deploy/verify path. A separate raw app is development
   only.
5. **Current environment.** An isolated `--target-root` run proves every
   configured target mapping. After that proof, only the current host's local
   target group is deployed and strictly verified; no remote fleet rollout is
   implied or authorized.
6. **Independent verification.** Focused tests cover the pin, six-tree target
   matrix, GPTel projections, composed deployment, CWD isolation, Home Manager
   default, and operator path. Every flake check passes except the one
   PowerShell-specific test the user explicitly waived; the replacement pytest
   gate retains full line and branch coverage. The final commit receives an
   independent fess audit, observations are drained, and the branch is locally
   current with `origin/main`.

## Explicitly optional follow-up

Lifecycle hooks, ambient mode persistence, status lines, plugin registration,
runtime publication, CAS settings updates, recovery journals, rollback, and
fleet rollout are not completion requirements. Existing committed substrate
for those ideas remains dormant and is not extended in this run.

## Constraints

- Do not mutate remote targets.
- Do not install dependencies or use `nix develop`.
- Do not overwrite unmanaged target artifacts; retain promptdeploy's existing
  collision and adoption rules.
- Keep the six canonical skill trees sourced from Ponytail rather than copying
  or maintaining divergent `SKILL.md` files.
- Do not push, submit, force-update, or rewrite shared history.
