FROM python:3.15-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md README.en.md README.zh-CN.md LICENSE ./
COPY requirements.lock ./
COPY src ./src
RUN pip install --no-cache-dir --require-hashes -r requirements.lock \
    && pip install --no-cache-dir --no-deps . && useradd --uid 10001 --create-home app \
    && mkdir /app/data && chown app:app /app/data
USER app
EXPOSE 8000
CMD ["qq-mail-mcp", "serve"]
