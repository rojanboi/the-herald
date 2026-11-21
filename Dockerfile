FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY pyproject.toml ./
COPY alertsvc ./alertsvc
RUN pip install --no-cache-dir .
COPY config ./config
COPY samples ./samples
COPY loadtest ./loadtest
COPY ui ./ui
RUN useradd --create-home herald && mkdir -p /var/log/herald && chown herald /var/log/herald
USER herald
EXPOSE 8000
CMD ["uvicorn", "alertsvc.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
