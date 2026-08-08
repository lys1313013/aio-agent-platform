"""Management CLI — administrative commands for the platform.

Usage:
    python -m aio_agent_platform.manage set-admin <username>
    python -m aio_agent_platform.manage list-users
    python -m aio_agent_platform.manage purge-shadow-users [--dry-run]
"""

import argparse
import asyncio
import sys
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.db.connection import close_db, get_session_factory
from aio_agent_platform.db.models import (
    ChannelBinding,
    ChannelSessionMapping,
    CronJob,
    Delegation,
    Hook,
    Memory,
    Message,
    PetExpLog,
    PortraitVersion,
    RefreshToken,
    SandboxSession,
    Session,
    Skill,
    TenantMembership,
    TokenUsageDaily,
    Trace,
    User,
    UserConfig,
    UserPet,
    UserProfile,
    Workspace,
)
from aio_agent_platform.hooks import dispatcher
from aio_agent_platform.hooks.manager import HookDef


async def _get_session() -> AsyncSession:
    factory = get_session_factory()
    return factory()


async def set_admin(username: str) -> None:
    """Promote a user to admin role."""
    session = await _get_session()
    try:
        result = await session.execute(
            select(User).where(User.username == username)
        )
        user = result.scalar_one_or_none()
        if not user:
            print(f"错误：用户 '{username}' 不存在。")
            sys.exit(1)

        if user.role == "admin":
            print(f"用户 '{username}' 已经是管理员。")
            return

        user.role = "admin"
        await session.commit()
        print(f"已将用户 '{username}' 设置为管理员。")
    except Exception as e:
        await session.rollback()
        print(f"操作失败：{e}")
        sys.exit(1)
    finally:
        await session.close()
        await close_db()


async def remove_admin(username: str) -> None:
    """Demote a user from admin role."""
    session = await _get_session()
    try:
        result = await session.execute(
            select(User).where(User.username == username)
        )
        user = result.scalar_one_or_none()
        if not user:
            print(f"错误：用户 '{username}' 不存在。")
            sys.exit(1)

        if user.role != "admin":
            print(f"用户 '{username}' 不是管理员。")
            return

        user.role = "user"
        await session.commit()
        print(f"已将用户 '{username}' 的管理员权限移除。")
    except Exception as e:
        await session.rollback()
        print(f"操作失败：{e}")
        sys.exit(1)
    finally:
        await session.close()
        await close_db()


async def list_users() -> None:
    """List all users with their roles."""
    session = await _get_session()
    try:
        result = await session.execute(
            select(User).order_by(User.created_at)
        )
        users = result.scalars().all()
        if not users:
            print("数据库中暂无用户。")
            return

        print(f"{'用户名':<20} {'邮箱':<30} {'角色':<10} {'状态':<8}")
        print("-" * 70)
        for u in users:
            status = "活跃" if u.is_active else "禁用"
            print(f"{u.username:<20} {u.email:<30} {u.role:<10} {status:<8}")
    except Exception as e:
        print(f"操作失败：{e}")
        sys.exit(1)
    finally:
        await session.close()
        await close_db()


# 影子账号直接关联（含 user_id 列）的表
_SHADOW_DIRECT_TABLES = [
    TenantMembership,
    RefreshToken,
    UserProfile,
    PortraitVersion,
    UserConfig,
    Memory,
    Skill,
    Trace,
    CronJob,
    Workspace,
    SandboxSession,
    TokenUsageDaily,
    Delegation,
    UserPet,
    PetExpLog,
    ChannelBinding,
]


async def purge_shadow_users(dry_run: bool) -> None:
    """Delete all shadow accounts and their dependent data.

    Shadow accounts are never created by the channel pipeline anymore (see
    channels/binding.py); this command cleans up legacy rows. It removes the
    users themselves plus every row that references them (sessions, messages,
    memories, bindings, pets, ...). Runs in a single transaction.
    """
    session = await _get_session()
    try:
        shadow_ids = list(
            (await session.execute(select(User.id).where(User.is_shadow.is_(True)))).scalars()
        )
        if not shadow_ids:
            print("没有需要清理的影子账号。")
            return

        session_ids = list(
            (await session.execute(
                select(Session.id).where(Session.user_id.in_(shadow_ids))
            )).scalars()
        )
        print(f"找到 {len(shadow_ids)} 个影子账号，关联 {len(session_ids)} 个会话。")
        if dry_run:
            print("DRY-RUN 模式：仅统计，不执行删除。")
        print("-" * 50)

        async def _count(model, where_clause) -> int:
            return (await session.execute(
                select(func.count()).select_from(model).where(where_clause)
            )).scalar_one()

        async def _remove(model, where_clause, label: str) -> int:
            if dry_run:
                n = await _count(model, where_clause)
            else:
                n = (await session.execute(delete(model).where(where_clause))).rowcount or 0
            print(f"  {label:<28} {'将删除' if dry_run else '已删除'} {n} 行")
            return n

        total = 0
        if session_ids:
            total += await _remove(
                Message,
                Message.session_id.in_(session_ids),
                "messages",
            )
            total += await _remove(
                ChannelSessionMapping,
                ChannelSessionMapping.session_id.in_(session_ids),
                "channel_session_mappings",
            )
            total += await _remove(Session, Session.id.in_(session_ids), "sessions")

        for model in _SHADOW_DIRECT_TABLES:
            total += await _remove(model, model.user_id.in_(shadow_ids), model.__tablename__)

        total += await _remove(User, User.id.in_(shadow_ids), "users")

        print("-" * 50)
        print(f"合计 {'将删除' if dry_run else '已删除'} {total} 行。")

        if not dry_run:
            await session.commit()
    except Exception as e:
        await session.rollback()
        print(f"操作失败：{e}")
        sys.exit(1)
    finally:
        await session.close()
        await close_db()


async def hook_list() -> None:
    """列出全部 Hook 配置。"""
    session = await _get_session()
    try:
        result = await session.execute(select(Hook).order_by(Hook.created_at))
        hooks = result.scalars().all()
        if not hooks:
            print("无 Hook 配置。")
            return
        print(f"{'ID':<36} {'名称':<24} {'事件':<14} {'作用域':<8} {'动作':<16} {'启用'}")
        print("-" * 110)
        for h in hooks:
            print(
                f"{h.id!s:<36} {h.name[:24]:<24} {h.event:<14} {h.scope:<8} "
                f"{h.action_type:<16} {'是' if h.is_enabled else '否'}"
            )
    except Exception as e:
        print(f"操作失败：{e}")
        sys.exit(1)
    finally:
        await session.close()
        await close_db()


async def hook_create(
    *,
    user: str,
    event: str,
    scope: str,
    action: str,
    url: str | None,
    command: str | None,
    name: str | None,
    agent_id: str | None,
    secret: str | None,
    sign: bool,
) -> None:
    """创建一个 Hook 配置（归属给定用户，global 仅限管理员）。"""
    session = await _get_session()
    try:
        owner = (
            await session.execute(select(User).where(User.username == user))
        ).scalar_one_or_none()
        if not owner:
            print(f"错误：用户 '{user}' 不存在。")
            sys.exit(1)
        if scope == "global" and owner.role != "admin":
            print("错误：仅管理员可创建 global 作用域 Hook。")
            sys.exit(1)

        if action == "webhook":
            if not url:
                print("错误：webhook 动作需提供 --url。")
                sys.exit(1)
            config = {"url": url, "headers": {}, "sign": sign}
            if secret:
                config["secret"] = secret
        elif action == "sandbox_command":
            if not command:
                print("错误：sandbox_command 动作需提供 --command。")
                sys.exit(1)
            config = {"command": command}
        else:
            print(f"错误：未知动作类型 '{action}'（webhook / sandbox_command）。")
            sys.exit(1)

        hook = Hook(
            tenant_id=None if scope == "global" else owner.tenant_id,
            created_by=owner.id,
            name=name or f"{event}-{action}",
            scope=scope,
            agent_id=UUID(agent_id) if agent_id else None,
            event=event,
            action_type=action,
            config=config,
        )
        session.add(hook)
        await session.commit()
        print(f"已创建 Hook：{hook.id}（{hook.name}，事件 {hook.event}，作用域 {hook.scope}）")
    except Exception as e:
        await session.rollback()
        print(f"操作失败：{e}")
        sys.exit(1)
    finally:
        await session.close()
        await close_db()


async def hook_delete(hook_id: str) -> None:
    """删除一个 Hook 配置。"""
    session = await _get_session()
    try:
        hook = (
            await session.execute(select(Hook).where(Hook.id == UUID(hook_id)))
        ).scalar_one_or_none()
        if not hook:
            print(f"错误：Hook 不存在：{hook_id}")
            sys.exit(1)
        await session.delete(hook)
        await session.commit()
        print(f"已删除 Hook：{hook_id}（{hook.name}）")
    except Exception as e:
        await session.rollback()
        print(f"操作失败：{e}")
        sys.exit(1)
    finally:
        await session.close()
        await close_db()


async def hook_test(hook_id: str) -> None:
    """用样例负载直接触发一次 Hook 动作，验证配置。"""
    session = await _get_session()
    try:
        hook = (
            await session.execute(select(Hook).where(Hook.id == UUID(hook_id)))
        ).scalar_one_or_none()
        if not hook:
            print(f"错误：Hook 不存在：{hook_id}")
            sys.exit(1)
        def_row = HookDef.from_row(hook)
        payload = {
            "event": hook.event,
            "event_id": str(uuid4()),
            "timestamp": datetime.now(UTC).isoformat(),
            "trace_id": None,
            "session_id": None,
            "user_id": str(hook.created_by),
            "tenant_id": str(hook.tenant_id) if hook.tenant_id else None,
            "agent_id": str(hook.agent_id) if hook.agent_id else None,
            "model": "test",
            "hook": {"id": str(hook.id), "name": hook.name, "scope": hook.scope},
            "data": {"test": True},
        }
        result = await dispatcher.execute(def_row, payload)
        print(f"状态: {result.status}")
        print(f"耗时: {result.duration_ms} ms")
        if result.http_status is not None:
            print(f"HTTP: {result.http_status}")
        if result.exit_code is not None:
            print(f"退出码: {result.exit_code}")
        if result.error:
            print(f"错误: {result.error}")
        if result.response_preview:
            print(f"响应: {result.response_preview[:200]}")
    except Exception as e:
        print(f"操作失败：{e}")
        sys.exit(1)
    finally:
        await session.close()
        await close_db()


def main():
    parser = argparse.ArgumentParser(
        prog="aio-manage",
        description="AIO Agent Platform 平台管理工具",
    )
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # set-admin
    sp = subparsers.add_parser("set-admin", help="将用户设置为管理员")
    sp.add_argument("username", help="用户名")

    # remove-admin
    sp = subparsers.add_parser("remove-admin", help="移除用户的管理员权限")
    sp.add_argument("username", help="用户名")

    # list-users
    subparsers.add_parser("list-users", help="列出所有用户及其角色")

    # purge-shadow-users
    sp = subparsers.add_parser("purge-shadow-users", help="删除所有影子账号及其关联数据")
    sp.add_argument("--dry-run", action="store_true", help="仅统计将删除的数据，不实际删除")

    # hook
    sp = subparsers.add_parser("hook", help="Hook 配置管理")
    hook_sub = sp.add_subparsers(dest="hook_command", help="可用子命令")

    hook_sub.add_parser("list", help="列出 Hook 配置")
    sp_create = hook_sub.add_parser("create", help="创建 Hook")
    sp_create.add_argument("--user", required=True, help="归属用户名（确定 created_by / tenant_id）")
    sp_create.add_argument("--event", required=True, help="事件名，如 PostToolUse")
    sp_create.add_argument("--scope", required=True, choices=["global", "tenant", "agent"], help="作用域")
    sp_create.add_argument("--action", required=True, choices=["webhook", "sandbox_command"], help="动作类型")
    sp_create.add_argument("--url", help="webhook URL")
    sp_create.add_argument("--command", help="sandbox_command 命令")
    sp_create.add_argument("--name", help="名称（默认 event-action）")
    sp_create.add_argument("--agent-id", help="scope=agent 时的目标智能体 ID")
    sp_create.add_argument("--secret", help="webhook 签名密钥")
    sp_create.add_argument("--sign", action="store_true", help="启用 HMAC 签名")
    sp_delete = hook_sub.add_parser("delete", help="删除 Hook")
    sp_delete.add_argument("hook_id", help="Hook ID")
    sp_test = hook_sub.add_parser("test", help="测试触发 Hook 动作")
    sp_test.add_argument("hook_id", help="Hook ID")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    match args.command:
        case "set-admin":
            asyncio.run(set_admin(args.username))
        case "remove-admin":
            asyncio.run(remove_admin(args.username))
        case "list-users":
            asyncio.run(list_users())
        case "purge-shadow-users":
            asyncio.run(purge_shadow_users(args.dry_run))
        case "hook":
            if not args.hook_command:
                sp.print_help()
                sys.exit(1)
            match args.hook_command:
                case "list":
                    asyncio.run(hook_list())
                case "create":
                    asyncio.run(hook_create(
                        user=args.user,
                        event=args.event,
                        scope=args.scope,
                        action=args.action,
                        url=args.url,
                        command=args.command,
                        name=args.name,
                        agent_id=args.agent_id,
                        secret=args.secret,
                        sign=args.sign,
                    ))
                case "delete":
                    asyncio.run(hook_delete(args.hook_id))
                case "test":
                    asyncio.run(hook_test(args.hook_id))


if __name__ == "__main__":
    main()
