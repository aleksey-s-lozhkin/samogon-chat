#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Usage:
  scripts/verify_backup_restore.sh \
    --source-container postgres \
    --database samogon \
    --media-dir /srv/data/samogon/media \
    --output-dir /srv/backups/samogon

The production database is read only. The dump is restored into a temporary
PostgreSQL container, verified, and left in output-dir together with the media
archive and SHA-256 checksums.
EOF
}

source_container=""
database=""
media_dir=""
output_dir=""
postgres_image=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --source-container) source_container=${2:-}; shift 2 ;;
        --database) database=${2:-}; shift 2 ;;
        --media-dir) media_dir=${2:-}; shift 2 ;;
        --output-dir) output_dir=${2:-}; shift 2 ;;
        --postgres-image) postgres_image=${2:-}; shift 2 ;;
        --help|-h) usage; exit 0 ;;
        *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
done

if [[ ! "$source_container" =~ ^[a-zA-Z0-9][a-zA-Z0-9_.-]*$ ]]; then
    echo "--source-container must be an explicit Docker container name" >&2
    exit 2
fi
if [[ ! "$database" =~ ^[a-zA-Z_][a-zA-Z0-9_-]*$ ]]; then
    echo "--database must be an explicit PostgreSQL database name" >&2
    exit 2
fi
if [[ "$media_dir" != /* || "$media_dir" == "/" || ! -d "$media_dir" ]]; then
    echo "--media-dir must be an existing absolute directory other than /" >&2
    exit 2
fi
if [[ "$output_dir" != /* || "$output_dir" == "/" ]]; then
    echo "--output-dir must be an absolute directory other than /" >&2
    exit 2
fi

for command in docker openssl tar; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "Required command is unavailable: $command" >&2
        exit 1
    fi
done

docker inspect "$source_container" >/dev/null
docker exec -u postgres "$source_container" \
    psql --dbname "$database" --no-align --tuples-only \
    --command "SELECT 1" >/dev/null

if [[ -z "$postgres_image" ]]; then
    postgres_major=$(docker exec -u postgres "$source_container" \
        psql --dbname "$database" --no-align --tuples-only \
        --command "SHOW server_version_num" | cut -c1-2)
    if [[ ! "$postgres_major" =~ ^[0-9]+$ ]]; then
        echo "Could not determine the source PostgreSQL major version" >&2
        exit 1
    fi
    postgres_image="postgres:${postgres_major}"
fi

umask 077
mkdir -p "$output_dir"
timestamp=$(date -u +%Y%m%dT%H%M%SZ)
dump_path="$output_dir/samogon-${timestamp}.dump"
media_path="$output_dir/samogon-media-${timestamp}.tar.gz"
checksum_path="$output_dir/samogon-${timestamp}.sha256"
restore_container="samogon-restore-check-${timestamp}"
restore_password=$(openssl rand -hex 24)

cleanup() {
    docker rm -f "$restore_container" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "Creating PostgreSQL dump..."
docker exec -u postgres "$source_container" \
    pg_dump --format=custom --no-owner --no-acl --dbname "$database" \
    > "$dump_path"
test -s "$dump_path"

echo "Creating media archive..."
tar -C "$media_dir" -czf "$media_path" .
test -s "$media_path"
tar -tzf "$media_path" >/dev/null

source_counts=$(docker exec -u postgres "$source_container" \
    psql --dbname "$database" --no-align --tuples-only --field-separator=: \
    --command "SELECT 'django_migrations', count(*) FROM django_migrations
               UNION ALL SELECT 'users_user', count(*) FROM users_user
               UNION ALL SELECT 'chat_room', count(*) FROM chat_room
               UNION ALL SELECT 'chat_message', count(*) FROM chat_message
               ORDER BY 1")

echo "Restoring into isolated PostgreSQL container..."
docker run --detach \
    --name "$restore_container" \
    --tmpfs /var/lib/postgresql/data:rw,noexec,nosuid,size=2g \
    --env POSTGRES_PASSWORD="$restore_password" \
    --env POSTGRES_DB=samogon_restore_check \
    "$postgres_image" >/dev/null

ready=0
for _attempt in $(seq 1 60); do
    if docker exec "$restore_container" \
        pg_isready --username postgres --dbname samogon_restore_check >/dev/null 2>&1; then
        ready=1
        break
    fi
    sleep 1
done
if [[ "$ready" -ne 1 ]]; then
    echo "Temporary PostgreSQL did not become ready" >&2
    exit 1
fi

docker cp "$dump_path" "$restore_container:/tmp/samogon.dump" >/dev/null
docker exec "$restore_container" \
    pg_restore --exit-on-error --no-owner --no-acl \
    --username postgres --dbname samogon_restore_check /tmp/samogon.dump

restored_counts=$(docker exec "$restore_container" \
    psql --username postgres --dbname samogon_restore_check \
    --no-align --tuples-only --field-separator=: \
    --command "SELECT 'django_migrations', count(*) FROM django_migrations
               UNION ALL SELECT 'users_user', count(*) FROM users_user
               UNION ALL SELECT 'chat_room', count(*) FROM chat_room
               UNION ALL SELECT 'chat_message', count(*) FROM chat_message
               ORDER BY 1")

if [[ "$source_counts" != "$restored_counts" ]]; then
    echo "Core table counts differ after restore" >&2
    diff <(printf '%s\n' "$source_counts") <(printf '%s\n' "$restored_counts") || true
    exit 1
fi

if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$dump_path" "$media_path" > "$checksum_path"
else
    shasum -a 256 "$dump_path" "$media_path" > "$checksum_path"
fi

chmod 600 "$dump_path" "$media_path" "$checksum_path"
printf 'Backup and isolated restore verified.\nDatabase: %s\nMedia: %s\nChecksums: %s\n' \
    "$dump_path" "$media_path" "$checksum_path"
