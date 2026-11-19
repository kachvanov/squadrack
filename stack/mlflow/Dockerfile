# MLflow tracking server: Postgres backend store, artifacts proxied to S3, basic auth.
# The official image (ghcr.io/mlflow/mlflow) installs only mlflow, without the Postgres driver and boto3.
# SQLAlchemy 2.1 maps postgresql:// to psycopg 3, so the driver is psycopg, not psycopg2.
FROM python:3.13-slim-trixie

ARG MLFLOW_VERSION=3.18.0
RUN pip install --no-cache-dir \
        "mlflow[auth]==${MLFLOW_VERSION}" \
        "psycopg[binary]==3.3.9" \
        boto3==1.44.4

# COPY keeps the mode the file has in the build context, and a clone under a default ACL makes it
# 0660, unreadable for the mlflow user; configparser then silently reads nothing. The directory is
# made first: COPY --chmod would apply 0644 to the directory it creates, too.
RUN install -d -m 0755 /etc/mlflow
COPY --chmod=0644 basic_auth.ini /etc/mlflow/basic_auth.ini
ENV MLFLOW_AUTH_CONFIG_PATH=/etc/mlflow/basic_auth.ini

RUN useradd --system --no-create-home --shell /usr/sbin/nologin mlflow
USER mlflow
WORKDIR /tmp

EXPOSE 5000
