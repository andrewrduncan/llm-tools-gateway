# Playwright's image ships Chromium + system deps, needed only for browse_page.
# For a slimmer build without the browser, swap to python:3.12-slim and set
# ENABLE_BROWSER=0.
FROM mcr.microsoft.com/playwright/python:v1.62.0-noble
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY gateway/ ./gateway/
EXPOSE 8000
CMD ["uvicorn","gateway.server:app","--host","0.0.0.0","--port","8000","--no-access-log"]
