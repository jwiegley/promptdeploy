{
  description = "Deploy prompts, agents, skills, and MCP servers to multiple AI coding tools";

  inputs = {
    # This repository has one tracked skill backed by a Git submodule.
    # Include it in self.outPath so the composed deployment is complete.
    self.submodules = true;
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    flake-utils.url = "github:numtide/flake-utils";
    home-manager = {
      url = "github:nix-community/home-manager/9a40ec3b78fc688d0908485887d355caa5666d18";
      inputs.nixpkgs.follows = "nixpkgs";
    };
    ponytail = {
      url = "github:DietrichGebert/ponytail/16f29800fd2681bdf24f3eb4ccffe38be3baec6b";
      flake = false;
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      flake-utils,
      home-manager,
      ponytail,
    }:
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        python = pkgs.python312;

        pythonWithDeps = python.withPackages (
          ps: with ps; [
            pyyaml
            jinja2
            ruamel-yaml
            pytest
            pytest-cov
            mypy
            types-pyyaml
          ]
        );

        src = self.outPath;
        revision = self.rev or null;
        ponytailVersion =
          (builtins.fromJSON (builtins.readFile "${ponytail}/package.json")).version;
        # External projects enter the deployment through one explicit mapping.
        # Their adapter manifests remain small, reviewed files in this repo.
        deploymentSources = {
          ponytail = {
            source = ponytail;
            destination = "sources/ponytail";
            revision = ponytail.rev;
            narHash = ponytail.narHash;
            version = ponytailVersion;
          };
        };
        deploymentBindings = builtins.toJSON {
          schema = 1;
          bindings = pkgs.lib.mapAttrs (
            _name: mapping: {
              path = "@deployment@/${mapping.destination}";
              inherit (mapping) revision narHash version;
              mutable = false;
            }
          ) deploymentSources;
        };
        copyDeploymentSources = pkgs.lib.concatStringsSep "\n" (
          pkgs.lib.mapAttrsToList (
            _name: mapping: ''
              destination=${pkgs.lib.escapeShellArg mapping.destination}
              mkdir -p "$out/$destination"
              cp -R ${mapping.source}/. "$out/$destination/"
            ''
          ) deploymentSources
        );
        deployment = pkgs.runCommand "promptdeploy-deployment" { } ''
          mkdir -p "$out"
          cp -R ${src}/. "$out/"
          ${copyDeploymentSources}
          mkdir -p "$out/.promptdeploy"
          printf '%s\n' ${pkgs.lib.escapeShellArg deploymentBindings} \
            >"$out/.promptdeploy/bundle-bindings.json"
          substituteInPlace "$out/.promptdeploy/bundle-bindings.json" \
            --replace-fail '@deployment@' "$out"
        '';

        promptdeploy = python.pkgs.buildPythonApplication {
          pname = "promptdeploy";
          version = "0.1.0";
          pyproject = true;

          inherit src;

          build-system = [
            python.pkgs.setuptools
            python.pkgs.wheel
          ];

          dependencies = with python.pkgs; [
            pyyaml
            jinja2
            ruamel-yaml
          ];

          doCheck = false;

          passthru = {
            promptdeployCodeSource = src;
            promptdeployDeployment = deployment;
            # Compatibility alias for the Home Manager module's former name.
            promptdeploySource = deployment;
            promptdeployRevision = revision;
          };

          # Remote deployment shells out to rsync/ssh (src/promptdeploy/ssh.py),
          # which would otherwise resolve from the ambient PATH. On macOS that
          # is openrsync, whose filter/--delete semantics differ from GNU rsync
          # and would break the filters that confine the push's delete blast
          # radius. Prefix PATH so the installed binary always uses GNU rsync
          # and OpenSSH from nixpkgs.
          makeWrapperArgs = [
            "--prefix"
            "PATH"
            ":"
            "${pkgs.lib.makeBinPath [
              pkgs.rsync
              pkgs.openssh
            ]}"
          ];

          meta = {
            description = "Deploy prompts, agents, skills, and MCP servers to multiple AI coding tools";
            mainProgram = "promptdeploy";
          };
        };

        deploymentPromptdeploy = pkgs.writeShellScriptBin "promptdeploy" ''
          exec ${pkgs.lib.getExe promptdeploy} \
            --config ${deployment}/deploy.yaml \
            --bundle-bindings-file ${deployment}/.promptdeploy/bundle-bindings.json \
            --require-immutable-bundles \
            "$@"
        '';
        promptdeployDeploy = pkgs.writeShellScriptBin "promptdeploy-deploy" ''
          exec ${deploymentPromptdeploy}/bin/promptdeploy deploy "$@"
        '';
      in
      {
        packages = {
          default = promptdeploy;
          inherit promptdeploy deployment;
        };

        apps = {
          default = {
            type = "app";
            program = "${promptdeployDeploy}/bin/promptdeploy-deploy";
            meta.description = "Deploy from the composed immutable store deployment";
          };
          promptdeploy = {
            type = "app";
            program = "${deploymentPromptdeploy}/bin/promptdeploy";
            meta.description = "Run the promptdeploy CLI against the composed store deployment";
          };
          raw = {
            type = "app";
            program = pkgs.lib.getExe promptdeploy;
            meta.description = "Run the raw promptdeploy CLI against an explicit mutable source";
          };
        };

        checks = {
          ruff-format = pkgs.runCommand "ruff-format" { nativeBuildInputs = [ pkgs.ruff ]; } ''
            ruff format --no-cache --check ${src}
            touch $out
          '';

          ruff-lint = pkgs.runCommand "ruff-lint" { nativeBuildInputs = [ pkgs.ruff ]; } ''
            ruff check --no-cache ${src}
            touch $out
          '';

          mypy = pkgs.runCommand "mypy" { nativeBuildInputs = [ pythonWithDeps ]; } ''
            cp -r ${src} $TMPDIR/src && chmod -R u+w $TMPDIR/src
            cd $TMPDIR/src
            PYTHONPATH=src mypy src/ tests/
            touch $out
          '';

          pytest =
            pkgs.runCommand "pytest"
              {
                PONYTAIL_TEST_SOURCE = ponytail;
                nativeBuildInputs = [
                  pythonWithDeps
                  pkgs.nodejs
                  pkgs.powershell
                  pkgs.rsync
                  pkgs.openssh
                ];
              }
              ''
                cp -r ${src} $TMPDIR/src && chmod -R u+w $TMPDIR/src
                cd $TMPDIR/src
                PYTHONPATH=src python -m pytest tests/ -x -q --cov --cov-report=term-missing
                touch $out
              '';

          hm-module = pkgs.callPackage ./nix/hm-module-test.nix {
            homeManager = home-manager;
          };

          hm-activation = pkgs.callPackage ./nix/hm-activation-test.nix { };

          deployment = pkgs.runCommand "promptdeploy-deployment-check" {
            nativeBuildInputs = [ pkgs.jq ];
          } ''
            export HOME="$TMPDIR/home"
            mkdir -p "$HOME"
            deployment_root=${deployment}
            ponytail_root="$deployment_root/sources/ponytail"
            test -f "$deployment_root/deploy.yaml"
            test -f "$deployment_root/bundles/ponytail.yaml"
            test -f "$deployment_root/skills/translate-en/SKILL.md"
            test -z "$(
              find "$deployment_root" -type l \
                ! -exec test -e '{}' ';' -print -quit
            )"
            test ! -L "$ponytail_root"
            test -f "$ponytail_root/hooks/ponytail-activate.js"
            for name in \
              ponytail ponytail-review ponytail-audit \
              ponytail-debt ponytail-gain ponytail-help; do
              test -f "$ponytail_root/skills/$name/SKILL.md"
            done
            jq -e \
              --arg path "$ponytail_root" \
              --arg revision ${pkgs.lib.escapeShellArg ponytail.rev} \
              --arg narHash ${pkgs.lib.escapeShellArg ponytail.narHash} \
              --arg version ${pkgs.lib.escapeShellArg ponytailVersion} \
              '.schema == 1 and .bindings.ponytail == {
                path: $path,
                revision: $revision,
                narHash: $narHash,
                version: $version,
                mutable: false
              }' \
              "$deployment_root/.promptdeploy/bundle-bindings.json"

            caller="$TMPDIR/caller"
            preview="$TMPDIR/preview"
            mkdir -p "$caller" "$preview"
            printf '%s\n' 'invalid: [caller config' >"$caller/deploy.yaml"
            cd "$caller"
            ${deploymentPromptdeploy}/bin/promptdeploy validate
            ${promptdeployDeploy}/bin/promptdeploy-deploy \
              --target claude-personal \
              --target-root "$preview" \
              --only-item bundle:ponytail \
              --only-item skill:ponytail
            ${deploymentPromptdeploy}/bin/promptdeploy verify \
              --target claude-personal \
              --target-root "$preview" \
              --only-item bundle:ponytail \
              --only-item skill:ponytail
            test -f "$preview/claude-personal/skills/ponytail/SKILL.md"
            touch $out
          '';

          build = promptdeploy;
        };

        devShells.default = pkgs.mkShell {
          packages = [
            pythonWithDeps
            pkgs.ruff
            pkgs.lefthook
            pkgs.nodejs
            # GNU rsync (not macOS openrsync) for remote deploys run from
            # the dev shell.
            pkgs.rsync
          ];
        };
      }
    )
    // {
      homeManagerModules.default = import ./nix/hm-module.nix { inherit self; };
    };
}
