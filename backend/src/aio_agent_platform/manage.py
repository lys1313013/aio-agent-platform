"""Management CLI — administrative commands for the platform.

Usage:
    python -m aio_agent_platform.manage set-admin <username>
    python -m aio_agent_platform.manage list-users
"""

import argparse
import asyncio
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aio_agent_platform.db.connection import close_db, get_session_factory
from aio_agent_platform.db.models import User


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


if __name__ == "__main__":
    main()
