#!/bin/bash
# Выполняется один раз при первом старте контейнера Postgres.
# Восстанавливает выгрузку кейса в отдельную базу alfa_source.
set -e

BACKUP=/source/alfa_course_hackathon.backup

if [ ! -f "$BACKUP" ]; then
    echo "WARNING: $BACKUP не найден. Положите выгрузку в папку data/ и пересоздайте том: docker compose down -v"
    exit 0
fi

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres -c "CREATE DATABASE alfa_source"
# --no-owner/--no-privileges: роли из исходной базы у нас не существуют
pg_restore --username "$POSTGRES_USER" --dbname alfa_source --no-owner --no-privileges "$BACKUP" || \
    echo "pg_restore завершился с предупреждениями (обычно это отсутствующие роли), продолжаем"
echo "Выгрузка восстановлена в базу alfa_source"
