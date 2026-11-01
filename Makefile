.PHONY: test eval bench demo clean

test:
	python -m unittest discover -s tests -p 'test_*.py' -v

eval:
	python -m eval.replay_30d

bench:
	python -m eval.bench --n 500

demo:
	python examples/companion_demo.py

local-demo:
	python examples/local_embedding.py

local-eval:
	HIPPO_EMBED_BASE=http://127.0.0.1:11434/v1 HIPPO_EMBED_KEY=ollama HIPPO_EMBED_MODEL=bge-m3 \
		python -m eval.replay_30d --embedder openai

clean:
	rm -rf __pycache__ */__pycache__ */*/__pycache__ .pytest_cache
