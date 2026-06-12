.PHONY: all help dry-run deploy force

# A bare `make` must never perform a live deploy.
all: help

help:
	@echo "Usage: make <target>"
	@echo ""
	@echo "  dry-run  Preview what would be deployed (no changes made)"
	@echo "  deploy   Deploy prompts to all configured targets"
	@echo "  force    Deploy, rewriting items even if unchanged"

dry-run:
	nix develop --command bash -c "PYTHONPATH=src python -m promptdeploy deploy --dry-run"

deploy:
	nix develop --command bash -c "PYTHONPATH=src python -m promptdeploy deploy"

force:
	nix develop --command bash -c "PYTHONPATH=src python -m promptdeploy deploy --force"
