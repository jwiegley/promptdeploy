{
  description = "Deploy prompts, agents, skills, and MCP servers to multiple AI coding tools";

  inputs = {
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
            promptdeploySource = src;
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

        promptdeployDeploy = pkgs.writeShellScriptBin "promptdeploy-deploy" ''
          exec ${pkgs.lib.getExe promptdeploy} deploy "$@"
        '';
      in
      {
        packages.default = promptdeploy;

        apps = {
          default = {
            type = "app";
            program = "${promptdeployDeploy}/bin/promptdeploy-deploy";
            meta.description = "Run promptdeploy deploy in the packaged Nix environment";
          };
          promptdeploy = {
            type = "app";
            program = pkgs.lib.getExe promptdeploy;
            meta.description = "Run the promptdeploy CLI in the packaged Nix environment";
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
