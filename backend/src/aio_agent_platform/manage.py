"""Management CLI — administrative commands for the platform.

Usage:
    python -m aio_agent_platform.manage set-admin <username>
    python -m aio_agent_platform.manage list-users
    python -m aio_agent_platform.manage purge-shadow-users [--dry-run]
"""

import argparse
import asyncio
import sys

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.db.connection import close_db, get_session_factory
from aio_agent_platform.db.models import (
    ChannelBinding,
    ChannelSessionMapping,
    CronJob,
    Delegation,
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


if __name__ == "__main__":
    main()
