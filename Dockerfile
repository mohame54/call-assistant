FROM python:3.10-slim AS builder


WORKDIR /usr/src/app


COPY requirements.txt  ./


RUN pip install --no-cache-dir -r requirements.txt   && pip check

    
# CLEAN UP UNUSED DEPENDENCIES
FROM python:3.10-slim AS cleaner


WORKDIR /usr/src/app


COPY --from=builder /usr/local/lib/python3.10/site-packages /usr/local/lib/python3.10/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin


RUN apt-get clean && rm -rf /var/lib/apt/lists/* && \
    find /usr/local/lib/python3.10/site-packages -name "*.pyc" -delete && \
    find /usr/local/lib/python3.10/site-packages -name "__pycache__" -delete


# FINAL RUNTIME STAGE
FROM  python:3.10-slim AS runtime


WORKDIR /usr/src/app


# Copy cleaned environment
COPY --from=cleaner /usr/local/lib/python3.10/site-packages /usr/local/lib/python3.10/site-packages
COPY --from=cleaner /usr/local/bin /usr/local/bin


# Copy application code
COPY . .

CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}
