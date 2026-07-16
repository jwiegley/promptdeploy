# Ponytail integration plan (frozen)

This plan records the full Definition of Done for the user request below. It
may be annotated with evidence, but its requirements must not be weakened or
redefined to fit an implementation.

> Study the family of "ponytail" skills and other AI tools in
> `/Users/johnw/Desktop/ponytail`. I want to understand how to reference this
> from promptdeploy so that I can make use of these skills in all of my agent,
> everywhere that I use LLMs. `$command-wiggum`

## Reference target

- Upstream checkout: `/Users/johnw/Desktop/ponytail`
- Repository: `https://github.com/DietrichGebert/ponytail`
- Inspected revision: `16f29800fd2681bdf24f3eb4ccffe38be3baec6b`
- Declared release: `4.8.4`
- Portable behavior: the six `skills/ponytail*` trees plus the compact
  always-on `AGENTS.md` rule set.
- Native tooling to account for: lifecycle hooks and mode state, slash-command
  adapters, plugin/extension manifests, OpenCode and Pi runtimes, Hermes
  integration, MCP package, and instruction-only rule adapters.

## Non-negotiable done criteria

1. **Complete study.** Every top-level ponytail artifact family is classified
   as portable behavior, host adapter, runtime/tooling, development evidence,
   or non-deployable project material. The result identifies dependencies,
   state, security/trust implications, and update provenance.
2. **Reproducible reference.** Promptdeploy has a checked, documented,
   host-portable way to reference a pinned ponytail source. It must work from a
   fresh checkout and from the immutable Nix/Home Manager source path; it may
   not depend only on `/Users/johnw/Desktop/ponytail` existing.
3. **Full skill family.** `ponytail`, `ponytail-review`, `ponytail-audit`,
   `ponytail-debt`, `ponytail-gain`, and `ponytail-help` remain sourced from the
   reference tree (not hand-maintained divergent copies), including future
   auxiliary files inside each skill tree.
4. **Every configured LLM surface.** Each target type present in
   `deploy.yaml`—Claude, Codex, Droid, OpenCode, and GPTel—gets the strongest
   safe form it supports. Skill-capable targets receive the complete skill
   trees. Prompt-only targets receive faithful prompt adapters rather than
   silently losing the capability. Remote instances use the same source and
   target-specific transformation as local instances.
5. **Native runtime where supportable.** Ponytail's always-on/mode tooling is
   enabled through a thin native adapter wherever promptdeploy can manage it
   safely and reproducibly. Unsupported host features are explicitly mapped
   to the best instruction/skill fallback; nothing is claimed as native parity
   without runtime evidence. Node-dependent lifecycle hooks must fail quiet as
   upstream intends and must not rely on plugin-only environment variables
   outside a plugin host.
6. **No collision or stale-copy traps.** Discovery, naming, source confinement,
   manifests, exact-item selection, removal, verification, and target-specific
   transforms handle imported items deterministically. Existing unmanaged user
   artifacts are not overwritten or removed without the repository's normal
   force/adoption rules.
7. **Usable operator path.** Documentation explains the architecture, what each
   target receives, how to deploy/verify just ponytail, how to update the pin,
   required executables or hook trust, and the difference between skill,
   prompt, instruction, and full plugin parity.
8. **Independent verification.** Focused regression tests cover the imported
   source and every target mapping. The full `nix flake check` suite and package
   build pass. A target-root deployment and strict verification prove the six
   named capabilities on all configured local target types without mutating
   live agent configuration. Reference parity is checked against the pinned
   ponytail files and version.
9. **Wiggum closeout.** Logical work units are committed and independently
   audited; real findings are fixed; non-hidden `doc/observations/*.md` items
   are drained if present; the branch is locally current with `origin/main`;
   and a final fess audit finds no actionable defect in the completed work.

## Planned work units

1. Inventory ponytail and promptdeploy, then record the artifact/target matrix
   and chosen source-reference design.
2. Add the reproducible ponytail source reference and source-discovery support
   needed to consume it without weakening confinement.
3. Deploy the six skill trees to native skill targets and faithful adapters to
   prompt-only targets, with selection/manifest/verification coverage.
4. Add safe native runtime adapters where the target and promptdeploy model
   support them; document deliberate fallbacks elsewhere.
5. Add operator documentation and provenance/update checks.
6. Run focused and full verification, independent audits, observation cleanup,
   local rebase/restack, parity audit, and final fess audit.

## Authoritative evidence

- Source and adapter coverage: current files at the pinned ponytail revision.
- Supported target behavior: promptdeploy target implementations and regression
  tests, not assumptions about product names.
- Fresh/Nix portability: `nix flake check`, package contents, and Home Manager
  module/activation tests.
- Deployment parity: isolated `--target-root` output plus `verify --strict` and
  exact path/content comparisons for all six capabilities.
- Completion: committed diff, clean status, current-base ancestry, passing full
  suite, independent audit reports, and an item-by-item audit of this section.

## Constraints

- Do not install dependencies on the fly or use `nix develop`; use the checked
  environment and existing Nix checks.
- Do not deploy into live agent directories during development; use isolated
  target roots until the implementation is proven and separately authorized.
- Do not push, submit, force-update, delete user data, or rewrite shared history
  inside the autonomous loop.
- Preserve unrelated user work and keep Git/shared-state mutation in the
  coordinator only.
