FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
# traçabilité des mises à jour : sha + date injectés par la CI, affichés
# dans Paramètres → Système (routers/system.py)
ARG GIT_SHA=dev
ARG BUILD_TIME=
ENV MATHPRINT_BUILD_SHA=$GIT_SHA \
    MATHPRINT_BUILD_TIME=$BUILD_TIME \
    MATHPRINT_DATA_DIR=/data
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
