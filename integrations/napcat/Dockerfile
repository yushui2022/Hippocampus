FROM python:3.12-slim

WORKDIR /app
RUN pip install --no-cache-dir aiohttp

ENV PYTHONPATH=/opt/hippocampus:/opt/hippocampus/integrations/napcat
ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "hippocampus_napcat"]
