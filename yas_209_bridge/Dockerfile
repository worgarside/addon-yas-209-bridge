ARG BUILD_FROM
FROM $BUILD_FROM

COPY bridge /bridge

# Install requirements for add-on
RUN \
  apk add --no-cache \
    python3 \
    py3-pip \
    py3-lxml \
    python3-dev \
    musl-dev \
    gcc \
    libffi-dev

WORKDIR /bridge

RUN pip install -r requirements.txt

# Copy data for add-on
COPY run.sh /
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
