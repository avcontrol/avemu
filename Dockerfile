# avemu - A/V Equipment Emulator
FROM python:3.12-slim AS build

ARG DEVICE_MODEL=mcintosh/mx160

# install build dependencies
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && apt-get purge -y --auto-remove \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# copy and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# copy application code
COPY avemu.py .

EXPOSE 4999

# use shell form to expand variable
CMD python avemu.py --model ${DEVICE_MODEL}
