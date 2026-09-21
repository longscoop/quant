FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/pip \
    python -m pip install "pip<24.1" \
    && python -c "import tomllib; dependencies = tomllib.load(open('pyproject.toml', 'rb'))['project']['dependencies']; open('/tmp/requirements.txt', 'w').write('\\n'.join(dependencies))" \
    && pip install --prefer-binary -r /tmp/requirements.txt
COPY . .
RUN pip install --no-cache-dir --no-deps .
CMD ["streamlit", "run", "streamlit_app.py", "--server.address=0.0.0.0"]
