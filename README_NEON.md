# Neon Free — короткая настройка

1. Создай аккаунт на Neon и новый Postgres project.
2. Скопируй connection string из **Connect**.
3. В Render добавь переменную:

```text
DATABASE_URL=postgresql://...?...&sslmode=require
```

4. Не создавай SQLite и не добавляй Persistent Disk.
5. После первого запуска сервис автоматически создаст нужные таблицы.

Текущие лимиты Free Plan Neon меняются; сверяй их на официальной странице Neon перед запуском. На текущем Free Plan Neon заявлены бесплатная стоимость, 0.5 GB storage на проект и месячный compute allowance; актуальные значения лучше проверять непосредственно в Neon Console/docs.
