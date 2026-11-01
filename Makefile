.PHONY: test eval bench demo clean

test:
	python -m unittest discover -s tests -p 'test_*.py' -v

eval:
	python -m eval.replay_30d

bench:
	python -m eval.bench --n 500

demo:
	python examples/companion_demo.py

clean:
	rm -rf __pycache__ */__pycache__ */*/__pycache__ .pytest_cache
