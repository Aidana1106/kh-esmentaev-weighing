-- Обезличивание копии боевой базы перед загрузкой на тестовый стенд (ЛЗ 11).
-- Запускать ТОЛЬКО на копии:
--   HASH=$(python -c "from app.security import hash_password; print(hash_password('ПАРОЛЬ_СТЕНДА'))")
--   psql "$TEST_DATABASE_URL" -v test_hash="$HASH" -f scripts/anonymize.sql
-- Значения заменяются, а не удаляются: структура и объём данных остаются реалистичными.

BEGIN;
-- 1. Люди: имена и логины заменяются на обезличенные, пароли сбрасываются.
--    Всем ставится один тестовый пароль стенда (хэш передаётся через -v test_hash=...).
UPDATE users SET
    name  = CASE role WHEN 'admin' THEN 'Администратор ' || id ELSE 'Весовщик ' || id END,
    login = CASE role WHEN 'admin' THEN 'admin' || id ELSE 'user' || id END,
    password_hash = :'test_hash';
-- 2. Сессии не переносятся: на стенде никто не должен оказаться залогиненным чужим токеном.
DELETE FROM sessions;
-- 3. Веса: небольшой случайный шум ±3 %, чтобы итоги не совпадали с реальным урожаем заказчика.
UPDATE trips SET
    gross_kg = tare_kg + greatest(100, round((gross_kg - tare_kg) * (0.97 + random() * 0.06)))::int;
COMMIT;
-- Машины и поля – справочники хозяйства, не персональные данные: остаются как есть.
