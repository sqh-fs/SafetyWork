-- AS 两表化认证库结构。
--
-- 使用方式:
--   mysql -u <user> -p <database> < as/schema_auth.sql
--
-- 本脚本只创建图片设计中的两张表:
--   1. user_account: 用户账号、密码摘要、登录代数和状态。
--   2. security_event_log: 注册、登录、改密等安全事件日志。
--
-- 说明:
--   - 本脚本不包含旧库迁移逻辑。
--   - 以新建或重建认证库为主，AS 运行时只依赖下方两张表。

CREATE TABLE IF NOT EXISTS user_account (
    user_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '用户唯一 ID，写入 TGT 和后续 ServiceTicket',
    username VARCHAR(64) NOT NULL COMMENT '用户登录名；AS 写入前统一 trim + lower',
    password_hash VARBINARY(64) NOT NULL COMMENT 'PBKDF2-HMAC-SHA256 后的密码摘要，不保存明文密码',
    password_salt VARBINARY(32) NOT NULL COMMENT 'PBKDF2 salt，当前实现默认生成 16 字节',
    pbkdf2_iter INT UNSIGNED NOT NULL DEFAULT 100000 COMMENT 'PBKDF2 迭代次数',
    login_gen INT UNSIGNED NOT NULL DEFAULT 0 COMMENT '登录代数；成功登录和改密时递增，用于让旧票据/旧会话失效',
    status TINYINT UNSIGNED NOT NULL DEFAULT 1 COMMENT '账号状态：1 表示启用，0 表示禁用',
    last_login_at DATETIME(3) NULL COMMENT '最近一次成功登录时间',
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '账号创建时间',
    updated_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3) COMMENT '账号更新时间',
    PRIMARY KEY (user_id),
    UNIQUE KEY uk_user_account_username (username),
    KEY idx_user_account_status (status)
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci
  COMMENT='AS 用户账号表';

CREATE TABLE IF NOT EXISTS security_event_log (
    event_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '安全事件 ID',
    user_id BIGINT UNSIGNED NULL COMMENT '关联用户 ID；登录失败或用户名不存在时允许为空',
    username VARCHAR(64) NULL COMMENT '用户名快照，便于追踪登录失败或非法请求',
    event_type VARCHAR(32) NOT NULL COMMENT '事件类型，如 REGISTER、LOGIN_SUCCESS、LOGIN_FAIL、CHANGE_PASSWORD、TICKET_EXPIRED、REPLAY_BLOCKED',
    result TINYINT UNSIGNED NOT NULL COMMENT '事件结果：1 成功，0 失败',
    client_id VARCHAR(64) NULL COMMENT '客户端运行期实例 ID，仅记录报文中携带的 clientId',
    remote_addr VARCHAR(128) NULL COMMENT 'WebSocket 连接远端地址，仅用于审计',
    reason VARCHAR(128) NULL COMMENT '失败原因或安全事件原因',
    created_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3) COMMENT '事件发生时间',
    PRIMARY KEY (event_id),
    KEY idx_security_event_user_time (user_id, created_at),
    KEY idx_security_event_type_time (event_type, created_at),
    CONSTRAINT fk_security_event_user
        FOREIGN KEY (user_id) REFERENCES user_account(user_id)
        ON UPDATE CASCADE
        ON DELETE SET NULL
) ENGINE=InnoDB
  DEFAULT CHARSET=utf8mb4
  COLLATE=utf8mb4_unicode_ci
  COMMENT='AS 安全事件日志表';
