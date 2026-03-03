{
  description =
    "Deploy prompts, agents, skills, and MCP servers to multiple AI coding tools";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachDefaultSystem (system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        python = pkgs.python312;

        promptdeploy = python.pkgs.buildPythonApplication {
          pname = "promptdeploy";
          version = "0.1.0";
          pyproject = true;

          src = ./.;

          build-system = [
            python.pkgs.setuptools
            python.pkgs.wheel
          ];

          dependencies = with python.pkgs; [
            pyyaml
          ];

          doCheck = false;

          meta = {
            description =
              "Deploy prompts, agents, skills, and MCP servers to multiple AI coding tools";
            mainProgram = "promptdeploy";
          };
        };
      in {
        packages.default = promptdeploy;

        devShells.default = pkgs.mkShell {
          packages = [
            (python.withPackages (ps:
              with ps; [
                pyyaml
                pytest
                pytest-cov
                mypy
              ]))
            pkgs.ruff
          ];
        };
      });
}
