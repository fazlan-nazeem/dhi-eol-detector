# DHI EOL Detector

A CLI tool that inspects a Docker image, verifies whether it is a **Docker Hardened Image (DHI)**, and reads its **End of Life** date directly from the image labels.

## Prerequisites

- **Docker** running locally

## Quick Start

### Build the image

```bash
docker build -t dhi-eol-detector .
```

### Run

```bash
docker run --rm \
  -v /var/run/docker.sock:/var/run/docker.sock \
  dhi-eol-detector <image-name>
```

> **Note:** The Docker socket mount (`-v /var/run/docker.sock:...`) is required so the tool can run `docker inspect` on images available on your host and `docker pull` if the image needs to be pulled.

### Examples

```bash
# Check if an image uses a DHI base and get its EOL dates
docker run --rm \
  -v /var/run/docker.sock:/var/run/docker.sock \
  dhi-eol-detector demonstrationorg/task-api
```
### Sample Output

```
🔍 DHI EOL Detector — analysing 'demonstrationorg/task-api'

Step 1: Inspecting image labels
  ✔ This image is based on a Docker Hardened Image! ✅
  ℹ com.docker.dhi.url: https://dhi.io/catalog/node
  ℹ com.docker.dhi.version: 24.13.1-alpine3.22
  ℹ DHI Repository: node
  ℹ DHI Version: 24.13.1-alpine3.22

Step 2: Checking End of Life status
  ℹ com.docker.dhi.date.end-of-life: 2028-04-30
  2 years, 2 months, 7 days remaining
```

## How It Works

1. **Inspect image labels** — pulls the image if not available locally, then reads labels via `docker inspect`
2. **Check for DHI labels** — looks for `com.docker.dhi.url` and `com.docker.dhi.version`. If present, the image is based on a Docker Hardened Image. If not, it stops and reports it is not a DHI.
3. **Check EOL status** — reads the `com.docker.dhi.date.end-of-life` label and reports whether the image is past EOL or how much time remains
