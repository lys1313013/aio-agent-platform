"""Fixed sandbox programs. Paths are data, traversed with no-follow directory FDs."""
from __future__ import annotations

import base64
import json
import shlex

from aio_agent_platform.skills.contracts import MAX_FILE_SIZE, SkillError, validate_path

# O_NOFOLLOW on every component prevents both directory and final-file symlinks.
READ_FILE_SCRIPT = r'''
import os, sys, json, stat, base64
p = json.loads(sys.argv[1])
fd = os.open('/workspace', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
try:
    for part in p['path'].split('/')[:-1]:
        child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
        os.close(fd)
        fd = child
    file_fd = os.open(p['path'].split('/')[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    with os.fdopen(file_fd, 'rb') as f:
        if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
            raise ValueError('not a regular file')
        data = f.read(p['limit'] + 1)
        if len(data) > p['limit']:
            raise ValueError('file too large')
        print(base64.b64encode(data).decode())
finally:
    os.close(fd)
'''

WRITE_PACKAGE_SCRIPT = r'''
import os, sys, json, base64, stat
p = json.loads(sys.argv[1])
for entry in p['files']:
    parts = (p['base'] + '/' + entry['path']).split('/')
    if any(x in ('', '.', '..') for x in parts):
        raise ValueError('invalid path')
    fd = os.open('/workspace', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            try:
                os.mkdir(part, dir_fd=fd)
            except FileExistsError:
                pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        file_fd = os.open(parts[-1], os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=fd)
        with os.fdopen(file_fd, 'wb') as f:
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                raise ValueError('not a regular file')
            offset = entry.get('offset', 0)
            if offset == 0:
                f.truncate(0)
            f.seek(offset)
            f.write(base64.b64decode(entry['data'], validate=True))
    finally:
        os.close(fd)
'''


async def read_workspace_file(manager, user_id, session_id, workspace_id, slug, path):
    validate_path(slug, package=False)
    prefix = f"/workspace/{slug}/"
    if path.startswith(prefix):
        path = path[len(prefix):]
    validate_path(path, package=False)
    payload = {"path": f"{slug}/{path}", "limit": MAX_FILE_SIZE}
    sandbox = await manager.get_or_create(user_id, session_id, workspace_id, slug)
    result = await manager.execute(sandbox, "python3 -c " + shlex.quote(READ_FILE_SCRIPT) + " " + shlex.quote(json.dumps(payload)))
    if result.exit_code != 0:
        raise SkillError("file_unavailable", f"无法读取附件（不存在、越界、非普通文件或超过 1 MiB）：{path}")
    try:
        return base64.b64decode(result.stdout.strip(), validate=True)
    except ValueError as exc:
        raise SkillError("file_unavailable", f"无法解码工作区附件：{path}") from exc
