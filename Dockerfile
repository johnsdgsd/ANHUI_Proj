FROM python:3.10-slim

WORKDIR /app

COPY requirements.txt .

# RUN pip install --no-cache-dir -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/

COPY . .

EXPOSE 5000

# CMD ["python", "-m", "backend.api.app"]
CMD ["tail", "-f", "/etc/hosts"]

python -m backend.api.app