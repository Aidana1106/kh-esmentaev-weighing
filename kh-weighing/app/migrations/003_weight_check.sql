-- Проверка веса (ADR-003, задача #61): предупреждение о подозрительном брутто.
-- Предупреждение считает телефон (работает без связи), сервер хранит результат для отчёта PM.
ALTER TABLE trips
    ADD COLUMN weight_warning   text,                           -- текст предупреждения, если оно было
    ADD COLUMN weight_confirmed boolean NOT NULL DEFAULT false; -- весовщик нажал «Вес верный, сохранить»
