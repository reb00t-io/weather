FROM python:3.13-slim

WORKDIR /app
COPY pyproject.toml VERSION ./
RUN pip install --no-cache-dir .

COPY src/ ./src/

ARG DEPLOY_DATE=unknown
ENV DEPLOY_DATE=$DEPLOY_DATE

RUN useradd --create-home appuser \
 && mkdir -p /home/appuser/.cache/weather /app/src/static/ai_images \
 && chown -R appuser:appuser /home/appuser/.cache /app/src/static/ai_images
USER appuser

CMD ["python", "src/main.py"]
