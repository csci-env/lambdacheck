build:
	python -m build

install:
	pip install --force-reinstall ./dist/*.whl
