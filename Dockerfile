FROM python:3.13-slim
WORKDIR /app
COPY . /app
RUN mkdir -p /app/data && useradd --system --uid 10001 arapp && chown -R arapp:arapp /app/data
USER arapp
EXPOSE 8088
HEALTHCHECK --interval=15s --timeout=3s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8088/api/health',timeout=2)"
CMD ["python", "server.py", "--host", "0.0.0.0", "--port", "8088"]
