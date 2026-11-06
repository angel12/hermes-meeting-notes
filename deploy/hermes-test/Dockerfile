ARG HERMES_BASE_IMAGE
FROM ${HERMES_BASE_IMAGE}
COPY idle-callback-compat.js /opt/hermes/apps/desktop/dist-webapp/assets/idle-callback-compat-v1.js
COPY install-compat.py /tmp/install-meeting-test-compat.py
RUN python3 /tmp/install-meeting-test-compat.py && rm /tmp/install-meeting-test-compat.py
COPY provider-requests.patch install-provider-requests.py /tmp/
RUN python3 /tmp/install-provider-requests.py && rm /tmp/install-provider-requests.py /tmp/provider-requests.patch
