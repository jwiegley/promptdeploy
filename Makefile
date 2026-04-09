all: deploy

deploy:
	nix develop --command bash -c "PYTHONPATH=src python -m promptdeploy deploy"

force:
	nix develop --command bash -c "PYTHONPATH=src python -m promptdeploy deploy --force"
