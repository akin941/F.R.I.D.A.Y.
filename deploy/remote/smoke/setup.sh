#!/bin/sh
# Image-build-time setup for the deploy smoke "VPS" (see Dockerfile).
set -eu

# Root-owned config, exactly as the runbook installs it.
install -d -o root -g root -m 0755 /etc/friday-deploy /etc/friday-deploy/apps
cat > /etc/friday-deploy/apps/smoke-app.conf <<'EOF'
APP_DIR=/srv/app
TRACKED_BRANCH=main
BUILD_CMD="docker compose build"
RESTART_CMD="docker compose up -d"
HEALTH_CMD="wget -qO- http://127.0.0.1:8080/health | grep -qx ok"
EOF
chown root:root /etc/friday-deploy/apps/smoke-app.conf
chmod 0644 /etc/friday-deploy/apps/smoke-app.conf
install -d -o deploy -g deploy -m 0750 /var/lib/friday-deploy /srv/live

# The app's origin: A (good, initially live) -> B (good) -> C (health says
# "fail") on main, and D on a side branch that is never merged. The app
# checkout's origin URL is the remote Friday knows; git rewrites it to the
# local bare repo.
git config --system url./srv/git/app.git.insteadOf git@example.invalid:acme/smoke-app.git
work=$(mktemp -d)
git init -q -b main "$work"
cd "$work"
git config user.name smoke
git config user.email smoke@example.invalid
mkdir www
commit() {
    printf '%s\n' "$1" > www/health
    printf '%s\n' "$2" > www/version
    git add -A
    git commit -qm "$2"
    git rev-parse HEAD
}
A=$(commit ok A)
B=$(commit ok B)
C=$(commit fail C)
git checkout -q -b side "$B"
D=$(commit ok D)
git checkout -q main
install -d -o deploy -g deploy /srv/git
git clone -q --bare "$work" /srv/git/app.git
chown -R deploy:deploy /srv/git
cd /
rm -rf "$work"

install -d -o deploy -g deploy -m 0755 /srv/app
su deploy -s /bin/sh -c "git clone -q git@example.invalid:acme/smoke-app.git /srv/app && git -C /srv/app checkout -q --detach $A"
printf 'SMOKE_SHA_A=%s\nSMOKE_SHA_B=%s\nSMOKE_SHA_C=%s\nSMOKE_SHA_D=%s\n' "$A" "$B" "$C" "$D" > /srv/smoke-shas.env
