FROM nvidia/cuda:12.6.3-cudnn-runtime-ubuntu24.04

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    CASTWELL_DATA_DIR=/data \
    CASTWELL_MODEL_CACHE=/models \
    PATH=/opt/castwell/bin:$PATH

RUN --mount=type=secret,id=build_ca,mode=0444 \
    if [ -f /run/secrets/build_ca ]; then \
      printf 'Acquire::https::CaInfo "/run/secrets/build_ca";\n' > /etc/apt/apt.conf.d/99-castwell-build-ca; \
    fi \
    && apt-get update \
    && apt-get install --no-install-recommends -y ffmpeg libgomp1 ca-certificates python3 python3-venv \
    && rm -rf /var/lib/apt/lists/* \
    && rm -f /etc/apt/apt.conf.d/99-castwell-build-ca \
    && groupadd --gid 10001 castwell \
    && useradd --uid 10001 --gid 10001 --create-home castwell \
    && mkdir -p /data /models \
    && chown 10001:10001 /data /models \
    && python3 -m venv /opt/castwell

WORKDIR /app
COPY pyproject.toml README.md ./
COPY castwell/ ./castwell/
RUN --mount=type=secret,id=build_ca,mode=0444 \
    if [ -f /run/secrets/build_ca ]; then export PIP_CERT=/run/secrets/build_ca; fi \
    && python -m pip install '.[transcription]' \
    && python -m pip uninstall -y onnxruntime \
    && python -m pip install --no-deps --force-reinstall onnxruntime-gpu==1.23.2 \
    && chmod -R a+rX /app

USER 10001:10001
VOLUME ["/data", "/models"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/jobs', timeout=5).close()"
ENTRYPOINT ["python", "-m", "castwell"]
CMD ["--host", "0.0.0.0", "--port", "8000"]
