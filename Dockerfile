FROM python:3.9

RUN apt-get update && \
    apt-get install ffmpeg -y && \
    python -m pip install --upgrade pip && \
    mkdir /app

WORKDIR /app
COPY . /app
RUN pip install -r requirements.txt

CMD ["python", "/app"]
