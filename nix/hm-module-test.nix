{
  pkgs,
  homeManager,
}:

let
  inherit (pkgs) lib;
  revision = "0123456789abcdef0123456789abcdef01234567";
  wrongRevision = "89abcdef0123456789abcdef0123456789abcdef";
  fixtureSource = pkgs.runCommand "promptdeploy-module-source" { } ''
    mkdir -p "$out/.promptdeploy"
    touch "$out/deploy.yaml"
    printf '%s\n' '{"schema":1,"bindings":{}}' >"$out/.promptdeploy/bundle-bindings.json"
  '';
  otherSource = pkgs.runCommand "promptdeploy-other-source" { } ''
    mkdir -p "$out/.promptdeploy"
    touch "$out/deploy.yaml"
    printf '%s\n' '{"schema":1,"bindings":{}}' >"$out/.promptdeploy/bundle-bindings.json"
  '';
  packageBase = pkgs.writeShellApplication {
    name = "promptdeploy";
    text = "exit 0";
  };
  package = packageBase.overrideAttrs (old: {
    passthru = (old.passthru or { }) // {
      promptdeployDeployment = fixtureSource;
      promptdeployRevision = revision;
    };
  });
  wrongSourcePackage = packageBase.overrideAttrs (old: {
    passthru = (old.passthru or { }) // {
      promptdeployDeployment = otherSource;
      promptdeployRevision = revision;
    };
  });
  fakeSelf = {
    outPath = fixtureSource;
    rev = revision;
    packages.${pkgs.stdenv.hostPlatform.system} = {
      default = package;
      deployment = fixtureSource;
    };
  };
  module = import ./hm-module.nix { self = fakeSelf; };
  mkConfiguration =
    overrides:
    homeManager.lib.homeManagerConfiguration {
      inherit pkgs;
      modules = [
        module
        {
          home = {
            username = "promptdeploy-test";
            homeDirectory = "/tmp/promptdeploy-test-home";
            stateVersion = "24.11";
          };
          programs.promptdeploy = {
            enable = true;
            stateDir = "/tmp/promptdeploy-module-state";
          }
          // overrides;
        }
      ];
    };
  allAssertionsPass = configuration: lib.all (entry: entry.assertion) configuration.config.assertions;
  configurationSucceeds =
    overrides: (builtins.tryEval ((mkConfiguration overrides).activationPackage.drvPath)).success;

  valid = mkConfiguration { };
  validConfig = valid.config.programs.promptdeploy;
  activation = valid.config.home.activation.promptdeploy.data;
in
assert validConfig.package == package;
assert toString validConfig.source == toString fixtureSource;
assert validConfig.expectedRevision == revision;
assert validConfig.targets == [ ];
assert
  validConfig.exactItems == [
    "mcp:anvil"
    "mcp:anvil-tools"
    "skill:anvil"
  ];
assert allAssertionsPass valid;
assert !(valid.options.programs.promptdeploy ? sourceDir);
assert lib.hasInfix "/bin/promptdeploy-home-activation" activation;
assert !lib.hasInfix "--target local" activation;
assert !lib.hasInfix "/Users/" activation;
assert
  !(configurationSucceeds {
    expectedRevision = wrongRevision;
  });
assert
  !(configurationSucceeds {
    package = wrongSourcePackage;
  });
assert
  !(configurationSucceeds {
    source = otherSource;
  });
assert
  !(configurationSucceeds {
    exactItems = [ ];
  });
assert
  !(configurationSucceeds {
    exactItems = [ "bad selector" ];
  });
assert
  !(configurationSucceeds {
    targets = [ "bad target" ];
  });
assert
  !(configurationSucceeds {
    stateDir = "relative/path";
  });
assert
  !(configurationSucceeds {
    lockWaitSeconds = 0;
  });
assert
  !(configurationSucceeds {
    transactionTimeoutSeconds = 0;
  });
assert
  !(configurationSucceeds {
    logMaxBytes = 0;
  });
pkgs.runCommand "promptdeploy-hm-module-test" { } ''
  touch "$out"
''
