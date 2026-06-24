FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt debugpy

COPY . .

EXPOSE 5001
EXPOSE 5678

CMD ["python", "-m", "debugpy", "--listen", "0.0.0.0:5678", "/usr/local/bin/hypercorn", "app:app", "--bind", "0.0.0.0:5001"]