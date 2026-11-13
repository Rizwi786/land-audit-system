FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt gunicorn
COPY app.py plot_extractor.py local_data.py ./
COPY static ./static
# Render (and most hosts) provide $PORT at runtime; 10000 is the fallback
# Set at deploy time:  APP_PASSWORD (demo login)   LAND_AUDIT_CONTACT (your email, for OSM)
CMD gunicorn -w 2 -b 0.0.0.0:${PORT:-10000} --timeout 120 app:app
