FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Режим браузера (JS-сайты): раскомментируйте две строки ниже и добавьте playwright в requirements.txt
# RUN pip install --no-cache-dir playwright && playwright install --with-deps chromium

COPY *.py ./

EXPOSE 8501

# curl в python:slim нет — проверяем здоровье средствами Python
HEALTHCHECK CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8501/_stcore/health').status==200 else 1)"

ENTRYPOINT ["streamlit", "run", "app.py", \
            "--server.port=8501", \
            "--server.address=0.0.0.0", \
            "--server.headless=true", \
            "--browser.gatherUsageStats=false"]
