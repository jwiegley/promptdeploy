# Home Manager module for exact, fail-closed promptdeploy activation.
{ self }:
{
  config,
  lib,
  pkgs,
  ...
}:

let
  cfg = config.programs.promptdeploy;
  system = pkgs.stdenv.hostPlatform.system;
  defaultRevision = self.rev or "";
  packagePassthru = cfg.package.passthru or { };
  packageSource = packagePassthru.promptdeploySource or null;
  packageRevision = packagePassthru.promptdeployRevision or null;
  sourceString = toString cfg.source;
  validTarget = target: builtins.match "[A-Za-z0-9][A-Za-z0-9._-]*" target != null;
  validItem =
    item: builtins.match "[A-Za-z0-9][A-Za-z0-9._-]*:[A-Za-z0-9][A-Za-z0-9._-]*" item != null;
  driver = pkgs.callPackage ./mk-activation-driver.nix {
    package = cfg.package;
    source = cfg.source;
    stateDir = cfg.stateDir;
    inherit (cfg)
      targets
      exactItems
      lockWaitSeconds
      transactionTimeoutSeconds
      logMaxBytes
      ;
  };
in
{
  options.programs.promptdeploy = {
    enable = lib.mkEnableOption "exact promptdeploy activation";

    package = lib.mkOption {
      type = lib.types.package;
      default = self.packages.${system}.default;
      defaultText = lib.literalExpression "self.packages.\${pkgs.system}.default";
      description = "The promptdeploy package from the same pinned flake revision.";
    };

    source = lib.mkOption {
      type = lib.types.path;
      default = self.outPath;
      defaultText = lib.literalExpression "self.outPath";
      description = "Immutable promptdeploy source from the same pinned flake revision.";
    };

    expectedRevision = lib.mkOption {
      type = lib.types.str;
      default = defaultRevision;
      defaultText = lib.literalExpression "self.rev";
      description = "The full Git revision expected in both package and source.";
    };

    targets = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
      description = ''
        Optional local target IDs or labels used to narrow activation.
        Empty means all targets owned by the current host. Remote targets
        are always excluded by --local-only.
      '';
    };

    exactItems = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [
        "mcp:anvil"
        "mcp:anvil-tools"
        "skill:anvil"
      ];
      description = "Exact deployed items that must pass strict verification.";
    };

    stateDir = lib.mkOption {
      type = lib.types.str;
      default = "${config.xdg.stateHome}/promptdeploy";
      defaultText = lib.literalExpression ''config.xdg.stateHome + "/promptdeploy"'';
      description = "Private state directory containing the activation lock and bounded log; when shared over NFS, the lock serializes activations across hosts.";
    };

    lockWaitSeconds = lib.mkOption {
      type = lib.types.int;
      default = 60;
      description = "Maximum seconds to wait for the shared activation lock.";
    };

    transactionTimeoutSeconds = lib.mkOption {
      type = lib.types.int;
      default = 300;
      description = "Maximum seconds for the combined deploy and verify transaction.";
    };

    logMaxBytes = lib.mkOption {
      type = lib.types.int;
      default = 1048576;
      description = "Maximum bytes retained in the private activation log.";
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = builtins.match "[0-9a-f]{40}" cfg.expectedRevision != null;
        message = "programs.promptdeploy.expectedRevision must be a full lowercase Git SHA";
      }
      {
        assertion = lib.hasPrefix "/nix/store/" sourceString;
        message = "programs.promptdeploy.source must be an immutable Nix store path";
      }
      {
        assertion = builtins.pathExists "${sourceString}/deploy.yaml";
        message = "programs.promptdeploy.source must contain deploy.yaml";
      }
      {
        assertion = packageSource != null;
        message = "programs.promptdeploy.package must expose passthru.promptdeploySource";
      }
      {
        assertion = packageSource != null && toString packageSource == sourceString;
        message = "programs.promptdeploy.package and source must come from the same flake source";
      }
      {
        assertion = packageRevision != null;
        message = "programs.promptdeploy.package must expose passthru.promptdeployRevision";
      }
      {
        assertion = packageRevision != null && packageRevision == cfg.expectedRevision;
        message = "programs.promptdeploy package revision does not match expectedRevision";
      }
      {
        assertion = cfg.exactItems != [ ] && lib.all validItem cfg.exactItems;
        message = "programs.promptdeploy.exactItems must contain canonical TYPE:NAME selectors";
      }
      {
        assertion = lib.all validTarget cfg.targets;
        message = "programs.promptdeploy.targets must contain canonical target IDs or labels";
      }
      {
        assertion = lib.hasPrefix "/" cfg.stateDir && builtins.match ".*[\n\r].*" cfg.stateDir == null;
        message = "programs.promptdeploy.stateDir must be an absolute single-line path";
      }
      {
        assertion = cfg.lockWaitSeconds > 0;
        message = "programs.promptdeploy.lockWaitSeconds must be positive";
      }
      {
        assertion = cfg.transactionTimeoutSeconds > 0;
        message = "programs.promptdeploy.transactionTimeoutSeconds must be positive";
      }
      {
        assertion = cfg.logMaxBytes > 0;
        message = "programs.promptdeploy.logMaxBytes must be positive";
      }
    ];

    home.activation.promptdeploy = lib.hm.dag.entryAfter [ "writeBoundary" ] ''
      ${driver}/bin/promptdeploy-home-activation
    '';
  };
}
