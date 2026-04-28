-- AuthDB 初始化脚本。
--
-- 作用：
-- 1. 保存用户账号、密码摘要、PBKDF2 参数和 loginGen。
-- 2. 保存 AS/TGS/GS 服务注册信息和长期密钥密文。
-- 3. 保存注册、登录、改密、票据签发等低频审计信息。
--
-- 使用方式：
-- 在启动 as/as_server.py 前，先在 MySQL 目标库中执行本脚本。
-- 本脚本只创建表，不插入初始密钥；密钥由 seed_auth_keys.py 写入。

SET NAMES utf8mb4;

-- 用户账号表。
-- 输入来源：REGISTER_REQ、AS_REQ、CHANGE_PASSWORD_REQ。
-- 输出用途：AS 校验密码；TGS/GS 后续可读取 login_gen 判断旧票据是否失效。
CREATE TABLE IF NOT EXISTS user_account (
    -- 用户唯一 ID，作为审计日志和票据日志的外键。
    user_id BIGINT NOT NULL AUTO_INCREMENT,
    -- 登录名。服务端会 trim + lower，因此这里用唯一索引防止重复账号。
    username VARCHAR(64) NOT NULL,
    -- PBKDF2-HMAC-SHA256 派生出的 32 字节摘要，不保存明文密码。
    password_hash VARBINARY(64) NOT NULL,
    -- 每个用户独立随机盐，客户端登录时通过 AS_REP.payload.salt 拿到。
    password_salt VARBINARY(32) NOT NULL,
    -- 预留算法标识，当前固定 PBKDF2。
    password_algo VARCHAR(32) NOT NULL DEFAULT 'PBKDF2',
    -- PBKDF2 迭代次数，客户端派生 Kuser 时必须使用相同值。
    pbkdf2_iter INT NOT NULL DEFAULT 100000,
    -- 登录代数。登录或改密成功后递增，用于使旧 TGT/Service Ticket/GS 会话失效。
    login_gen INT NOT NULL DEFAULT 0,
    -- 账号状态。AS 只允许 ACTIVE 用户登录和改密。
    status VARCHAR(16) NOT NULL DEFAULT 'ACTIVE',
    -- 最近一次成功登录的客户端实例 ID，便于审计和调试。
    last_client_id VARCHAR(64) NULL,
    -- 最近一次成功登录时间。
    last_login_at DATETIME(3) NULL,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (user_id),
    UNIQUE KEY uk_user_account_username (username),
    KEY idx_user_account_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 服务注册表。
-- 作用：让 AS/TGS/GS 通过稳定 service_name 查到服务 ID、类型和地址。
-- AS 种子脚本会写入 AS 服务和 TGS 服务记录。
CREATE TABLE IF NOT EXISTS service_registry (
    service_id BIGINT NOT NULL AUTO_INCREMENT,
    -- 服务名，例如 as/GAME.LOCAL 或 krbtgt/GAME.LOCAL。
    service_name VARCHAR(128) NOT NULL,
    -- 服务类型，当前脚本至少使用 AS、TGS。
    service_type VARCHAR(16) NOT NULL,
    -- 认证域，用于区分不同 Kerberos realm。
    realm VARCHAR(64) NOT NULL DEFAULT 'GAME.LOCAL',
    host VARCHAR(64) NOT NULL,
    port INT NOT NULL,
    -- WebSocket 访问地址，便于客户端或其他服务发现。
    websocket_url VARCHAR(255) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'ENABLED',
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (service_id),
    UNIQUE KEY uk_service_registry_name (service_name),
    KEY idx_service_registry_type_status (service_type, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 服务密钥表。
-- 作用：保存 AS RSA 私钥/公钥、TGS DES 长期密钥等服务密钥。
-- 注意：key_ciphertext 存 Fernet 加密后的密文，不存明文密钥。
CREATE TABLE IF NOT EXISTS service_key (
    key_id BIGINT NOT NULL AUTO_INCREMENT,
    -- 关联 service_registry.service_id。
    service_id BIGINT NOT NULL,
    -- 密钥用途，例如 AS_RSA_PRIVATE、AS_RSA_PUBLIC、K_TGS。
    key_usage VARCHAR(32) NOT NULL,
    -- 密钥版本号，便于后续轮换。
    key_version VARCHAR(32) NOT NULL,
    -- 原始密钥算法，例如 RSA 或 DES。
    algorithm VARCHAR(32) NOT NULL,
    -- Fernet(AUTH_MASTER_KEY, 明文密钥材料)。
    -- RSA PEM 加密后可能较长，因此这里放宽到 8192 字节。
    key_ciphertext VARBINARY(8192) NOT NULL,
    enabled TINYINT NOT NULL DEFAULT 1,
    -- not_before / expires_at 预留给密钥轮换窗口，本轮代码只检查 enabled。
    not_before DATETIME(3) NULL,
    expires_at DATETIME(3) NULL,
    created_at DATETIME(3) NOT NULL,
    updated_at DATETIME(3) NOT NULL,
    PRIMARY KEY (key_id),
    UNIQUE KEY uk_service_key_version (service_id, key_usage, key_version),
    KEY idx_service_key_enabled (service_id, key_usage, enabled),
    CONSTRAINT fk_service_key_service
        FOREIGN KEY (service_id) REFERENCES service_registry(service_id)
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 登录与安全审计表。
-- 作用：记录注册、登录成功/失败、改密等安全事件，便于答辩和排查问题。
CREATE TABLE IF NOT EXISTS login_audit (
    audit_id BIGINT NOT NULL AUTO_INCREMENT,
    -- 登录失败且用户名不存在时允许为空。
    user_id BIGINT NULL,
    -- 事件发生时提交的用户名快照，避免用户后续改名影响审计。
    username_snapshot VARCHAR(64) NULL,
    -- 客户端实例 ID。
    client_id VARCHAR(64) NULL,
    -- REGISTER、LOGIN_SUCCESS、LOGIN_FAILED、CHANGE_PASSWORD 等。
    event_type VARCHAR(32) NOT NULL,
    -- 1 表示成功，0 表示失败。
    success TINYINT NOT NULL,
    -- 失败原因，例如 BAD_CREDENTIALS、WEAK_PASSWORD。
    error_code VARCHAR(64) NULL,
    -- WebSocket remote_address 中提取的客户端 IP。
    ip_addr VARCHAR(64) NULL,
    -- 事件完成后的 loginGen，未知时为空。
    login_gen_after INT NULL,
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (audit_id),
    KEY idx_login_audit_user_time (user_id, created_at),
    KEY idx_login_audit_event_time (event_type, created_at),
    CONSTRAINT fk_login_audit_user
        FOREIGN KEY (user_id) REFERENCES user_account(user_id)
        ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 票据签发日志表。
-- 作用：记录 TGT/Service Ticket 的签发历史，便于审计和调试。
-- 安全点：只保存票据密文 hash，不保存票据明文。
CREATE TABLE IF NOT EXISTS ticket_issue_log (
    ticket_log_id BIGINT NOT NULL AUTO_INCREMENT,
    -- 被签发票据的用户。
    user_id BIGINT NOT NULL,
    -- 客户端实例 ID。
    client_id VARCHAR(64) NOT NULL,
    -- 当前 AS 写 TGT；后续 TGS 可写 SERVICE_TICKET。
    ticket_type VARCHAR(16) NOT NULL,
    -- 票据目标服务。TGT 指向 TGS 服务。
    service_id BIGINT NULL,
    -- 票据密文字符串的 SHA-256 摘要，用于唯一追踪票据。
    ticket_hash CHAR(64) NOT NULL,
    -- 票据中绑定的 loginGen。
    login_gen INT NOT NULL,
    -- 签发和过期时间。
    issued_at DATETIME(3) NOT NULL,
    expire_at DATETIME(3) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'ISSUED',
    created_at DATETIME(3) NOT NULL,
    PRIMARY KEY (ticket_log_id),
    UNIQUE KEY uk_ticket_issue_hash (ticket_hash),
    KEY idx_ticket_issue_user_time (user_id, issued_at),
    KEY idx_ticket_issue_expire (expire_at, status),
    CONSTRAINT fk_ticket_issue_user
        FOREIGN KEY (user_id) REFERENCES user_account(user_id)
        ON DELETE CASCADE,
    CONSTRAINT fk_ticket_issue_service
        FOREIGN KEY (service_id) REFERENCES service_registry(service_id)
        ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
