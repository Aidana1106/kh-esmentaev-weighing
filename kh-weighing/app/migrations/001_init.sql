-- Схема MVP: машины, поля, пользователи, рейсы, журнал исправлений.
-- Вес везде в целых килограммах.

CREATE TABLE users (
    id            serial PRIMARY KEY,
    login         text NOT NULL UNIQUE,
    name          text NOT NULL,
    role          text NOT NULL CHECK (role IN ('operator', 'admin')),
    password_hash text NOT NULL,
    active        boolean NOT NULL DEFAULT true,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE sessions (
    token_hash text PRIMARY KEY,           -- храним только хэш токена
    user_id    int NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL
);
CREATE INDEX sessions_user_idx ON sessions(user_id);

-- Рейс привязан к машине, а не к водителю: водители меняются, машины нет.
CREATE TABLE vehicles (
    id      serial PRIMARY KEY,
    name    text NOT NULL UNIQUE,
    tare_kg int CHECK (tare_kg > 0),        -- тара на сезон; NULL = ещё не задана
    active  boolean NOT NULL DEFAULT true
);

CREATE TABLE fields (
    id     serial PRIMARY KEY,
    name   text NOT NULL UNIQUE,
    crop   text,                            -- культура на сезон (необязательно)
    active boolean NOT NULL DEFAULT true
);

CREATE TABLE trips (
    id           uuid PRIMARY KEY,          -- генерирует клиент: повторная отправка из офлайн-очереди не создаёт дубль
    vehicle_id   int NOT NULL REFERENCES vehicles(id),
    field_id     int NOT NULL REFERENCES fields(id),
    gross_kg     int NOT NULL,
    tare_kg      int NOT NULL,              -- копия тары на момент рейса (не ссылка на машину)
    net_kg       int GENERATED ALWAYS AS (gross_kg - tare_kg) STORED,
    weighed_at   timestamptz NOT NULL,      -- время взвешивания с устройства (ZER-4)
    received_at  timestamptz NOT NULL DEFAULT now(),
    created_by   int NOT NULL REFERENCES users(id),
    updated_at   timestamptz,
    is_corrected boolean NOT NULL DEFAULT false,   -- ZER-10
    CONSTRAINT trips_tare_positive CHECK (tare_kg > 0),
    CONSTRAINT trips_gross_gt_tare CHECK (gross_kg > tare_kg),      -- ZER-6
    CONSTRAINT trips_gross_sane    CHECK (gross_kg <= 100000)
);
CREATE INDEX trips_weighed_at_idx ON trips(weighed_at);
CREATE INDEX trips_field_idx ON trips(field_id);
CREATE INDEX trips_vehicle_idx ON trips(vehicle_id);

-- Журнал исправлений (НФТ-05): только добавление.
CREATE TABLE trip_revisions (
    id         bigserial PRIMARY KEY,
    trip_id    uuid NOT NULL REFERENCES trips(id),
    changed_at timestamptz NOT NULL DEFAULT now(),
    changed_by int NOT NULL REFERENCES users(id),
    old        jsonb NOT NULL,
    new        jsonb NOT NULL
);
CREATE INDEX trip_revisions_trip_idx ON trip_revisions(trip_id);

CREATE FUNCTION forbid_revision_change() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'trip_revisions is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trip_revisions_immutable
    BEFORE UPDATE OR DELETE ON trip_revisions
    FOR EACH ROW EXECUTE FUNCTION forbid_revision_change();
