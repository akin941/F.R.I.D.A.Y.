#!/bin/sh
# Installs the smoke's generated public key behind the forced command,
# brings commit A live, then runs sshd in the foreground.
set -eu
: "${AUTHORIZED_KEY:?AUTHORIZED_KEY (the runner's public key) is required}"
install -d -o deploy -g deploy -m 0700 /home/deploy/.ssh
printf 'command="/opt/friday-deploy/friday-deploy.sh",restrict %s\n' "$AUTHORIZED_KEY" \
    > /home/deploy/.ssh/authorized_keys
chown deploy:deploy /home/deploy/.ssh/authorized_keys
chmod 0600 /home/deploy/.ssh/authorized_keys
su deploy -s /bin/sh -c 'cd /srv/app && docker compose build && docker compose up -d'
exec /usr/sbin/sshd -D -e
