all:
	nix develop --command bash -c "PYTHONPATH=src python -m promptdeploy deploy"
