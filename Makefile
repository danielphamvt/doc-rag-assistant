.PHONY: help setup ingest run test clean

help: ## Hiển thị trợ giúp các lệnh
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

setup: ## Cài đặt dependencies từ requirements.txt
	pip install -r requirements.txt
	pip install -r requirements-dev.txt

ingest: ## Chuyển đổi docs/ thành Markdown và nạp vào Qdrant (chạy lần đầu hoặc khi thay đổi luật)
	python db/document_processor.py

run: ## Khởi chạy web chatbot tại http://127.0.0.1:7860
	python app.py

test: ## Chạy bộ test (không cần LLM)
	pytest tests/ -v

clean: ## Dọn cache Python và pytest
	rm -rf __pycache__ .pytest_cache tests/__pycache__ rag_agent/__pycache__ db/__pycache__ ui/__pycache__
