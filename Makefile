.PHONY: test regen selftest
test:
	pytest -q -p no:cacheprovider

regen:
	python3 pipeline/regen.py

selftest:
	python3 pipeline/selftest.py
