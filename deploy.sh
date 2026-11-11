#!/bin/bash
# Build, publish and redeploy car-labels on TOWER, from this repo (tower path:
# /mnt/user/appdata/claude-code/workspace/repos/car-labels/deploy.sh).
#
# The container comes from its Unraid template
# (/boot/config/plugins/dockerMan/templates-user/my-car-labels.xml), never from compose.
# The image goes to Docker Hub first, so tower never holds the only copy.
set -euo pipefail
cd "$(dirname "$0")"
IMG=louij2/car-labels
TAG=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' car_labels/__init__.py)
C=car-labels
PORT=8095
TPL=/boot/config/plugins/dockerMan/templates-user/my-$C.xml
RECREATE=/mnt/user/appdata/compose-to-templates/tpl-recreate.sh

docker build -t "$IMG:latest" -t "$IMG:$TAG" .
docker run --rm --entrypoint sh -v "$PWD/tests:/app/tests:ro" "$IMG:$TAG" \
  -c "pip install -q pytest >/dev/null 2>&1 && python -m pytest -q -p no:cacheprovider tests"
docker push "$IMG:$TAG"
docker push "$IMG:latest"

sed -i "s#<Repository>$IMG:[^<]*</Repository>#<Repository>$IMG:$TAG</Repository>#" "$TPL"
"$RECREATE" "$C"
for _ in $(seq 12); do
  curl -fs -o /dev/null "http://localhost:$PORT/healthz" && break
  sleep 5
done
if curl -fs -o /dev/null "http://localhost:$PORT/healthz"; then
  "$RECREATE" --commit "$C"
  echo "deployed $IMG:$TAG"
else
  echo "NOT answering on :$PORT. Roll back: docker rm -f $C; docker rename $C-old $C; docker start $C" >&2
  exit 1
fi
