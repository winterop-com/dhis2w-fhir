FROM ghcr.io/fhir/ig-publisher-localdev
# The publisher jar is a few hundred megabytes from GitHub's release storage, and a slow or flaky link
# drops the transfer part way. `latest` is resolved once to the release it names, so every attempt
# reads the same file; each attempt resumes the partial file rather than starting over, gives up on a
# transfer stalled below 1 KB/s for a minute, and speaks HTTP/1.1, whose stream a proxy cannot reset
# the way it resets an HTTP/2 one. The release URL is resolved rather than the storage URL behind it,
# because that one is signed for an hour and a slow download outlives it. The build fails only when
# every attempt has.
RUN mkdir -p /home/publisher/.ig-publisher && \
    cd /home/publisher/.ig-publisher && \
    url=$(curl --fail --silent --show-error --http1.1 --head --output /dev/null --write-out '%{redirect_url}' \
        "https://github.com/HL7/fhir-ig-publisher/releases/latest/download/publisher.jar") && \
    test -n "$url" && \
    downloaded=0 && \
    for attempt in 1 2 3 4 5 6 7 8; do \
        if curl --fail --location --http1.1 --continue-at - --speed-limit 1024 --speed-time 60 \
            --output publisher.jar "$url"; then downloaded=1; break; fi; \
        echo "publisher.jar: attempt $attempt stopped, resuming in 10 seconds"; \
        sleep 10; \
    done && \
    test "$downloaded" = 1
RUN npm install -g fsh-sushi
WORKDIR /home/publisher/ig
