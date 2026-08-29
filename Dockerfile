# KururuCMS の本番イメージ。
#
# 2段構成にしている。ビルドに必要なもの（コンパイラなど）を
# 最終イメージへ持ち込まないため。
# 攻撃者が侵入したときに、その場でコードをビルドする道具を
# 渡さないという意味もある。

# --- 1段目: 依存パッケージを wheel にする -----------------------------------
FROM python:3.12-slim@sha256:09f7da3bc104798d0afb40bc08d23ab2da20a76130cec1f2ef170848f5d85217 AS builder

# .pyc を書かない / 出力をためこまない。
# 後者はログがリアルタイムで見えなくなるのを防ぐ。
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /wheels
COPY requirements.lock .
COPY plugin-requirements.lock .
COPY plugin_wheels /plugin-wheels
RUN pip wheel --only-binary=:all: --require-hashes \
    --wheel-dir /wheels -r requirements.lock
RUN if [ ! -s plugin-requirements.lock ]; then exit 0; fi \
 && pip wheel --no-deps --no-index --find-links=/plugin-wheels --require-hashes \
      --wheel-dir /wheels -r plugin-requirements.lock

# --- 2段目: 実行用 ----------------------------------------------------------
FROM python:3.12-slim@sha256:09f7da3bc104798d0afb40bc08d23ab2da20a76130cec1f2ef170848f5d85217

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=config.settings.production

# root で動かさない。
# コンテナが乗っ取られたとき、root だとホスト側への影響が桁違いに大きくなる。
RUN useradd --create-home --uid 10001 kururu

WORKDIR /app

COPY --from=builder /wheels /wheels
COPY requirements.lock .
COPY plugin-requirements.lock .
RUN pip install --require-hashes --no-index --find-links=/wheels -r requirements.lock \
 && if [ -s plugin-requirements.lock ]; then \
      pip install --no-deps --require-hashes --no-index --find-links=/wheels \
        -r plugin-requirements.lock; \
    fi \
 && rm -rf /wheels

COPY --chown=kururu:kururu . .
RUN rm -rf /app/plugin_wheels

# 静的ファイルとアップロードの置き場。
# nginx と共有するので、後で volume がここへマウントされる。
RUN mkdir -p /app/staticfiles /app/media \
 && chown -R kururu:kururu /app/staticfiles /app/media

USER kururu

EXPOSE 8000

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "3", \
     "--timeout", "60", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
