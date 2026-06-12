# Home-manager module for promptdeploy.
#
# Runs "promptdeploy deploy" during home-manager activation so that
# agents, commands, skills, MCP servers, hooks, and models are kept
# in sync with the source repository on every system rebuild.
#
# Failures never abort activation, but they are not silent either:
# all promptdeploy output is captured to
# $XDG_STATE_HOME/promptdeploy/deploy.log (default
# ~/.local/state/promptdeploy/deploy.log) and a warning naming that
# log is printed when the deploy fails.
{ config, lib, ... }:

let
  cfg = config.programs.promptdeploy;

  # An empty targets list means "deploy to the local machine only":
  # deploy.yaml labels every target that lives on the invoking host
  # with `local`, so `--target local` expands to exactly those.
  # Deploying to *all* targets here would make activation reach out
  # to remote hosts over SSH, which must be an explicit opt-in.
  targetArgs =
    if cfg.targets == [ ] then
      "--target local"
    else
      lib.concatMapStringsSep " " (t: "--target ${lib.escapeShellArg t}") cfg.targets;
in
{
  options.programs.promptdeploy = {
    enable = lib.mkEnableOption "promptdeploy deployment during home-manager activation" // {
      description = ''
        Whether to run promptdeploy during home-manager activation.

        Deployment failures do not abort activation: all promptdeploy
        output is captured to
        `$XDG_STATE_HOME/promptdeploy/deploy.log` (default
        `~/.local/state/promptdeploy/deploy.log`) and a warning naming
        the log file is printed when the deploy fails.
      '';
    };

    package = lib.mkOption {
      type = lib.types.package;
      description = "The promptdeploy package to use.";
    };

    sourceDir = lib.mkOption {
      type = lib.types.str;
      description = "Path to the promptdeploy source directory containing deploy.yaml.";
    };

    targets = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
      description = ''
        Target labels or IDs to deploy to (passed as `--target`
        arguments).  When empty, only the targets carrying the
        `local` label in deploy.yaml are deployed; remote targets are
        never touched implicitly, so activation cannot hang or fail
        on SSH.  Name labels or target IDs explicitly (including
        remote ones) to widen the set.
      '';
    };
  };

  config = lib.mkIf cfg.enable {
    home.activation.promptdeploy = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
      if [[ -d "${cfg.sourceDir}" ]]; then
        promptdeployStateDir="''${XDG_STATE_HOME:-$HOME/.local/state}/promptdeploy"
        promptdeployLog="$promptdeployStateDir/deploy.log"
        mkdir -p "$promptdeployStateDir"
        if ! ( cd "${cfg.sourceDir}" && \
               ${lib.getExe cfg.package} deploy ${targetArgs} --quiet
             ) > "$promptdeployLog" 2>&1; then
          echo "" >&2
          echo "warning: promptdeploy deploy failed; activation continues anyway." >&2
          echo "warning: see $promptdeployLog for details." >&2
        fi
      fi
    '';
  };
}
