# `make deploy-smoke` (Phase 5 branch 6, brief D10): a throwaway "VPS" --
# sshd with the dedicated deploy user whose only key runs the REAL
# friday-deploy.sh as a forced command, a git origin with good/bad/side
# commits, and a dummy compose app (a `docker compose` stub that stages and
# serves a static site with busybox-extras httpd). Build context: deploy/remote.
FROM alpine:3.20

RUN apk add --no-cache openssh-server bash git busybox-extras procps \
    && ssh-keygen -A \
    && adduser -D -s /bin/bash deploy \
    && sed -i 's/^deploy:!/deploy:*/' /etc/shadow \
    && printf '%s\n' \
        'PasswordAuthentication no' \
        'KbdInteractiveAuthentication no' \
        'PermitRootLogin no' \
        'PermitUserEnvironment no' \
        'AllowUsers deploy' \
        >> /etc/ssh/sshd_config

COPY friday-deploy.sh /opt/friday-deploy/friday-deploy.sh
COPY smoke/docker-stub.sh /usr/local/bin/docker
COPY smoke/setup.sh smoke/entrypoint.sh /usr/local/sbin/
RUN chown root:root /opt/friday-deploy/friday-deploy.sh /usr/local/bin/docker \
    && chmod 0755 /opt/friday-deploy /opt/friday-deploy/friday-deploy.sh /usr/local/bin/docker \
        /usr/local/sbin/setup.sh /usr/local/sbin/entrypoint.sh \
    && /usr/local/sbin/setup.sh

EXPOSE 22 8080
ENTRYPOINT ["/usr/local/sbin/entrypoint.sh"]
