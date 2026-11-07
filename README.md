# Только оригинальные (OEM) запчасти — поиск по артикулу

Веб-приложение на Streamlit.  
**Фокус:** только оригинальные запчасти (OEM), без аналогов.

---

## 1. Локальный запуск (на своём компьютере)

```bash
cd parts_price_app
pip install -r requirements.txt
streamlit run app.py
```

Откроется в браузере: http://localhost:8501

---

## 2. Запуск на сервере (VPS / выделенный сервер)

### Быстрый способ

```bash
# 1. Скопируйте папку parts_price_app на сервер
# 2. Установите зависимости
cd parts_price_app
pip install -r requirements.txt

# 3. Запустите с доступом извне
streamlit run app.py \
  --server.port 8501 \
  --server.address 0.0.0.0 \
  --server.headless true
```

После этого приложение будет доступно по адресу:  
`http://IP-вашего-сервера:8501`

**Важно:** откройте порт 8501 в firewall:

```bash
# Ubuntu / Debian
sudo ufw allow 8501
```

---

### Рекомендуемый способ (systemd + nginx)

#### Шаг 1. Создайте сервис

```bash
sudo nano /etc/systemd/system/parts-app.service
```

Вставьте:

```ini
[Unit]
Description=OEM Parts Price Search (Streamlit)
After=network.target

[Service]
User=www-data
WorkingDirectory=/path/to/parts_price_app
ExecStart=/usr/bin/streamlit run app.py \
  --server.port 8501 \
  --server.address 127.0.0.1 \
  --server.headless true \
  --browser.gatherUsageStats false
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Замените `/path/to/parts_price_app` на реальный путь.

```bash
sudo systemctl daemon-reload
sudo systemctl enable parts-app
sudo systemctl start parts-app
sudo systemctl status parts-app
```

#### Шаг 2. Nginx как reverse proxy (рекомендуется)

```bash
sudo nano /etc/nginx/sites-available/parts-app
```

```nginx
server {
    listen 80;
    server_name your-domain.ru;   # или IP

    location / {
        proxy_pass http://127.0.0.1:8501;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 86400;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/parts-app /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

После этого сайт будет доступен по домену или IP без указания порта.

---

## 3. Запуск через Docker (удобно)

В папке уже есть `Dockerfile`.

```bash
cd parts_price_app

# Собрать образ
docker build -t parts-oem-app .

# Запустить
docker run -d \
  --name parts-app \
  -p 8501:8501 \
  --restart unless-stopped \
  parts-oem-app
```

Приложение будет доступно на `http://IP:8501`.

Остановить / удалить:

```bash
docker stop parts-app
docker rm parts-app
```

---

## 4. Бесплатный хостинг (самый простой)

### Streamlit Community Cloud

1. Загрузите код на GitHub (создайте репозиторий).
2. Зайдите на https://share.streamlit.io
3. Подключите GitHub-репозиторий.
4. Укажите файл `app.py`.
5. Нажмите Deploy.

Получите постоянную ссылку вида `https://ваш-ник-parts-app.streamlit.app`.

---

## Файлы проекта

| Файл              | Назначение                     |
|-------------------|--------------------------------|
| `app.py`          | Само приложение                |
| `requirements.txt`| Зависимости Python             |
| `Dockerfile`      | Для запуска в Docker           |
| `README.md`       | Эта инструкция                 |

---

## Полезные команды

```bash
# Проверить, что приложение запущено
curl http://localhost:8501/_stcore/health

# Смотреть логи systemd
sudo journalctl -u parts-app -f

# Смотреть логи Docker
docker logs -f parts-app
```
