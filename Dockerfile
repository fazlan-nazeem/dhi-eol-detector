FROM dhi.io/python:3-alpine3.23-dev

# Install Docker CLI (needed to inspect images via the mounted socket)
RUN apk add --no-cache docker-cli

WORKDIR /app

COPY pyproject.toml dhi_eol_detector.py ./

RUN pip install --no-cache-dir .

ENTRYPOINT ["dhi-eol-detector"]
