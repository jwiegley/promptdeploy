all: deploy

deploy:
	nix develop --command bash -c "PYTHONPATH=src python -m promptdeploy deploy"

force:
	nix develop --command bash -c "PYTHONPATH=src python -m promptdeploy deploy --force"

commit:
	nix develop --command bash -c 'git commit -m "Update list of models"'
