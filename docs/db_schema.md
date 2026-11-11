# Схема БД

Две базы в одном Postgres:

* `alfa_source` — выгрузка кейса как есть (восстанавливается из `data/alfa_course_hackathon.backup`), только читаем.
* `alfa_bot` — данные бота и индекс базы знаний. DDL: [`db/schema.sql`](../db/schema.sql).

```mermaid
erDiagram
    users ||--o| business_profiles : "профиль"
    users ||--o{ dialog_messages : "диалог"
    users ||--o{ reminders : "напоминания"
    users ||--o{ events : "события"
    kb_documents ||--o{ kb_chunks : "фрагменты"
    kb_documents ||--o{ reminders : "материал для продолжения"

    users {
        bigint id PK "Telegram id"
        text username
        text timezone
        timestamptz created_at
        timestamptz last_seen_at
    }
    business_profiles {
        bigint user_id PK
        text stage "idea|launch|running|growth"
        text legal_form "ip|ooo|self_employed|none"
        text tax_regime "usn6|usn15|patent|npd|osno"
        text industry
        text experience
        text current_goal
        bool has_staff
    }
    kb_documents {
        int id PK
        text source_table "Articles|Lessons|Courses|Documents|Glossary|..."
        text source_id "id в выгрузке"
        text content_type
        text title
        text url "ссылка на kurs.alfabank.ru"
        bool is_paid
        text[] topics
    }
    kb_chunks {
        int id PK
        int document_id FK
        int chunk_index
        text text
        vector embedding "bge-m3, 1024"
        tsvector tsv "полнотекстовый поиск"
    }
    dialog_messages {
        bigint id PK
        bigint user_id FK
        text role
        text text
        text answer_type "answer|clarify|not_found"
        jsonb sources
    }
    reminders {
        bigint id PK
        bigint user_id FK
        text kind "learning|tax|custom"
        text text
        timestamptz next_run_at
        interval repeat_interval "NULL = разовое"
        bool is_active
        int related_doc_id FK
    }
    events {
        bigint id PK
        bigint user_id FK
        text type "question|answer_shown|link_click|reminder_sent|..."
        jsonb payload
        timestamptz created_at
    }
```

## Что откуда берётся в индексе

| Тип | Таблицы выгрузки | Ссылка | Что индексируем |
|---|---|---|---|
| Статьи (834) | `Articles` + `ArticleEmbedItems` | `/articles/{Url}/` | опубликованные, полный текст |
| Курсы (33) | `Courses` | `/courses/{Url}/` | описание, цена, доступ |
| Уроки (235) | `Lessons` + `LessonEmbedItems` | страница курса | только уроки бесплатных курсов |
| Шаблоны (247) | `Documents` + `DocumentEmbedItems` | `/documents/{Url}/` | описание и текст |
| Глоссарий (351) | `Glossary` + `GlossaryEmbedItems` | `/vocabulary/{Url}/` | определения |
| Вебинары, подкасты | `Webinars`, `Podcasts` | `/webinars/…`, `/podcasts/…` | описания |

Платный курс: `Courses.Cost > 0` или курс входит в подписку «Курс+» (`CourseSubscriptions`).
Тексты платных уроков в индекс не попадают, бот только коротко называет пользу и даёт ссылку.

## Метрика возврата

`events` пишет каждое действие пользователя в боте. Возврат на платформу считаем по
`link_click` (переход по ссылке на материал) и `reminder_open` (переход из напоминания):
доля пользователей с переходом за 7 дней и число дней с переходами на пользователя.
