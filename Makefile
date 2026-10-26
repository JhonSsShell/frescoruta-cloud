.PHONY: install test lint format run demo up down

install:      ## Instala dependencias de desarrollo
	pip install -r requirements-dev.txt

test:         ## Ejecuta las pruebas automatizadas
	pytest

lint:         ## Revisa estilo y errores comunes
	ruff check . && ruff format --check .

format:       ## Formatea el código
	ruff format . && ruff check . --fix

run:          ## Levanta los 5 servicios y Redis sin Docker
	./scripts/run_local.sh

demo:         ## Ejecuta el guion de demostración contra el gateway local
	python scripts/demo.py

up:           ## Levanta todo con Docker Compose
	docker compose up --build -d

down:         ## Detiene Docker Compose y borra volúmenes
	docker compose down -v
