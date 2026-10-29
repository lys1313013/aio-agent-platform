# 聊天图片附件权限

图片对象只允许使用 `chat-attachments/{user_id}/{session_id|_pending}/{filename}`。

- `user_id` 来自已认证用户；`session_id` 来自已校验归属和执行权限的会话。新会话只能使用本人 `_pending` 上传，已有会话可以使用本人 `_pending` 或本会话附件。
- 普通聊天、流式聊天及斜杠命令在保存输入之前调用统一校验；文字模型也需要校验，不能通过图片链接路径绕过。
- 历史重放使用数据库会话的所有者重新校验附件，不信任历史消息携带的用户或对象信息。房间提交和房间执行也使用相同规则。
- 发给模型的 URL 由服务器重建，忽略客户端 URL。对象读取再次校验 key，并使用对象存储中的 MIME 和大小；Anthropic 图片 MIME 取实际生成的数据 URI。
- 上传时指定会话必须具有该会话的访问权限；指定房间时验证房间所有者和房间执行租户权限。

没有数据库结构变更，也不重写历史记录。本人正常 `_pending` 上传及当前会话附件继续可用；旧记录若含其他用户、其他会话或非法前缀的 key，重放会拒绝该请求，不能继续将这些对象发送给模型。不同会话间需要重新上传图片。

隔离回归：

```sh
cd backend
.venv/bin/pytest -q tests/test_chat_attachment_authorization.py tests/test_chat_attachments.py tests/test_message_order.py tests/test_rooms.py tests/test_chat_runs.py
```

这些测试使用 mock 对象存储和 SQLite/假数据库，不连接生产对象存储、数据库或模型。
