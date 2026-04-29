# AS 认证服务器使用说明

本目录实现独立的 AS(Authentication Server) 认证服务器。AS 通过 WebSocket 接收 JSON 报文，负责用户注册、用户登录、修改密码和 TGT 签发。

当前版本是“两表化”实现，运行时只依赖两张数据库表：

- `user_account`：保存用户账号、PBKDF2 密码摘要、登录代数 `login_gen` 和账号状态。
- `security_event_log`：记录注册、登录、改密等安全事件。

AS 不再依赖旧的 `service_registry`、`service_key`、`login_audit`、`ticket_issue_log` 表。AS RSA 私钥和 TGS 长期密钥 `K_TGS` 由环境变量或本地密钥文件加载。

## 目录文件

| 文件 | 作用 |
| --- | --- |
| `as_server.py` | AS WebSocket 服务入口，处理 `REGISTER_REQ`、`AS_REQ`、`CHANGE_PASSWORD_REQ`。 |
| `config.py` | 读取数据库、监听地址、票据参数和密钥来源环境变量。 |
| `db.py` | MySQL DAO，只访问 `user_account` 和 `security_event_log`。 |
| `crypto_utils.py` | RSA、DES、PBKDF2、Base64、随机密钥等密码学工具函数。 |
| `protocol.py` | JSON 协议报文构造、解析和字段校验工具。 |
| `schema_auth.sql` | 两张认证表的建表 SQL。 |
| `seed_auth_keys.py` | 生成本地 AS RSA 密钥和 `K_TGS_BASE64` 示例值。 |
| `smoke_test_as.py` | WebSocket 基本链路测试脚本。 |
| `requirements.txt` | Python 依赖列表。 |
| `.gitignore` | 忽略本地私钥、`K_TGS` 等敏感输出。 |

## 运行前置条件

需要准备：

- Python 3.9+。
- MySQL 8.x 或兼容版本。
- 可以安装 `as/requirements.txt` 中依赖的 Python 环境。
- 一个空数据库或可重建的认证数据库。

安装依赖：

```powershell
python -m pip install -r .\as\requirements.txt
```

如果当前 PowerShell 找不到 `python`，可以改用你机器上的解释器完整路径，例如：

```powershell
D:\anaconda3\python.exe -m pip install -r .\as\requirements.txt
```

## 初始化数据库

先创建数据库，名称可以自定。下面以 `safety_auth` 为例：

```sql
CREATE DATABASE safety_auth
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;
```

Windows PowerShell 中可以用 `cmd /c` 执行 SQL 文件重定向：

```powershell
cmd /c "mysql -u root -p safety_auth < as\schema_auth.sql"
```

Linux/macOS 或 Git Bash 中：

```bash
mysql -u root -p safety_auth < as/schema_auth.sql
```

执行后应只创建：

- `user_account`
- `security_event_log`

## 生成本地密钥

运行密钥生成脚本：

```powershell
python .\as\seed_auth_keys.py
```

默认生成：

- `as/as_private_key.pem`：AS RSA 私钥，服务端使用，不要提交。
- `as/as_public_key.pem`：AS RSA 公钥，客户端和测试脚本使用，可按团队需要提交或分发。
- `as/k_tgs_base64.txt`：`K_TGS` 的 Base64 文本，服务端启动时写入环境变量，不要提交。

如果文件已存在，脚本会拒绝覆盖，避免误轮换密钥。确实需要重新生成时使用：

```powershell
python .\as\seed_auth_keys.py --overwrite
```

`K_TGS_BASE64` 解码后必须正好是 8 字节，因为当前课程协议要求使用 DES。

## 环境变量

### 必填变量

| 变量 | 示例 | 说明 |
| --- | --- | --- |
| `AUTH_DB_USER` | `root` | MySQL 用户名。 |
| `AUTH_DB_NAME` | `safety_auth` | 已初始化的认证数据库名。 |
| `K_TGS_BASE64` | `xxxxxxxxxxx=` | TGS 长期 DES key 的 Base64 文本，解码后必须 8 字节。 |
| `AS_RSA_PRIVATE_KEY_PATH` 或 `AS_RSA_PRIVATE_PEM` | `.\as\as_private_key.pem` | AS RSA 私钥来源，二选一。推荐使用文件路径。 |

### 可选变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `AUTH_DB_HOST` | `127.0.0.1` | MySQL 地址。 |
| `AUTH_DB_PORT` | `3306` | MySQL 端口。 |
| `AUTH_DB_PASSWORD` | 空字符串 | MySQL 密码。 |
| `AS_HOST` | `0.0.0.0` | AS WebSocket 监听地址。 |
| `AS_PORT` | `9000` | AS WebSocket 监听端口。 |
| `AUTH_REALM` | `SAFETYWORK` | 写入 TGT 的认证域。 |
| `AUTH_TGS_SERVICE_NAME` | `krbtgt/{realm}` | 写入 TGT 的 TGS 逻辑服务名。 |
| `AUTH_TGT_TTL_SECONDS` | `7200` | TGT 有效期，默认 2 小时。 |
| `AUTH_PBKDF2_ITER` | `100000` | 新注册或改密时使用的 PBKDF2 迭代次数。 |

PowerShell 设置示例：

```powershell
$env:AUTH_DB_HOST='127.0.0.1'
$env:AUTH_DB_PORT='3306'
$env:AUTH_DB_USER='root'
$env:AUTH_DB_PASSWORD='你的MySQL密码'
$env:AUTH_DB_NAME='safety_auth'

$env:AS_RSA_PRIVATE_KEY_PATH='.\as\as_private_key.pem'
$env:K_TGS_BASE64=(Get-Content .\as\k_tgs_base64.txt -Raw).Trim()

$env:AS_HOST='0.0.0.0'
$env:AS_PORT='9000'
```

也可以直接用 `AS_RSA_PRIVATE_PEM` 传私钥文本，但在命令行中处理换行容易出错，不推荐团队日常使用。

## 启动 AS 服务

```powershell
python .\as\as_server.py
```

启动成功会看到类似输出：

```text
AS server listening on ws://0.0.0.0:9000 realm=SAFETYWORK
```

服务默认监听 `0.0.0.0:9000`。客户端本机访问通常使用：

```text
ws://127.0.0.1:9000
```

## 运行 smoke test

服务启动后，另开一个终端运行：

```powershell
$env:AS_URL='ws://127.0.0.1:9000'
$env:AS_PUBLIC_KEY_PATH='.\as\as_public_key.pem'
python .\as\smoke_test_as.py
```

通过时输出：

```text
AS smoke test passed
```

测试覆盖：

- 注册随机用户。
- 连续登录，确认 `loginGen` 递增。
- 修改密码。
- 旧密码登录失败。
- 新密码登录成功。

## 协议说明

所有 WebSocket 消息都是 UTF-8 JSON 字符串。顶层字段一般包含：

- `type`：报文类型。
- `clientId`：客户端运行期实例 ID，AS 会写入 `security_event_log.client_id`。
- `payload`：请求中的敏感内容。注册、登录、改密请求都使用 RSA 加密。

错误统一返回：

```json
{"type":"ERROR","error":"BAD_CREDENTIALS"}
```

### REGISTER_REQ

请求顶层：

```json
{
  "type": "REGISTER_REQ",
  "clientId": "cli-001",
  "payload": "Base64(RSA-OAEP-SHA256(JSON))"
}
```

RSA 解密前的 payload 明文 JSON：

```json
{
  "username": "alice",
  "password": "Alice1234"
}
```

成功响应：

```json
{
  "type": "REGISTER_REP",
  "payload": "{\"ok\":true,\"userId\":1}"
}
```

注意：`REGISTER_REP.payload` 是普通 JSON 字符串，不是 RSA/DES 密文。

### AS_REQ

请求顶层：

```json
{
  "type": "AS_REQ",
  "clientId": "cli-001",
  "payload": "Base64(RSA-OAEP-SHA256(JSON))"
}
```

RSA 解密前的 payload 明文 JSON：

```json
{
  "username": "alice",
  "password": "Alice1234",
  "nonce": "n1"
}
```

成功响应：

```json
{
  "type": "AS_REP",
  "ticket": "Base64(DES-CBC-PKCS7(K_TGS,TGT_JSON))",
  "payload": "{\"salt\":\"...\",\"iter\":100000,\"part\":\"...\"}"
}
```

`AS_REP.ticket` 是 TGT，使用 `K_TGS` 加密。TGT 明文字段包括：

- `ticketType`: 固定为 `TGT`。
- `realm`: 认证域。
- `userId`: 用户 ID。
- `username`: 规范化用户名。
- `clientId`: 客户端 ID。
- `service`: TGS 逻辑服务名。
- `kcTgs`: Base64 后的客户端-TGS 会话 DES key。
- `loginGen`: 本次登录成功后递增得到的登录代数。
- `iat`: 签发时间，Unix 毫秒。
- `exp`: 过期时间，Unix 毫秒。

`AS_REP.payload` 是普通 JSON 字符串，里面的 `part` 使用 `Kuser` 加密。`Kuser` 的派生方式：

```text
PBKDF2-HMAC-SHA256(password, salt, iter, dklen=32) 的前 8 字节
```

`part` 解密后包含：

- `userId`
- `username`
- `nonce`
- `kcTgs`
- `exp`
- `loginGen`

客户端应校验 `part.nonce` 是否等于自己发出的 nonce。

### CHANGE_PASSWORD_REQ

请求顶层：

```json
{
  "type": "CHANGE_PASSWORD_REQ",
  "clientId": "cli-001",
  "payload": "Base64(RSA-OAEP-SHA256(JSON))"
}
```

RSA 解密前的 payload 明文 JSON：

```json
{
  "username": "alice",
  "oldPassword": "Alice1234",
  "newPassword": "Alice5678"
}
```

成功响应：

```json
{
  "type": "CHANGE_PASSWORD_REP",
  "payload": "{\"ok\":true}"
}
```

改密成功会递增 `user_account.login_gen`，后续 TGS/GS 校验 `loginGen` 时应让旧 TGT、旧 Service Ticket 和旧 GS 会话失效。

## 密码和账号规则

用户名规则：

- AS 写入和查询前统一执行 `trim + lower`。
- 最大长度 64 字符。
- `user_account.username` 有唯一索引。

密码规则：

- 至少 8 位。
- 至少包含一个大写字母。
- 至少包含一个小写字母。
- 至少包含一个数字。

账号状态：

- `status = 1`：启用，允许登录和改密。
- `status = 0`：禁用，登录和改密返回 `ACCOUNT_DISABLED`。

## 数据库字段行为

`user_account` 关键字段：

- `password_hash`: PBKDF2 后的摘要，不保存明文密码。
- `password_salt`: PBKDF2 salt。
- `pbkdf2_iter`: PBKDF2 迭代次数。
- `login_gen`: 成功登录和改密时递增。
- `last_login_at`: 最近一次成功登录时间。
- `created_at`: 默认 `CURRENT_TIMESTAMP(3)`。
- `updated_at`: 默认 `CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3)`。

`security_event_log` 关键字段：

- `event_type`: 当前 AS 会写 `REGISTER`、`LOGIN_SUCCESS`、`LOGIN_FAIL`、`CHANGE_PASSWORD`。
- `result`: 成功写 `1`，失败写 `0`。
- `reason`: 失败原因，例如 `BAD_CREDENTIALS`、`WEAK_PASSWORD`、`USERNAME_EXISTS`、`ACCOUNT_DISABLED`。
- `client_id`: 顶层报文 `clientId`。
- `remote_addr`: WebSocket 远端地址，仅用于审计。

## 常见错误

| 错误码 | 含义 | 处理建议 |
| --- | --- | --- |
| `BAD_CREDENTIALS` | 用户不存在、旧密码错误或登录密码错误。 | 检查用户名和密码。 |
| `USERNAME_EXISTS` | 注册用户名已存在。 | 换用户名，或直接登录。 |
| `WEAK_PASSWORD` | 新密码不满足强度策略。 | 使用至少 8 位且含大小写字母和数字的密码。 |
| `ACCOUNT_DISABLED` | `user_account.status = 0`。 | 管理员在数据库中启用账号后再试。 |
| `KEY_NOT_CONFIGURED` | AS 私钥或 `K_TGS` 未正确加载。 | 检查 `AS_RSA_PRIVATE_KEY_PATH` / `AS_RSA_PRIVATE_PEM` 和 `K_TGS_BASE64`。 |
| `INVALID_BASE64` | Base64 字段格式错误。 | 检查 `payload` 或 `K_TGS_BASE64`。 |
| `RSA_DECRYPT_FAILED` | RSA 解密失败。 | 确认客户端使用的是当前 AS 公钥。 |
| `INVALID_DES_KEY_LENGTH` | DES key 不是 8 字节。 | 重新生成或检查 `K_TGS_BASE64`。 |

依赖问题：

- `No module named 'websockets'`：安装 `requirements.txt`。
- `No module named 'Crypto'`：缺少 `pycryptodome`，安装 `requirements.txt`。
- `pymysql is required`：缺少 `pymysql`，安装 `requirements.txt`。

## 安全注意事项

- 不要提交 `as/as_private_key.pem`。
- 不要提交 `as/k_tgs_base64.txt`。
- 如果泄露 AS 私钥或 `K_TGS`，应重新生成密钥，并清理旧票据和测试数据。
- 公钥 `as/as_public_key.pem` 可以分发给客户端，但客户端必须确保使用的是当前 AS 对应的公钥。
- `K_TGS_BASE64` 是 AS 和 TGS 之间的长期共享密钥；后续实现 TGS 时必须使用同一个值才能解密 TGT。

## 推荐启动流程

完整 PowerShell 示例：

```powershell
# 1. 安装依赖
python -m pip install -r .\as\requirements.txt

# 2. 初始化数据库
mysql -u root -p -e "CREATE DATABASE IF NOT EXISTS safety_auth DEFAULT CHARACTER SET utf8mb4 DEFAULT COLLATE utf8mb4_unicode_ci;"
cmd /c "mysql -u root -p safety_auth < as\schema_auth.sql"

# 3. 生成密钥
python .\as\seed_auth_keys.py

# 4. 设置环境变量
$env:AUTH_DB_HOST='127.0.0.1'
$env:AUTH_DB_PORT='3306'
$env:AUTH_DB_USER='root'
$env:AUTH_DB_PASSWORD='你的MySQL密码'
$env:AUTH_DB_NAME='safety_auth'
$env:AS_RSA_PRIVATE_KEY_PATH='.\as\as_private_key.pem'
$env:K_TGS_BASE64=(Get-Content .\as\k_tgs_base64.txt -Raw).Trim()

# 5. 启动 AS
python .\as\as_server.py
```

另开一个 PowerShell 终端测试：

```powershell
$env:AS_URL='ws://127.0.0.1:9000'
$env:AS_PUBLIC_KEY_PATH='.\as\as_public_key.pem'
python .\as\smoke_test_as.py
```
