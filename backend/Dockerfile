# syntax=docker/dockerfile:1
FROM python:3.12-slim

# pdfplumber needs no system packages, but matplotlib and reportlab want the
# standard font and image libraries present.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libfreetype6 libjpeg62-turbo libpng16-16 \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    MPLBACKEND=Agg

WORKDIR /app

# Dependencies first, so editing application code does not reinstall them.
COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install .

COPY app ./app

# Run as a non-root user. This container reads financial statements; there is no
# reason for it to have root inside its own namespace.
RUN useradd --create-home --uid 10001 wimmg \
    && mkdir -p /data && chown -R wimmg:wimmg /data /app
USER wimmg

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
