# MLflow tracking server: Postgres backend store, artifacts proxied to S3, basic auth.
# The official image (ghcr.io/mlflow/mlflow) installs only mlflow, without the Postgres driver and boto3.
# SQLAlchemy 2.1 maps postgresql:// to psycopg 3, so the driver is psycopg, not psycopg2.
FROM python:3.13-slim-trixie

ARG MLFLOW_VERSION=3.18.0
RUN pip install --no-cache-dir \
        "mlflow[auth]==${MLFLOW_VERSION}" \
        "psycopg[binary]==3.3.9" \
        boto3==1.44.4

COPY basic_auth.ini /etc/mlflow/basic_auth.ini
ENV MLFLOW_AUTH_CONFIG_PATH=/etc/mlflow/basic_auth.ini

RUN useradd --system --no-create-home --shell /usr/sbin/nologin mlflow
USER mlflow
WORKDIR /tmp

EXPOSE 5000
