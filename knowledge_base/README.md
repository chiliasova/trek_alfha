# knowledge_base - модуль подготовки базы знаний «Альфа-Курса»

Компонент трека 2 (ИИ-помощник в Telegram). Отвечает за данные, персонализацию и метрики.

## Что внутри

| Файл | Назначение |
|---|---|
| `chunker.py` | Нарезка материалов на чанки с метаданными для RAG |
| `meta.json` | Карточки материалов (тема, платность, стадия, уровень) |
| `schema.sql` | Схема БД: пользователи, профиль, события, напоминания |
| `mock_data.py` | Генерация тестовых данных (50 пользователей, 14 дней) |
| `tax_calendar.py` | Налоговый календарь по режимам (УСН/Патент/НПД/ОСНО) |
| `recommender.py` | Скоринг материалов по профилю + объяснение |
| `evaluate.py` | Hit@3 и доля честных отказов на тестовом наборе |
| `metrics.py` | DAU/Retention, график для презентации |

## Запуск

```bash
pip install pandas matplotlib
python chunker.py
python mock_data.py
python tax_calendar.py
python recommender.py
python evaluate.py
python metrics.py