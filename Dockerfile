FROM python:3.11.11-slim-bullseye AS app
LABEL maintainer="Genaro Costa <genaro@carvalhocosta.net>"

WORKDIR /app

ARG UID=1000
ARG GID=1000

RUN apt-get update \
  && apt-get install -y --no-install-recommends build-essential curl libpq-dev \
  && rm -rf /var/lib/apt/lists/* /usr/share/doc /usr/share/man \
  && apt-get clean \
  && groupadd -g "${GID}" python \
  && useradd --create-home --no-log-init -u "${UID}" -g "${GID}" python \
  && chown python:python -R /app

ENV TINI_VERSION=v0.19.0
ADD https://github.com/krallin/tini/releases/download/${TINI_VERSION}/tini /usr/bin/tini
RUN chmod +x /usr/bin/tini

USER python

ARG FLASK_DEBUG="false"
ENV FLASK_DEBUG="${FLASK_DEBUG}" \
    FLASK_APP="app.py" \
    FLASK_SKIP_DOTENV="true" \
    PYTHONUNBUFFERED="true" \
    PATH="${PATH}:/home/python/.local/bin" \
    USER="python"
    # PYTHONPATH="." \

COPY ./app/ /app/
RUN pip3 install --upgrade pip && pip3 install --no-warn-script-location --no-cache-dir --user -r requirements.txt

EXPOSE 8000

CMD ["gunicorn","-b", "0.0.0.0", "app:app"]

ENTRYPOINT ["/usr/bin/tini", "--"]
