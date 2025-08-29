ARG version=3.11
ARG tag=${version}-alpine3.17

FROM python:${tag} as builder
WORKDIR /app
ENV CARGO_NET_GIT_FETCH_WITH_CLI=true

RUN apk add --update \
        cargo \
        git \
        gcc \
        g++ \
        jpeg-dev \
        libc-dev \
        linux-headers \
        musl-dev \
        patchelf \
        rust \
        zlib-dev

RUN pip install -U pip wheel setuptools maturin
COPY requirements.txt .
RUN pip install -r requirements.txt --no-build-isolation


FROM python:${tag}
WORKDIR /app

ARG version

# Add edge repository for latest FFmpeg
RUN echo "http://dl-cdn.alpinelinux.org/alpine/edge/main" >> /etc/apk/repositories && \
    echo "http://dl-cdn.alpinelinux.org/alpine/edge/community" >> /etc/apk/repositories && \
    echo "http://dl-cdn.alpinelinux.org/alpine/edge/testing" >> /etc/apk/repositories

COPY --from=builder \
        /usr/local/lib/python${version}/site-packages \
        /usr/local/lib/python${version}/site-packages

RUN apk add --update \
    ffmpeg \
    ffmpeg-dev \
    netcat-openbsd \
    libusb-dev \
    git \
    && rm -rf /var/cache/apk/*

COPY . .
RUN pip install . --no-cache-dir

COPY ./docker/entrypoint.sh /

ENTRYPOINT ["/entrypoint.sh"]
CMD ["unifi-cam-proxy"]
