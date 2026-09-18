.PHONY: demo ui api test evaluate data

demo:
	python demo.py "华东为什么下降？"

ui:
	streamlit run frontend/streamlit_app.py

api:
	uvicorn api.main:app --reload

test:
	pytest

evaluate:
	python -m evaluation.run --mode local --benchmark benchmark/benchmark_120.json

data:
	python scripts/generate_demo_data.py
	python scripts/generate_benchmark.py
