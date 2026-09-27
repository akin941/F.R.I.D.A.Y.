#!/bin/sh
# The smoke's dummy "compose app": `compose build` stages the checkout's
# www/ into .build (the live site is untouched), `compose up -d` swaps the
# staged build live and (re)starts busybox-extras httpd on :8080. Anything else is
# refused. Run by jarvis-deploy.sh inside APP_DIR as the deploy user.
set -eu
case "$*" in
"compose build")
    rm -rf .build
    cp -R www .build
    ;;
"compose up -d")
    rm -rf /srv/live/www
    cp -R .build /srv/live/www
    pkill -f 'busybox-extras httpd' 2>/dev/null || true
    sleep 0.2
    busybox-extras httpd -p 0.0.0.0:8080 -h /srv/live/www
    ;;
*)
    echo "docker stub: unsupported command: $*" >&2
    exit 1
    ;;
esac
