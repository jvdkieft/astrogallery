# Builder for boris: watches the Telescopes share (same layout as scopessd) and rebuilds site/.
# Built by deploy/docker-compose.yml from the copy of this repo that deploy.sh puts in $APPDATA/app.
FROM python:3.12-slim
RUN pip install --no-cache-dir pillow
WORKDIR /app
COPY gallery.py minyaml.py watch.py ./
COPY static/ static/
ENV PYTHONUNBUFFERED=1 \
    GALLERY_SRC=/data \
    GALLERY_OUT=/site \
    GALLERY_JOBS=2
CMD ["python3", "watch.py"]
