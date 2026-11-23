"""极简 JWT 认证服务：注册、登录、鉴权。

定位是**给本项目发 token 的小服务**，不是通用 IAM：

- 用户存 SQLite（单文件、零运维），密码用 PBKDF2-HMAC-SHA256 加盐存哈希；
- 签出来的 token 是 HS256，claim 里带 `user_id` 与 `exp`——这正是
  `app/auth/jwt.py` 校验时需要的东西（`user_id_claim` 默认就是 `user_id`）；
- 签名密钥直接读主项目的 `AGRI_JWT_SECRET`，两边不存第二份，也就不会漂。

校验职责仍在主项目：它按 `X-App-Id` 找到应用配置、用对应密钥验签。本服务只负责
"这个人是谁"，不负责"他能调哪些 Agent"（那是 `applications.allowed_agents` 的事）。
"""
