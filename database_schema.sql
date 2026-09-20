-- База данных SNI-доноров для VLESS Reality
-- Автоматическое обновление через RealiTLScanner

CREATE TABLE IF NOT EXISTS sni_donors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hostname TEXT NOT NULL UNIQUE,           -- например: "api.notion.com"
    ip_address TEXT NOT NULL,                -- IP адрес сервера
    country_code TEXT,                       -- "US", "DE", "FR"
    asn TEXT,                                -- AS номер
    as_name TEXT,                            -- Название автономной системы
    port INTEGER DEFAULT 443,                -- Порт (обычно 443)
    tls_version TEXT,                        -- "TLSv1.3"
    http_version TEXT,                       -- "HTTP/2"
    certificate_issuer TEXT,                 -- "Let's Encrypt", "DigiCert"
    has_cdn BOOLEAN DEFAULT FALSE,           -- Есть ли CDN
    cdn_name TEXT,                           -- Cloudflare, Akamai и т.д.
    last_checked TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    next_check TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    success_rate REAL DEFAULT 0.0,           -- Процент успешных подключений
    failure_count INTEGER DEFAULT 0,         -- Количество неудачных проверок
    total_checks INTEGER DEFAULT 0,          -- Всего проверок
    is_active BOOLEAN DEFAULT TRUE,          -- Активен ли донор
    tags TEXT,                               -- "stable", "fast", "saas", "developer"
    added_by TEXT DEFAULT 'system',          -- Кто добавил: system/user_id
    notes TEXT                               -- Примечания
);

-- Таблица для истории проверок
CREATE TABLE IF NOT EXISTS check_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    donor_id INTEGER,
    check_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_success BOOLEAN,
    response_time_ms INTEGER,
    error_message TEXT,
    FOREIGN KEY (donor_id) REFERENCES sni_donors(id)
);

-- Таблица для статистики использования
CREATE TABLE IF NOT EXISTS usage_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    donor_id INTEGER,
    user_count INTEGER DEFAULT 0,           -- Сколько пользователей используют
    last_used TIMESTAMP,
    total_connections INTEGER DEFAULT 0,
    FOREIGN KEY (donor_id) REFERENCES sni_donors(id)
);

-- Таблица для черного списка (не работающие)
CREATE TABLE IF NOT EXISTS blacklist (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    hostname TEXT UNIQUE,
    ip_address TEXT,
    reason TEXT,                            -- "blocked", "cdn", "tls_error"
    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Индексы для быстрого поиска
CREATE INDEX IF NOT EXISTS idx_donors_active ON sni_donors(is_active, success_rate DESC);
CREATE INDEX IF NOT EXISTS idx_donors_country ON sni_donors(country_code);
CREATE INDEX IF NOT EXISTS idx_donors_checked ON sni_donors(next_check);
CREATE INDEX IF NOT EXISTS idx_donors_tags ON sni_donors(tags);

-- Создаем представление для лучших доноров
CREATE VIEW IF NOT EXISTS best_donors AS
SELECT 
    hostname,
    ip_address,
    country_code,
    success_rate,
    last_checked,
    tags
FROM sni_donors 
WHERE is_active = TRUE 
    AND has_cdn = FALSE
    AND success_rate > 0.8
ORDER BY success_rate DESC, last_checked DESC;

-- Инициализируем несколькими известными донорами (примеры)
INSERT OR IGNORE INTO sni_donors (
    hostname, ip_address, country_code, tls_version, http_version, 
    certificate_issuer, has_cdn, tags, success_rate, total_checks
) VALUES 
    ('api.notion.com', '143.204.68.34', 'US', 'TLSv1.3', 'HTTP/2', 'DigiCert', FALSE, 'saas,stable', 0.92, 100),
    ('api.github.com', '140.82.121.3', 'US', 'TLSv1.3', 'HTTP/2', 'DigiCert', FALSE, 'developer,stable', 0.88, 95),
    ('slack.com', '52.85.193.24', 'US', 'TLSv1.3', 'HTTP/2', 'Amazon', FALSE, 'saas,fast', 0.85, 80),
    ('vercel.app', '76.76.21.21', 'US', 'TLSv1.3', 'HTTP/2', 'Let''s Encrypt', FALSE, 'developer,cdn-edge', 0.78, 60);

-- Создаем триггер для автоматического обновления next_check
CREATE TRIGGER IF NOT EXISTS update_next_check
AFTER UPDATE OF last_checked ON sni_donors
FOR EACH ROW
BEGIN
    UPDATE sni_donors 
    SET next_check = datetime(last_checked, '+1 hour')
    WHERE id = NEW.id;
END;